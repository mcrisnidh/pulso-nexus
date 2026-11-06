-- PULSO NEXUS E1-B: procedimientos almacenados.
-- Reglas comunes: SET XACT_ABORT ON; aislamiento READ COMMITTED explícito (no se hereda el de la
-- sesión); toda hora procede de SYSUTCDATETIME(). Errores de negocio con THROW 500xx y prefijo NEXUS:.
--   50010 NEXUS:FENCED          token de fencing obsoleto o lease vencido
--   50011 NEXUS:CLAIM_INVALID   claim inexistente, ajeno o vencido
--   50020 NEXUS:CONFLICT        mismo identificador con contenido distinto
--   50030 NEXUS:ALREADY_SEALED  la solicitud ya tiene otra generación sellada (C3)
--   50031 NEXUS:NOT_SEALED      se intenta completar sin generación sellada
--   50040 NEXUS:INVALID         parámetro o transición inválidos
-- Los bloques "FENCE:BEGIN/END" delimitan la validación de fencing (los usan las pruebas de mutación).

CREATE OR ALTER PROCEDURE nexus.usp_enqueue_request
    @idempotency_key nvarchar(200),
    @scope           nvarchar(128),
    @trigger_kind    nvarchar(16),
    @payload         nvarchar(4000),
    @max_attempts    int,
    @actor           nvarchar(128)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    IF @max_attempts IS NULL OR @max_attempts < 1 OR @idempotency_key IS NULL OR @scope IS NULL
    BEGIN
        THROW 50040, N'NEXUS:INVALID parámetros de encolado', 1;
    END;
    DECLARE @id bigint;
    DECLARE @created bit = 0;
    BEGIN TRANSACTION;
    SELECT @id = request_id
    FROM nexus.run_request WITH (UPDLOCK, HOLDLOCK)
    WHERE idempotency_key = @idempotency_key;
    IF @id IS NULL
    BEGIN
        INSERT INTO nexus.run_request (idempotency_key, scope, trigger_kind, payload, max_attempts)
        VALUES (@idempotency_key, @scope, @trigger_kind, @payload, @max_attempts);
        SET @id = CAST(SCOPE_IDENTITY() AS bigint);
        SET @created = 1;
        INSERT INTO nexus.run_event (kind, request_id, actor) VALUES (N'ENQUEUED', @id, @actor);
    END;
    COMMIT TRANSACTION;
    SELECT @id AS request_id, @created AS created;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_claim_request
    @worker   nvarchar(128),
    @claim_ms int
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    DECLARE @now datetime2(7) = SYSUTCDATETIME();
    DECLARE @token uniqueidentifier = NEWID();
    DECLARE @claimed TABLE (
        request_id bigint, claim_token uniqueidentifier, scope nvarchar(128),
        payload nvarchar(4000), attempts int, max_attempts int, claim_expires_at datetime2(7));
    BEGIN TRANSACTION;
    WITH next_request AS (
        SELECT TOP (1)
            request_id, state, claim_token, claimed_by, claim_expires_at,
            scope, payload, attempts, max_attempts
        FROM nexus.run_request WITH (
            -- CLAIM_HINTS
            UPDLOCK, READPAST, ROWLOCK
        )
        WHERE state = N'PENDING' AND not_before <= @now
        ORDER BY not_before, request_id
    )
    UPDATE next_request
    SET state = N'CLAIMED',
        claim_token = @token,
        claimed_by = @worker,
        claim_expires_at = DATEADD(millisecond, @claim_ms, SYSUTCDATETIME())
    OUTPUT inserted.request_id, inserted.claim_token, inserted.scope, inserted.payload,
           inserted.attempts, inserted.max_attempts, inserted.claim_expires_at
    INTO @claimed (request_id, claim_token, scope, payload, attempts, max_attempts, claim_expires_at);
    INSERT INTO nexus.run_event (kind, request_id, claim_token, actor)
    SELECT N'CLAIMED', request_id, claim_token, @worker FROM @claimed;
    COMMIT TRANSACTION;
    SELECT request_id, claim_token, scope, payload, attempts, max_attempts, claim_expires_at,
           SYSUTCDATETIME() AS server_ts
    FROM @claimed;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_release_claim
    @request_id  bigint,
    @claim_token uniqueidentifier,
    @backoff_ms  int,
    @actor       nvarchar(128)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    DECLARE @released int;
    BEGIN TRANSACTION;
    UPDATE nexus.run_request
    SET state = N'PENDING', claim_token = NULL, claimed_by = NULL, claim_expires_at = NULL,
        not_before = DATEADD(millisecond, @backoff_ms, SYSUTCDATETIME())
    WHERE request_id = @request_id AND state = N'CLAIMED' AND claim_token = @claim_token;
    SET @released = @@ROWCOUNT;
    IF @released = 1
        INSERT INTO nexus.run_event (kind, request_id, claim_token, actor)
        VALUES (N'CLAIM_RELEASED', @request_id, @claim_token, @actor);
    COMMIT TRANSACTION;
    SELECT @released AS released;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_acquire_lease
    @request_id  bigint,
    @claim_token uniqueidentifier,
    @worker      nvarchar(128),
    @lease_ms    int
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    DECLARE @scope nvarchar(128);
    DECLARE @attempts int;
    DECLARE @max int;
    DECLARE @sealed nvarchar(64);
    DECLARE @claim_until datetime2(7);
    DECLARE @lease TABLE (fencing_token bigint, expires_at datetime2(7));
    -- La fila de lease del ámbito se crea FUERA de la transacción principal e insertando
    -- directamente (duplicado = ya existe). Un IF NOT EXISTS ... WITH (UPDLOCK, HOLDLOCK) sobre una
    -- clave nueva toma bloqueos de rango que, con ámbitos contiguos y 16 workers, producían
    -- interbloqueos (detectados tras segundos) y leases emitidos ya vencidos (defecto hallado en CI).
    DECLARE @lease_scope nvarchar(128) = (SELECT scope FROM nexus.run_request WHERE request_id = @request_id);
    IF @lease_scope IS NOT NULL AND NOT EXISTS (SELECT 1 FROM nexus.scope_lease WHERE scope = @lease_scope)
    BEGIN
        BEGIN TRY
            INSERT INTO nexus.scope_lease (scope) VALUES (@lease_scope);
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() NOT IN (2601, 2627) THROW;
        END CATCH;
    END;
    BEGIN TRANSACTION;
    -- NX-E1B-R1-01: la vigencia del claim NUNCA se compara con una hora tomada antes de esperar
    -- bloqueos. Se lee claim_expires_at bajo UPDLOCK y se compara con SYSUTCDATETIME() en una
    -- sentencia posterior (evaluada tras obtener el bloqueo); se vuelve a comprobar tras el
    -- último punto de espera (UPDATE de la fila de lease), justo antes de conceder.
    SELECT @scope = scope, @attempts = attempts, @max = max_attempts, @claim_until = claim_expires_at
    FROM nexus.run_request WITH (UPDLOCK, HOLDLOCK)
    WHERE request_id = @request_id AND state = N'CLAIMED' AND claim_token = @claim_token;
    IF @scope IS NULL OR @claim_until IS NULL OR @claim_until <= SYSUTCDATETIME()
    BEGIN
        THROW 50011, N'NEXUS:CLAIM_INVALID claim inexistente, ajeno o vencido', 1;
    END;
    SELECT @sealed = attempt_id FROM nexus.generation
    WHERE request_id = @request_id AND sealed_at IS NOT NULL;
    IF @attempts >= @max AND @sealed IS NULL
    BEGIN
        UPDATE nexus.run_request
        SET state = N'FAILED_PERMANENT', claim_token = NULL, claimed_by = NULL, claim_expires_at = NULL
        WHERE request_id = @request_id;
        INSERT INTO nexus.run_event (kind, request_id, actor, detail)
        VALUES (N'FAILED_PERMANENT', @request_id, @worker, N'MAX_ATTEMPTS');
        COMMIT TRANSACTION;
        SELECT CAST(0 AS bit) AS acquired, N'MAX_ATTEMPTS' AS reason, CAST(NULL AS nvarchar(64)) AS attempt_id,
               CAST(NULL AS bigint) AS fencing_token, CAST(NULL AS int) AS attempt_no,
               CAST(NULL AS datetime2(7)) AS expires_at, CAST(NULL AS nvarchar(64)) AS sealed_attempt_id,
               SYSUTCDATETIME() AS server_ts;
        RETURN;
    END;
    DECLARE @attempt_id nvarchar(64) =
        N'att-' + LOWER(REPLACE(CONVERT(nvarchar(36), NEWID()), N'-', N''));
    -- Solo se emite un lease libre: los vencidos los libera usp_recover_expired (que invalida el token).
    UPDATE nexus.scope_lease
    SET holder = @worker, attempt_id = @attempt_id, fencing_token = fencing_token + 1,
        expires_at = DATEADD(millisecond, @lease_ms, SYSUTCDATETIME())
    OUTPUT inserted.fencing_token, inserted.expires_at INTO @lease (fencing_token, expires_at)
    WHERE scope = @scope AND holder IS NULL;
    IF NOT EXISTS (SELECT 1 FROM @lease)
    BEGIN
        COMMIT TRANSACTION;
        SELECT CAST(0 AS bit) AS acquired, N'LEASE_BUSY' AS reason, CAST(NULL AS nvarchar(64)) AS attempt_id,
               CAST(NULL AS bigint) AS fencing_token, CAST(NULL AS int) AS attempt_no,
               CAST(NULL AS datetime2(7)) AS expires_at, CAST(NULL AS nvarchar(64)) AS sealed_attempt_id,
               SYSUTCDATETIME() AS server_ts;
        RETURN;
    END;
    -- Segunda comprobación tras el último punto de espera: si el claim venció mientras se
    -- esperaba la fila de lease, THROW revierte TODO (incluido el UPDATE del lease).
    IF @claim_until <= SYSUTCDATETIME()
    BEGIN
        THROW 50011, N'NEXUS:CLAIM_INVALID claim vencido durante la adquisición', 1;
    END;
    DECLARE @token bigint = (SELECT fencing_token FROM @lease);
    UPDATE nexus.run_request
    SET state = N'RUNNING', attempts = attempts + 1, claim_expires_at = NULL
    WHERE request_id = @request_id;
    INSERT INTO nexus.attempt (attempt_id, request_id, attempt_no, scope, worker, fencing_token, state)
    VALUES (@attempt_id, @request_id, @attempts + 1, @scope, @worker, @token, N'RUNNING');
    INSERT INTO nexus.generation (attempt_id, request_id, state) VALUES (@attempt_id, @request_id, N'OPEN');
    INSERT INTO nexus.run_event (kind, request_id, attempt_id, claim_token, fencing_token, actor)
    VALUES (N'LEASE_ACQUIRED', @request_id, @attempt_id, @claim_token, @token, @worker);
    COMMIT TRANSACTION;
    SELECT CAST(1 AS bit) AS acquired, CAST(NULL AS nvarchar(32)) AS reason, @attempt_id AS attempt_id,
           @token AS fencing_token, @attempts + 1 AS attempt_no,
           (SELECT expires_at FROM @lease) AS expires_at, @sealed AS sealed_attempt_id,
           SYSUTCDATETIME() AS server_ts;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_heartbeat
    @attempt_id    nvarchar(64),
    @fencing_token bigint,
    @lease_ms      int
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    BEGIN TRANSACTION;
    -- FENCE:BEGIN
    -- Búsqueda por clave primaria en dos pasos (sin JOIN): el bloqueo UPDLOCK/HOLDLOCK cae SOLO
    -- sobre la fila del ámbito del intento, nunca sobre filas de otros workers.
    DECLARE @fence_scope nvarchar(128) = (
        SELECT scope FROM nexus.attempt
        WHERE attempt_id = @attempt_id AND state = N'RUNNING' AND fencing_token = @fencing_token);
    IF @fence_scope IS NULL OR NOT EXISTS (
        SELECT 1 FROM nexus.scope_lease WITH (UPDLOCK, HOLDLOCK)
        WHERE scope = @fence_scope AND attempt_id = @attempt_id
          AND fencing_token = @fencing_token AND expires_at > SYSUTCDATETIME())
    BEGIN
        THROW 50010, N'NEXUS:FENCED token obsoleto o lease vencido', 1;
    END;
    -- FENCE:END
    -- Por clave primaria (scope): un UPDATE por attempt_id recorría pk_scope_lease tomando U sobre
    -- filas de otros workers -> interbloqueos complete/complete (grafos de system_health en CI).
    DECLARE @lease_key nvarchar(128) = (SELECT scope FROM nexus.attempt WHERE attempt_id = @attempt_id);
    UPDATE nexus.scope_lease
    SET expires_at = DATEADD(millisecond, @lease_ms, SYSUTCDATETIME())
    WHERE scope = @lease_key AND attempt_id = @attempt_id AND fencing_token = @fencing_token;
    COMMIT TRANSACTION;
    SELECT expires_at, SYSUTCDATETIME() AS server_ts FROM nexus.scope_lease
    WHERE scope = @lease_key AND attempt_id = @attempt_id;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_register_snapshot_ref
    @attempt_id     nvarchar(64),
    @fencing_token  bigint,
    @snapshot_id    nvarchar(64),
    @content_sha256 char(64)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    DECLARE @existing char(64);
    DECLARE @created bit = 0;
    BEGIN TRANSACTION;
    -- FENCE:BEGIN
    -- Búsqueda por clave primaria en dos pasos (sin JOIN): el bloqueo UPDLOCK/HOLDLOCK cae SOLO
    -- sobre la fila del ámbito del intento, nunca sobre filas de otros workers.
    DECLARE @fence_scope nvarchar(128) = (
        SELECT scope FROM nexus.attempt
        WHERE attempt_id = @attempt_id AND state = N'RUNNING' AND fencing_token = @fencing_token);
    IF @fence_scope IS NULL OR NOT EXISTS (
        SELECT 1 FROM nexus.scope_lease WITH (UPDLOCK, HOLDLOCK)
        WHERE scope = @fence_scope AND attempt_id = @attempt_id
          AND fencing_token = @fencing_token AND expires_at > SYSUTCDATETIME())
    BEGIN
        THROW 50010, N'NEXUS:FENCED token obsoleto o lease vencido', 1;
    END;
    -- FENCE:END
    SELECT @existing = content_sha256 FROM nexus.snapshot_ref WITH (UPDLOCK, HOLDLOCK)
    WHERE snapshot_id = @snapshot_id;
    IF @existing IS NULL
    BEGIN
        INSERT INTO nexus.snapshot_ref (snapshot_id, content_sha256) VALUES (@snapshot_id, @content_sha256);
        SET @created = 1;
    END
    ELSE IF @existing <> @content_sha256
    BEGIN
        THROW 50020, N'NEXUS:CONFLICT snapshot registrado con otro hash', 1;
    END;
    COMMIT TRANSACTION;
    SELECT @created AS created;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_register_artifact
    @attempt_id     nvarchar(64),
    @fencing_token  bigint,
    @contract       nvarchar(128),
    @object_key     nvarchar(400),
    @content_sha256 char(64),
    @logical_hash   char(64),
    @size_bytes     bigint
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    DECLARE @created bit = 0;
    BEGIN TRANSACTION;
    -- FENCE:BEGIN
    -- Búsqueda por clave primaria en dos pasos (sin JOIN): el bloqueo UPDLOCK/HOLDLOCK cae SOLO
    -- sobre la fila del ámbito del intento, nunca sobre filas de otros workers.
    DECLARE @fence_scope nvarchar(128) = (
        SELECT scope FROM nexus.attempt
        WHERE attempt_id = @attempt_id AND state = N'RUNNING' AND fencing_token = @fencing_token);
    IF @fence_scope IS NULL OR NOT EXISTS (
        SELECT 1 FROM nexus.scope_lease WITH (UPDLOCK, HOLDLOCK)
        WHERE scope = @fence_scope AND attempt_id = @attempt_id
          AND fencing_token = @fencing_token AND expires_at > SYSUTCDATETIME())
    BEGIN
        THROW 50010, N'NEXUS:FENCED token obsoleto o lease vencido', 1;
    END;
    -- FENCE:END
    IF NOT EXISTS (SELECT 1 FROM nexus.generation WHERE attempt_id = @attempt_id AND state = N'OPEN')
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM nexus.artifact
            WHERE attempt_id = @attempt_id AND contract = @contract AND object_key = @object_key
              AND content_sha256 = @content_sha256 AND logical_hash = @logical_hash
              AND size_bytes = @size_bytes)
        BEGIN
            THROW 50040, N'NEXUS:INVALID la generación no admite nuevos artefactos', 1;
        END;
    END;
    IF EXISTS (SELECT 1 FROM nexus.artifact WITH (UPDLOCK)
               WHERE attempt_id = @attempt_id AND contract = @contract)
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM nexus.artifact
            WHERE attempt_id = @attempt_id AND contract = @contract AND object_key = @object_key
              AND content_sha256 = @content_sha256 AND logical_hash = @logical_hash
              AND size_bytes = @size_bytes)
        BEGIN
            THROW 50020, N'NEXUS:CONFLICT artefacto registrado con otro contenido', 1;
        END;
    END
    ELSE
    BEGIN
        INSERT INTO nexus.artifact (attempt_id, contract, object_key, content_sha256, logical_hash, size_bytes)
        VALUES (@attempt_id, @contract, @object_key, @content_sha256, @logical_hash, @size_bytes);
        SET @created = 1;
    END;
    COMMIT TRANSACTION;
    SELECT @created AS created;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_append_run_event
    @attempt_id    nvarchar(64),
    @fencing_token bigint,
    @kind          nvarchar(40),
    @detail        nvarchar(400)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    BEGIN TRANSACTION;
    -- FENCE:BEGIN
    -- Búsqueda por clave primaria en dos pasos (sin JOIN): el bloqueo UPDLOCK/HOLDLOCK cae SOLO
    -- sobre la fila del ámbito del intento, nunca sobre filas de otros workers.
    DECLARE @fence_scope nvarchar(128) = (
        SELECT scope FROM nexus.attempt
        WHERE attempt_id = @attempt_id AND state = N'RUNNING' AND fencing_token = @fencing_token);
    IF @fence_scope IS NULL OR NOT EXISTS (
        SELECT 1 FROM nexus.scope_lease WITH (UPDLOCK, HOLDLOCK)
        WHERE scope = @fence_scope AND attempt_id = @attempt_id
          AND fencing_token = @fencing_token AND expires_at > SYSUTCDATETIME())
    BEGIN
        THROW 50010, N'NEXUS:FENCED token obsoleto o lease vencido', 1;
    END;
    -- FENCE:END
    INSERT INTO nexus.run_event (kind, request_id, attempt_id, fencing_token, actor, detail)
    SELECT N'WORKER:' + @kind, request_id, @attempt_id, @fencing_token, worker, @detail
    FROM nexus.attempt WHERE attempt_id = @attempt_id;
    COMMIT TRANSACTION;
    SELECT CAST(1 AS bit) AS appended;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_seal_generation
    @attempt_id    nvarchar(64),
    @fencing_token bigint,
    @store_run_id  nvarchar(64),
    @input_digest  char(64),
    @result_digest char(64)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    DECLARE @request_id bigint;
    DECLARE @state nvarchar(16);
    BEGIN TRANSACTION;
    -- Idempotencia: un reintento tras un commit ambiguo de este mismo sellado no hace nada.
    IF EXISTS (SELECT 1 FROM nexus.generation
               WHERE attempt_id = @attempt_id AND state = N'SEALED' AND store_run_id = @store_run_id
                 AND input_digest = @input_digest AND result_digest = @result_digest)
    BEGIN
        COMMIT TRANSACTION;
        SELECT CAST(0 AS bit) AS sealed_now;
        RETURN;
    END;
    -- FENCE:BEGIN
    -- Búsqueda por clave primaria en dos pasos (sin JOIN): el bloqueo UPDLOCK/HOLDLOCK cae SOLO
    -- sobre la fila del ámbito del intento, nunca sobre filas de otros workers.
    DECLARE @fence_scope nvarchar(128) = (
        SELECT scope FROM nexus.attempt
        WHERE attempt_id = @attempt_id AND state = N'RUNNING' AND fencing_token = @fencing_token);
    IF @fence_scope IS NULL OR NOT EXISTS (
        SELECT 1 FROM nexus.scope_lease WITH (UPDLOCK, HOLDLOCK)
        WHERE scope = @fence_scope AND attempt_id = @attempt_id
          AND fencing_token = @fencing_token AND expires_at > SYSUTCDATETIME())
    BEGIN
        THROW 50010, N'NEXUS:FENCED token obsoleto o lease vencido', 1;
    END;
    -- FENCE:END
    SELECT @request_id = request_id, @state = state
    FROM nexus.generation WITH (UPDLOCK) WHERE attempt_id = @attempt_id;  -- UPDLOCK se mantiene hasta el fin de la transacción
    IF @state <> N'OPEN'
    BEGIN
        THROW 50040, N'NEXUS:INVALID la generación no está abierta', 1;
    END;
    IF NOT EXISTS (SELECT 1 FROM nexus.artifact WHERE attempt_id = @attempt_id)
    BEGIN
        THROW 50040, N'NEXUS:INVALID no se sella una generación sin artefactos', 1;
    END;
    IF EXISTS (SELECT 1 FROM nexus.generation
               WHERE request_id = @request_id AND sealed_at IS NOT NULL)
    BEGIN
        THROW 50030, N'NEXUS:ALREADY_SEALED la solicitud ya tiene una generación sellada', 1;
    END;
    UPDATE nexus.generation
    SET state = N'SEALED', store_run_id = @store_run_id, input_digest = @input_digest,
        result_digest = @result_digest, sealed_at = SYSUTCDATETIME()
    WHERE attempt_id = @attempt_id;
    INSERT INTO nexus.run_event (kind, request_id, attempt_id, fencing_token, detail)
    VALUES (N'SEALED', @request_id, @attempt_id, @fencing_token, @store_run_id);
    COMMIT TRANSACTION;
    SELECT CAST(1 AS bit) AS sealed_now;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_complete_run
    @attempt_id    nvarchar(64),
    @fencing_token bigint
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    DECLARE @request_id bigint;
    DECLARE @sealed nvarchar(64);
    BEGIN TRANSACTION;
    -- Idempotencia tras commit ambiguo: el intento ya completó la solicitud.
    IF EXISTS (SELECT 1 FROM nexus.attempt AS a JOIN nexus.run_request AS r ON r.request_id = a.request_id
               WHERE a.attempt_id = @attempt_id AND a.state = N'COMPLETED' AND r.state = N'COMPLETED'
                 AND a.fencing_token = @fencing_token)
    BEGIN
        COMMIT TRANSACTION;
        SELECT CAST(0 AS bit) AS completed_now, (SELECT completed_attempt_id FROM nexus.run_request AS r
               JOIN nexus.attempt AS a ON a.request_id = r.request_id WHERE a.attempt_id = @attempt_id)
               AS completed_attempt_id;
        RETURN;
    END;
    -- FENCE:BEGIN
    -- Búsqueda por clave primaria en dos pasos (sin JOIN): el bloqueo UPDLOCK/HOLDLOCK cae SOLO
    -- sobre la fila del ámbito del intento, nunca sobre filas de otros workers.
    DECLARE @fence_scope nvarchar(128) = (
        SELECT scope FROM nexus.attempt
        WHERE attempt_id = @attempt_id AND state = N'RUNNING' AND fencing_token = @fencing_token);
    IF @fence_scope IS NULL OR NOT EXISTS (
        SELECT 1 FROM nexus.scope_lease WITH (UPDLOCK, HOLDLOCK)
        WHERE scope = @fence_scope AND attempt_id = @attempt_id
          AND fencing_token = @fencing_token AND expires_at > SYSUTCDATETIME())
    BEGIN
        THROW 50010, N'NEXUS:FENCED token obsoleto o lease vencido', 1;
    END;
    -- FENCE:END
    SELECT @request_id = request_id FROM nexus.attempt WHERE attempt_id = @attempt_id;
    SELECT @sealed = attempt_id FROM nexus.generation
    WHERE request_id = @request_id AND sealed_at IS NOT NULL;
    IF @sealed IS NULL
    BEGIN
        THROW 50031, N'NEXUS:NOT_SEALED no hay generación sellada para la solicitud', 1;
    END;
    UPDATE nexus.attempt SET state = N'COMPLETED', finished_at = SYSUTCDATETIME()
    WHERE attempt_id = @attempt_id;
    UPDATE nexus.generation SET state = N'ABANDONED'
    WHERE attempt_id = @attempt_id AND state = N'OPEN';
    UPDATE nexus.run_request
    SET state = N'COMPLETED', completed_attempt_id = @sealed
    WHERE request_id = @request_id AND state = N'RUNNING';
    -- Por clave primaria (scope): un UPDATE por attempt_id recorría pk_scope_lease tomando U sobre
    -- filas de otros workers -> interbloqueos complete/complete (grafos de system_health en CI).
    DECLARE @lease_key nvarchar(128) = (SELECT scope FROM nexus.attempt WHERE attempt_id = @attempt_id);
    UPDATE nexus.scope_lease SET holder = NULL, attempt_id = NULL, expires_at = NULL
    WHERE scope = @lease_key AND attempt_id = @attempt_id AND fencing_token = @fencing_token;
    INSERT INTO nexus.run_event (kind, request_id, attempt_id, fencing_token, detail)
    VALUES (N'COMPLETED', @request_id, @attempt_id, @fencing_token, @sealed);
    COMMIT TRANSACTION;
    SELECT CAST(1 AS bit) AS completed_now, @sealed AS completed_attempt_id;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_fail_run
    @attempt_id    nvarchar(64),
    @fencing_token bigint,
    @reason        nvarchar(400),
    @backoff_ms    int
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    DECLARE @request_id bigint;
    BEGIN TRANSACTION;
    -- FENCE:BEGIN
    -- Búsqueda por clave primaria en dos pasos (sin JOIN): el bloqueo UPDLOCK/HOLDLOCK cae SOLO
    -- sobre la fila del ámbito del intento, nunca sobre filas de otros workers.
    DECLARE @fence_scope nvarchar(128) = (
        SELECT scope FROM nexus.attempt
        WHERE attempt_id = @attempt_id AND state = N'RUNNING' AND fencing_token = @fencing_token);
    IF @fence_scope IS NULL OR NOT EXISTS (
        SELECT 1 FROM nexus.scope_lease WITH (UPDLOCK, HOLDLOCK)
        WHERE scope = @fence_scope AND attempt_id = @attempt_id
          AND fencing_token = @fencing_token AND expires_at > SYSUTCDATETIME())
    BEGIN
        THROW 50010, N'NEXUS:FENCED token obsoleto o lease vencido', 1;
    END;
    -- FENCE:END
    SELECT @request_id = request_id FROM nexus.attempt WHERE attempt_id = @attempt_id;
    UPDATE nexus.attempt SET state = N'FAILED', failure = @reason, finished_at = SYSUTCDATETIME()
    WHERE attempt_id = @attempt_id;
    UPDATE nexus.generation SET state = N'ABANDONED' WHERE attempt_id = @attempt_id AND state = N'OPEN';
    UPDATE nexus.run_request
    SET state = CASE WHEN attempts >= max_attempts
                      AND NOT EXISTS (SELECT 1 FROM nexus.generation AS g
                                      WHERE g.request_id = @request_id AND g.sealed_at IS NOT NULL)
                     THEN N'FAILED_PERMANENT' ELSE N'PENDING' END,
        claim_token = NULL, claimed_by = NULL, claim_expires_at = NULL,
        not_before = DATEADD(millisecond, @backoff_ms, SYSUTCDATETIME())
    WHERE request_id = @request_id AND state = N'RUNNING';
    -- Por clave primaria (scope): un UPDATE por attempt_id recorría pk_scope_lease tomando U sobre
    -- filas de otros workers -> interbloqueos complete/complete (grafos de system_health en CI).
    DECLARE @lease_key nvarchar(128) = (SELECT scope FROM nexus.attempt WHERE attempt_id = @attempt_id);
    UPDATE nexus.scope_lease SET holder = NULL, attempt_id = NULL, expires_at = NULL
    WHERE scope = @lease_key AND attempt_id = @attempt_id AND fencing_token = @fencing_token;
    INSERT INTO nexus.run_event (kind, request_id, attempt_id, fencing_token, detail)
    VALUES (N'FAILED', @request_id, @attempt_id, @fencing_token, @reason);
    COMMIT TRANSACTION;
    SELECT CAST(1 AS bit) AS failed;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_recover_expired
    @backoff_ms int,
    @actor      nvarchar(128)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    DECLARE @now datetime2(7) = SYSUTCDATETIME();
    DECLARE @expired TABLE (scope nvarchar(128), attempt_id nvarchar(64), fencing_token bigint);
    DECLARE @claims TABLE (request_id bigint, claim_token uniqueidentifier);
    BEGIN TRANSACTION;
    -- 1) Leases vencidos: se libera el ámbito INCREMENTANDO el token (el zombi queda fenced).
    UPDATE l
    SET holder = NULL, attempt_id = NULL, expires_at = NULL, fencing_token = l.fencing_token + 1
    OUTPUT deleted.scope, deleted.attempt_id, inserted.fencing_token INTO @expired (scope, attempt_id, fencing_token)
    FROM nexus.scope_lease AS l WITH (UPDLOCK)
    WHERE l.holder IS NOT NULL AND l.expires_at < @now;
    UPDATE a SET state = N'FAILED', failure = N'LEASE_EXPIRED', finished_at = @now
    FROM nexus.attempt AS a JOIN @expired AS e ON e.attempt_id = a.attempt_id
    WHERE a.state = N'RUNNING';
    UPDATE g SET state = N'ABANDONED'
    FROM nexus.generation AS g JOIN @expired AS e ON e.attempt_id = g.attempt_id
    WHERE g.state = N'OPEN';
    UPDATE r
    SET state = CASE WHEN r.attempts >= r.max_attempts
                      AND NOT EXISTS (SELECT 1 FROM nexus.generation AS g2
                                      WHERE g2.request_id = r.request_id AND g2.sealed_at IS NOT NULL)
                     THEN N'FAILED_PERMANENT' ELSE N'PENDING' END,
        claim_token = NULL, claimed_by = NULL, claim_expires_at = NULL,
        not_before = DATEADD(millisecond, @backoff_ms, @now)
    FROM nexus.run_request AS r
    JOIN nexus.attempt AS a ON a.request_id = r.request_id
    JOIN @expired AS e ON e.attempt_id = a.attempt_id
    WHERE r.state = N'RUNNING';
    INSERT INTO nexus.run_event (kind, request_id, attempt_id, fencing_token, actor)
    SELECT N'LEASE_RECOVERED', a.request_id, e.attempt_id, e.fencing_token, @actor
    FROM @expired AS e LEFT JOIN nexus.attempt AS a ON a.attempt_id = e.attempt_id;
    -- 2) Claims vencidos sin lease: vuelven a PENDING sin consumir intento.
    UPDATE r
    SET state = N'PENDING', claim_token = NULL, claimed_by = NULL, claim_expires_at = NULL,
        not_before = DATEADD(millisecond, @backoff_ms, @now)
    OUTPUT inserted.request_id, deleted.claim_token INTO @claims (request_id, claim_token)
    FROM nexus.run_request AS r WITH (UPDLOCK)
    WHERE r.state = N'CLAIMED' AND r.claim_expires_at < @now;
    INSERT INTO nexus.run_event (kind, request_id, claim_token, actor)
    SELECT N'CLAIM_RECOVERED', request_id, claim_token, @actor FROM @claims;
    COMMIT TRANSACTION;
    SELECT (SELECT COUNT(*) FROM @expired) AS leases_recovered, (SELECT COUNT(*) FROM @claims) AS claims_recovered,
           @now AS server_ts;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_mark_artifact_state
    @attempt_id nvarchar(64),
    @contract   nvarchar(128),
    @new_state  nvarchar(16),
    @detail     nvarchar(400),
    @actor      nvarchar(128)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    DECLARE @current nvarchar(16);
    DECLARE @request_id bigint;
    IF @new_state NOT IN (N'MISSING', N'CORRUPT')
    BEGIN
        THROW 50040, N'NEXUS:INVALID solo se admite MISSING o CORRUPT', 1;
    END;
    BEGIN TRANSACTION;
    SELECT @current = state FROM nexus.artifact WITH (UPDLOCK, HOLDLOCK)
    WHERE attempt_id = @attempt_id AND contract = @contract;
    IF @current IS NULL
    BEGIN
        THROW 50040, N'NEXUS:INVALID artefacto inexistente', 1;
    END;
    IF @current = @new_state
    BEGIN
        COMMIT TRANSACTION;
        SELECT CAST(0 AS bit) AS changed;
        RETURN;
    END;
    IF @current <> N'PRESENT'
    BEGIN
        THROW 50040, N'NEXUS:INVALID transición de estado no permitida', 1;
    END;
    UPDATE nexus.artifact SET state = @new_state WHERE attempt_id = @attempt_id AND contract = @contract;
    UPDATE nexus.generation SET state = N'INELIGIBLE' WHERE attempt_id = @attempt_id AND state = N'SEALED';
    SELECT @request_id = request_id FROM nexus.generation WHERE attempt_id = @attempt_id;
    INSERT INTO nexus.run_event (kind, request_id, attempt_id, actor, detail)
    VALUES (N'RECONCILE_' + @new_state, @request_id, @attempt_id, @actor, LEFT(@contract + N' ' + @detail, 400));
    COMMIT TRANSACTION;
    SELECT CAST(1 AS bit) AS changed;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_list_artifacts
AS
BEGIN
    SET NOCOUNT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    SELECT a.attempt_id, a.contract, a.object_key, a.content_sha256, a.state AS artifact_state,
           g.state AS generation_state, g.store_run_id, g.request_id
    FROM nexus.artifact AS a JOIN nexus.generation AS g ON g.attempt_id = a.attempt_id
    ORDER BY a.attempt_id, a.contract;
END;
GO

-- NX-E1B-R1-02: listados de solo lectura para la reconciliación completa (procedencia).
CREATE OR ALTER PROCEDURE nexus.usp_list_snapshot_refs
AS
BEGIN
    SET NOCOUNT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    SELECT snapshot_id, content_sha256, registered_at FROM nexus.snapshot_ref ORDER BY snapshot_id;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_list_generations
AS
BEGIN
    SET NOCOUNT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    SELECT attempt_id, request_id, state AS generation_state, store_run_id, input_digest,
           result_digest, sealed_at
    FROM nexus.generation ORDER BY attempt_id;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_get_request
    @idempotency_key nvarchar(200)
AS
BEGIN
    SET NOCOUNT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    SELECT request_id, state, attempts, max_attempts, completed_attempt_id, claim_token
    FROM nexus.run_request WHERE idempotency_key = @idempotency_key;
END;
GO

CREATE OR ALTER PROCEDURE nexus.usp_get_environment
AS
BEGIN
    SET NOCOUNT ON;
    SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
    SELECT e.profile, e.kind, e.nonce,
           (SELECT MAX(version) FROM nexus.schema_version) AS schema_version,
           SYSUTCDATETIME() AS server_ts
    FROM nexus.environment AS e WHERE e.singleton = 1;
END;
GO
