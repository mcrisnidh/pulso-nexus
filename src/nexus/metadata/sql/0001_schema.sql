-- PULSO NEXUS E1-B: esquema de metadatos (solo metadatos; los datos tabulares viven en el ObjectStore).
-- Primitivas compatibles con SQL Server 2016 SP1+. Aplicado por nexus_migrator.

CREATE TABLE nexus.environment (
    singleton  tinyint      NOT NULL CONSTRAINT pk_environment PRIMARY KEY
                            CONSTRAINT ck_environment_singleton CHECK (singleton = 1),
    profile    nvarchar(64) NOT NULL,
    kind       nvarchar(32) NOT NULL,
    nonce      nvarchar(64) NOT NULL,
    created_at datetime2(7) NOT NULL CONSTRAINT df_environment_created DEFAULT SYSUTCDATETIME()
);
GO

CREATE TABLE nexus.run_request (
    request_id           bigint IDENTITY(1, 1) NOT NULL CONSTRAINT pk_run_request PRIMARY KEY,
    idempotency_key      nvarchar(200)  NOT NULL CONSTRAINT uq_run_request_key UNIQUE,
    scope                nvarchar(128)  NOT NULL,
    trigger_kind         nvarchar(16)   NOT NULL
                         CONSTRAINT ck_run_request_trigger CHECK (trigger_kind IN (N'manual', N'scheduled')),
    payload              nvarchar(4000) NOT NULL,
    state                nvarchar(24)   NOT NULL CONSTRAINT df_run_request_state DEFAULT N'PENDING'
                         CONSTRAINT ck_run_request_state CHECK (
                             state IN (N'PENDING', N'CLAIMED', N'RUNNING', N'COMPLETED', N'FAILED_PERMANENT')),
    attempts             int            NOT NULL CONSTRAINT df_run_request_attempts DEFAULT 0,
    max_attempts         int            NOT NULL CONSTRAINT ck_run_request_max CHECK (max_attempts >= 1),
    not_before           datetime2(7)   NOT NULL CONSTRAINT df_run_request_nb DEFAULT SYSUTCDATETIME(),
    claim_token          uniqueidentifier NULL,
    claimed_by           nvarchar(128)  NULL,
    claim_expires_at     datetime2(7)   NULL,
    completed_attempt_id nvarchar(64)   NULL,
    created_at           datetime2(7)   NOT NULL CONSTRAINT df_run_request_created DEFAULT SYSUTCDATETIME()
);
GO
CREATE INDEX ix_run_request_queue ON nexus.run_request (state, not_before, request_id);
GO

CREATE TABLE nexus.scope_lease (
    scope         nvarchar(128) NOT NULL CONSTRAINT pk_scope_lease PRIMARY KEY,
    holder        nvarchar(128) NULL,
    attempt_id    nvarchar(64)  NULL,
    fencing_token bigint        NOT NULL CONSTRAINT df_scope_lease_token DEFAULT 0,
    expires_at    datetime2(7)  NULL
);
GO

CREATE TABLE nexus.attempt (
    attempt_id    nvarchar(64)  NOT NULL CONSTRAINT pk_attempt PRIMARY KEY,
    request_id    bigint        NOT NULL CONSTRAINT fk_attempt_request REFERENCES nexus.run_request (request_id),
    attempt_no    int           NOT NULL,
    scope         nvarchar(128) NOT NULL,
    worker        nvarchar(128) NOT NULL,
    fencing_token bigint        NOT NULL,
    state         nvarchar(24)  NOT NULL
                  CONSTRAINT ck_attempt_state CHECK (state IN (N'RUNNING', N'COMPLETED', N'FAILED', N'ABANDONED')),
    failure       nvarchar(400) NULL,
    started_at    datetime2(7)  NOT NULL CONSTRAINT df_attempt_started DEFAULT SYSUTCDATETIME(),
    finished_at   datetime2(7)  NULL
);
GO

CREATE TABLE nexus.generation (
    attempt_id    nvarchar(64) NOT NULL CONSTRAINT pk_generation PRIMARY KEY
                  CONSTRAINT fk_generation_attempt REFERENCES nexus.attempt (attempt_id),
    request_id    bigint       NOT NULL,
    state         nvarchar(16) NOT NULL
                  CONSTRAINT ck_generation_state CHECK (state IN (N'OPEN', N'SEALED', N'ABANDONED', N'INELIGIBLE')),
    store_run_id  nvarchar(64) NULL,
    input_digest  char(64)     NULL,
    result_digest char(64)     NULL,
    sealed_at     datetime2(7) NULL
);
GO
-- C3: como máximo una generación sellada por solicitud (sealed_at nunca vuelve a NULL).
CREATE UNIQUE INDEX uq_generation_one_sealed_per_request ON nexus.generation (request_id)
    WHERE sealed_at IS NOT NULL;
GO

CREATE TABLE nexus.snapshot_ref (
    snapshot_id    nvarchar(64) NOT NULL CONSTRAINT pk_snapshot_ref PRIMARY KEY,
    content_sha256 char(64)     NOT NULL,
    registered_at  datetime2(7) NOT NULL CONSTRAINT df_snapshot_ref_registered DEFAULT SYSUTCDATETIME()
);
GO

CREATE TABLE nexus.artifact (
    attempt_id     nvarchar(64)  NOT NULL CONSTRAINT fk_artifact_generation REFERENCES nexus.generation (attempt_id),
    contract       nvarchar(128) NOT NULL,
    object_key     nvarchar(400) NOT NULL,
    content_sha256 char(64)      NOT NULL,
    logical_hash   char(64)      NOT NULL,
    size_bytes     bigint        NOT NULL,
    state          nvarchar(16)  NOT NULL CONSTRAINT df_artifact_state DEFAULT N'PRESENT'
                   CONSTRAINT ck_artifact_state CHECK (state IN (N'PRESENT', N'MISSING', N'CORRUPT')),
    CONSTRAINT pk_artifact PRIMARY KEY (attempt_id, contract)
);
GO

CREATE TABLE nexus.run_event (
    event_id      bigint IDENTITY(1, 1) NOT NULL CONSTRAINT pk_run_event PRIMARY KEY,
    kind          nvarchar(48)  NOT NULL,
    request_id    bigint        NULL,
    attempt_id    nvarchar(64)  NULL,
    claim_token   uniqueidentifier NULL,
    fencing_token bigint        NULL,
    actor         nvarchar(128) NULL,
    detail        nvarchar(400) NULL,
    server_ts     datetime2(7)  NOT NULL CONSTRAINT df_run_event_ts DEFAULT SYSUTCDATETIME()
);
GO
-- Append-only real: ni siquiera un procedimiento con encadenamiento de propiedad puede modificarla.
CREATE TRIGGER nexus.tr_run_event_append_only ON nexus.run_event
INSTEAD OF UPDATE, DELETE
AS
BEGIN
    THROW 50090, N'NEXUS:APPEND_ONLY run_event no admite UPDATE ni DELETE', 1;
END;
GO
