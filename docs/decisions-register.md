# Registro de decisiones — PULSO NEXUS

Estado a la entrega de E1-A. Categorías: **vigentes**, **descartadas**, **propuestas** y
**desconocidas/pendientes**. La fuente es el plan aprobado E0-R2 (`docs/architecture/E0.md`,
§7 y §10).

## Estado de las propuestas en E1-A

| Propuesta | ADR | Estado en E1-A |
|---|---|---|
| P-ADR-01 monolito modular | `docs/adr/0001-monolito-modular.md` | Aceptada e implementada |
| P-ADR-04 protocolo de objetos (pasos 1–3) | `docs/adr/0002-protocolo-objectstore.md` | Aceptada e implementada (los pasos 4–8 son de E1-B/E1-C) |
| P-ADR-05 niveles L1/L2/L3 | `docs/adr/0003-niveles-reproducibilidad.md` | Aceptada e implementada |
| P-ADR-07 allowlist de conectores | `docs/adr/0005-allowlist-conectores.md` | Aceptada e implementada |
| P-ADR-08 Parquet/Arrow | `docs/adr/0004-parquet-arrow.md` | Aceptada e implementada |
| P-ADR-02, 03, 06, 09 (SQL Server, procedimientos, roles, guard) | — | **No iniciadas.** Corresponden a E1-B/E1-C, que no están autorizadas |

### Vigentes (confirmadas por usuario o revisor)
- V1: proyecto nuevo, sin arquitecturas heredadas.
- V2: la web no contiene motores.
- V3: los conectores no implementan reglas comerciales.
- V4: los motores operan sobre contratos normalizados.
- V5: **Microsoft SQL Server** es el motor corporativo. El laboratorio es independiente de la base productiva pero comparte instancia.
- V6: los datos manuales se originan en SQL, con un contrato pendiente de validación.
- V7: la certificación transaccional se hace con SQL Server.
- V8: inventario de 11 dominios (§3), incluido Status Ventas. Sus reglas llegarán mediante un contrato separado y validado.
- V9: generaciones versionadas y publicación atómica tras la certificación. Ejecución manual y programada idénticas.
- V10: sin datos reales, credenciales, conexiones ni cambios de infraestructura en esta fase. Lo offline no está certificado para producción.
- V11: se conservan el monolito modular, los motores puros, los snapshots inmutables y los contratos versionados (revisión E0-R1).
- V12: destino SQL = esquema propio `nexus` en `PULSO_MAGNUM_STAGE` (instancia compartida con producción), sujeto a autorización. El desarrollo offline usa una instancia SQL Server temporal local con datos sintéticos.
- V13: E1 se divide en E1-A (sin SQL), E1-B (SQL Server local aislado) y E1-C (certificación, publicación, identidades y adversariales).
- V14: las reglas comerciales existen pero **no se han proporcionado todavía**. Llegarán como CRC validados. No se inventan.
- V15: la certificación usa hashes recalculados por el certificador. La cadena de hashes en la misma base no cuenta como evidencia frente a un administrador privilegiado.

### Vigentes de CORE v1.0 (noviembre 2026; registradas en la intervención R1/R3)

Procedencia común:
- dictamen R1/R3 (2026-11-07);
- resolución D-A…D-F;
- regla del Frozen definitivo;
- autorización de implementación controlada.

Evidencia: `docs/evidence/CORE-R1R3/REPORT.md`.

- V16: `OTHER` solo se asigna mediante una regla comercial **explícita y aprobada**.
- V17: toda clave de cliente ERP sin clasificación explícita es `UNKNOWN` (reglas `nexus.core.rules/2`). Nunca es `OTHER` automático.
- V18: una identidad `UNKNOWN` o una identidad Portal pendiente de R1 que afecte al universo se conserva íntegra y **bloquea la publicación oficial**. En `replay` queda como diagnóstico NO-GO (`validation.no_go`), nunca promovible.
- V19: no se elimina demanda desconocida ni se inventan equivalencias. Desconocido ≠ 0.
- V20: TUPY es independiente de CAT, y APTIV y DELPHI son identidades independientes (clave exacta).
- V21: homologación R1 con tabla v2 (`approval_id`) verificada contra un **registro autorizado independiente** y una lista privada de aprobadores (D-D). Muchos-a-uno solo con `R1_REVISION_EQUIVALENCE` (D-E). El destino ERP debe coincidir exactamente con OE965/IN092U del periodo (D-F). Un ITEM solo-Portal se acredita con `APPROVED_PORTAL_ONLY` (identidad `PORTAL::<Item ID>`).
- V22: **Frozen definitivo** = (29 OERAS Frozen − demanda CAT ERP) + Portal CAT Frozen depurado. **Universo mensual** = ITEM distintos del Frozen definitivo. Las fuentes actuales se filtran contra él y nunca lo amplían.
- V23: reglas v1 (`nexus.core.rules/1`) solo en ejecución sintética aislada (canal `sintetico` y fuentes `SYNTHETIC`), sin delimitación de universo: huella dorada histórica `400e36ce…`. Las fuentes reales exigen v2 en cualquier canal (D-B).
- V24: Pilot-20 conserva su comportamiento histórico. `planning/rules.py` no cambia; CORE selecciona explícitamente la clasificación estricta (D-C).
- V25: la fórmula comercial vigente no cambia (K-1). El Frozen da la demanda base y los extractos actuales son informativos. K-5 (CAT = 0 cuando el Portal no tiene líneas para un ITEM conservado) queda como **cuestión comercial abierta**; hoy es «no determinable».
- V26: OE965 se usa como representación **provisional** de 29 OERAS (equivalencia `NOT_ESTABLISHED`).
- V27: `stage()` escribe solo en un directorio privado sin enlaces ni junctions en ningún componente, sin `store/` previo y sin seguir enlaces del origen.

### Descartadas
- X1: SharePoint como fuente operativa.
- X2: Microsoft Graph para datos manuales.
- X3: conectores heredados no aprobados.
- X4: PostgreSQL como dependencia productiva (salvo autorización expresa).
- X5: orquestador externo (Airflow, Dagster…) en E1–E4.
- X6: broker o cola externa.
- X7: microservicios.
- X8: deduplicación global de artefactos.
- X9: `sp_getapplock` como mecanismo principal de lease.
- X10: ORM para las rutas transaccionales críticas.
- X11: `NOLOCK` o `READ UNCOMMITTED`.
- X12: SPA en la web.

- X13: crear otra base o instancia corporativa para NEXUS.
- X14: clasificación de cliente por **omisión** (`OTHER` implícito) en CORE con fuentes reales. Sustituye, solo para CORE, la frase de `docs/planning/PILOT20.md` «las demás (…, otras) se conservan».
- X15: coincidencia aproximada o normalizada de ITEM o cliente (mayúsculas, espacios, guiones, ceros, Unicode).
- X16: cuarentena del Portal **completo** por homologación parcial. Sustituida por un estado R1 por línea (`MAPPED`, `PORTAL_ONLY`, `PENDING`); la cuarentena perdía ITEM solo-Portal legítimos.
- X17: universo mensual por unión directa OE965 ∪ Portal, o ampliado por fuentes actuales.
- X18: tabla de homologación v1 (`APPROVED` textual sin registro autorizado) como evidencia de aprobación.

### Propuestas (cada una pasará a ADR en E1)
- P-ADR-01: monolito modular en Python 3.12 con fronteras verificadas por import-linter.
- P-ADR-02: `pyodbc` y T-SQL explícito, con migraciones como scripts `.sql` aplicados por DBA.
- P-ADR-03: procedimientos almacenados para lease, registro, sellado, certificación y publicación.
- P-ADR-04: protocolo "archivo antes que fila" y rutas con propietario.
- P-ADR-05: niveles L1, L2 y L3, con `logical_hash` canónico.
- P-ADR-06: cinco roles con principales SQL separados y política firmada por hash.
- P-ADR-07: allowlist de conectores.
- P-ADR-08: Parquet/Arrow para datos tabulares.
- P-ADR-09: guard de entorno por sentinel.

### Desconocidas o pendientes
- P1: inventario de fuentes corporativas.
- P2: contrato de datos manuales en SQL (esquema, edición, versionado y auditoría).
- P3: CRC de los 11 dominios y sus dependencias.
- P4: política de certificación, aprobación humana y quiénes forman la autoridad.
- P5: almacenamiento de artefactos de producción, backups y WORM.
- P6: autenticación y autorización web (¿SSO?).
- P7: formatos de reportes.
- P8: frecuencia, SLA, retención y retención legal.
- P9: zona horaria y calendario operativo, unidades.
- P10: criterios de certificación para producción.
- P11: **cerrado como política** en la revisión final. Se permite una instancia SQL Server temporal offline, con datos sintéticos, para desarrollo. Su uso corresponde **solo a E1-B, que no está autorizada**. E1-A no usa SQL. Esta es la única entrada vigente de P11 y sustituye a cualquier mención anterior.
- P12: versión, edición, collation y opciones de aislamiento de SQL Server.
- P13: autenticación SQL o AD y gestor de secretos.
- P14: disponibilidad de SQL Server Audit.
- P15: ventanas y límites de carga permitidos en el laboratorio compartido.

- P16: custodia de claves de firma del certificador y destino externo de anclaje (E1-C).
