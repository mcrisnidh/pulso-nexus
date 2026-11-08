# PULSO NEXUS — FG v0.3.2, cierre funcional y versionado controlado

**Estado:** VIGENTE para el laboratorio aislado de la Junta. **No es publicación productiva** ni certificación comercial de R1/R3. El motor CORE original permanece inalterado y sus pruebas deben conservarse.

**Base de origen:** paquete `PULSO_NEXUS_v0.3.2.zip`, SHA-256 `87ed581d0c34307fd91c6ad7f6ba47e91eefcb1c0be1048dca03001e7e5b4dbb`; repositorio `mcrisnidh/pulso-nexus`, `feature/core-v1` en `b4ec5be32cf7552c1e9fae0086b038861b8f40c1`. Se incorpora como **aplicación aparte** bajo `apps/nexus_junta_preview` y no como conector registrado en `src/nexus/connectors/allowlist.toml`: este último continúa sujeto a ADR-0005 y su contrato de solo transporte.

## Evidencia real en ASMXC1200 (8-nov-2026)

- Entorno, conexión y consulta ODBC de solo lectura con cuenta `remcb` y `AuroraODBC`: **PASS**. `SORTTYPE=0` es un atributo **privado** de la conexión de NEXUS; el DSN compartido no se editó.
- Tabla `AULT2F2PRD.STKBAL`, `CONO60='36'`, columnas `PNUM60`, `STRC60`, `SPHY60`, filtro `SPHY60 <> 0`: 560 pares ITEM/stockroom extraídos.
- Comparación con el reporte IN092U: alcance FG **343/343 pares exactos**, diferencia global de 1 par en almacén TH, explícitamente fuera del alcance acreditado. Los cortes manual y directo difieren 70 minutos: **no simultáneos**.
- Acreditación en el laboratorio: esquema `/2` válido con alcance `["FG"]`; `fg_odbc.activo=true` únicamente en configuración privada.
- `Refresh` iniciado en el navegador: `PASS`, conector FG `CAPTURADO`, 560 filas, captura nueva e íntegra, generación vigente, 238 ITEM del universo mensual y Frozen SHA-256 inalterado.
- Backlog SSRS y Embarques: `INACTIVO`. La vista sigue **PROVISIONAL — R1/R3 PENDIENTES — NO PUBLICABLE**; Programa y Faltante continúan N/D por falta de Embarques acreditados.

## Contratos obligatorios y límites

1. La lectura corporativa se ejecuta solo en ASMXC1200 con credenciales del Administrador de credenciales de Windows; **nunca** codificar credenciales, IP privadas, copias de `conectores.json`, informes, `.raw`, `.csv` corporativos, acreditaciones o notas en Git.
2. El código versionado entrega una **plantilla inactiva**. La activación vive exclusivamente en `%LOCALAPPDATA%\PULSO_NEXUS_LAB\junta-data-2026-11\conectores.json` y requiere acreditación verificada localmente.
3. Refresh exitoso consulta FG nuevamente. Contingencia conserva último corte solo si la política lo admite, y muestra su antigüedad. Congelado mensual no cambia.
4. La diferencia TH permanece en el informe completo; acreditación únicamente FG. No convertir ausencias desconocidas en cero ni inferir embarques.
5. Este versionado **no altera** `src/nexus/core`, `src/nexus/connectors`, esquemas SQL, el registro oficial de R1/R3 ni el repositorio PULSO Web.
6. Una actualización del CORE puede invalidar la compatibilidad: ejecutar ambas suites offline antes de hacer merge. No asumir que 147 pruebas sintéticas equivalen a una prueba corporativa.

## Clasificación de compatibilidad

| Componente | Estado | Justificación |
|---|---|---|
| Motor puro `src/nexus/core` y Frozen mensual | VIGENTE | Se importa y reutiliza; no se copia ni cambia |
| Junta v0.3.2 + conector FG aislado | VIGENTE (laboratorio) | Refresh corporativo real y SHA verificados |
| Datos, credencial y acreditación locales | VIGENTE (privado, no versionable) | Se conservan solo en ASMXC1200 |
| SSRS Backlog | PENDIENTE | Todavía no conectado ni acreditado |
| Embarques CFDI como fuente de despacho físico | DESCARTADO como evidencia suficiente | No habilitar Programa/Faltante |
| R1/R3 provisionales | PENDIENTE | No equivale a aprobación comercial |
| PULSO Web legacy / MAGNUM | LEGACY | No se importa al CORE |

## Procedimiento posterior

- Para ejecutar la aplicación versionada desde una rama limpia descendiente de `b4ec5be`, usar `apps/nexus_junta_preview/NexusJunta.ps1` desde ASMXC1200; no usar el lanzador antiguo del ZIP que exige HEAD exactamente en `b4ec5be`.
- Revisar el PR de `feature/fg-v032-core` hacia `feature/core-v1`; verificar pruebas de Junta (147, sintéticas), CORE offline y restricciones arquitectónicas. **No hacer merge automático a main**.
- Si fallan pruebas, no empujar ni mezclar cambios. El laboratorio con FG operativo continúa funcionando desde `%LOCALAPPDATA%\PULSO_NEXUS_LAB\nexus_junta_v02`.
