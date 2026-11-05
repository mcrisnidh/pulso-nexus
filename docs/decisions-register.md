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
