# PULSO NEXUS · R3 — área administrable «Clientes Portal CAT» (candidato NO INSTALADO)

**Referencia de código:** baseline FIX6.2 `0cb87e75726ba30b57e2e229347758d18324f8e7`; Frozen noviembre SHA256 `d5938411b58a06a65d978b31a64c9123a84869851c3af0d847dc1aebcc71fa28`.

## Contrato funcional

- Se añade navegación `Clientes Portal CAT` y página `GET /clientes-portal-cat` con catálogo histórico de **84 identidades exactas** (fuente de referencia R3 `R3_clientes_con_nombre_nov(1).csv`, 3147 líneas). Los nombres están truncados cuando así figuran en origen. Este catálogo es visual, **NO es la tabla de clasificación operativa**.
- **4 criterios comerciales informados Portal CAT:** `98753462  001`, `PERK2     001`, `PECL      001`, `17057704  205`.
- **4 criterios comerciales informados SSRS:** `TUPY      000`, `CATPERK   000`, `CATPERK   001`, `PERK1     001`.
- **76 identidades sin decisión**, incluidas 18 candidatas Caterpillar de serie 170 sujetas a validación de Customer Service (Carla). Criterio comercial informado **no significa** aprobación R3 v2 `CRC_APPROVED`.
- Solicitudes nuevas de **alta** `AGREGAR_PORTAL_CAT` y **baja** `RETIRAR_PORTAL_CAT` usando número de cliente ERP (1 a 10 caracteres) y secuencia exacta de 3 dígitos; se forma `customer_key_exact = customer_number.ljust(10) + sequence` (13 caracteres). Nunca deducir identidad por nombre, semejanza, ERP Item u otro prefijo.
- Formularios requieren motivo (12–500 caracteres), referencia de evidencia (5–250) y **mes propuesto posterior al mes actual**, para impedir retroactividad. Nunca se altera Frozen sellado.
- Cada solicitud se guarda localmente como objeto JSON de creación exclusiva, con UUID, UTC, autor `EDITOR_LOCAL_SIN_AUTENTICAR`, SHA256 verificable, estado `PENDIENTE_APROBACION`, `approved=false`, `applies_to_refresh=false`, `applies_to_frozen=false`. No se sobrescriben expedientes ni se silencian cambios de huella.
- Una cuenta de la lista SSRS explícitamente protegida no puede solicitarse como Portal CAT por vía ordinaria. Cambiar esas reglas exige expediente comercial de excepción y una fase autorizada adicional.
- No existe endpoint de aprobación, firma, activación, cambio a `rules.json`, escritura SQL, importación SSRS/Portal, ni publicación de esta UI. El catálogo visual no es consultado por `generaciones.py`, `servicio.py` ni conectores.

## Seguridad y limitaciones

La Junta actual solo está vinculada a localhost **y no autentica usuarios corporativos**. Esta UI es **preparatoria**, adecuada para redactar y recoger solicitudes locales, no para aprobar reglas. La protección existente `NEXUS_BASELINE_PROTECTION_01` **prohíbe modificar módulos operativos en la rama R3 preaprobación**: el parche fuente se entrega separado y no debe aplicarse allí ni instalarse sobre FIX6.2 sin ampliar formalmente el alcance de ingeniería y ejecutar una nueva guardia de integración independiente. No eludir ni modificar los hooks existentes.

El SHA256 de cada solicitud detecta corrupción accidental, **no acredita identidad de aprobador ni firma criptográfica**. Para uso multiusuario remoto, se requiere autenticación y autorización corporativas, junto con almacenamiento central y auditoría protegida.

## Fases

1. **Ahora:** probar el candidato sobre una copia temporal de FIX6.2 sin instalarlo, con pruebas sintéticas de exactitud, no retroactividad, protección de SSRS y ausencia de cualquier mutación operativa.
2. **Después de habilitar alcance R3-UI controlado:** integrar únicamente la interfaz y propuestas, manteniendo la aprobación y aplicación deshabilitadas. Preservar tag/frozen/instalación de baseline y emitir nuevo commit controlado.
3. **Cuando Carla responda:** registrar las 18 identidades exactas con su fuente validada, evidencia/fecha/aprobador; no asumir que todas son Portal CAT. Revisar las restantes 57 y las identidades nuevas del ERP.
4. **Posteriormente, fase R3 operativa distinta:** construir catálogo aprobado versionado y la compuerta de cambio con autoridad real, reglas efectivas por fecha, aplicación a SSRS Current y futuros Frozen, replay de diferencias, pruebas negativas y autorización expresa. No retroactuar noviembre.

## Condición de salida para el módulo preparatorio

Todas las altas/bajas quedan en `PENDIENTE_APROBACION` y **no alteran** SSRS Current, FG, CFDI, Portal CAT, Frozen noviembre, R1/R3, Change Backlog ni publicación. Si hay error de integridad, la página falla cerrada y no muestra registros fraudulentos como válidos.
