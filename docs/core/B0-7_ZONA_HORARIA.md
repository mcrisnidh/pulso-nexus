# B0-7 — Zona horaria operativa: configuración, identidad y procedimiento seguro

## Qué hace

| Concepto | Regla |
|---|---|
| Instantes absolutos (extracción, captura, corte, movimientos, eventos, versiones CS) | Se guardan y comparan en **UTC**, igual que antes |
| Mes operativo (Frozen, Current, FG y embarques del mes, mes vigente, canal operativo, vigencia CS) | Mes del calendario local de `business_tz` |
| Ventana de embarques | `[medianoche local del día 1 → UTC, corte]` |
| Fechas comerciales (entregas, fechas Portal, Fecha CS) | Siguen siendo fechas: sin cambio |

La zona se declara explícitamente (`zoneinfo`). Nunca se usa la zona del sistema operativo.

## Configuración

Archivo opcional `DATA/operation.json`:

```json
{"schema": "nexus.core.operation/1", "business_tz": "America/Mexico_City"}
```

- **Sin archivo** (instalaciones previas): UTC, con comportamiento idéntico byte a byte a `f404f96`.
- **Zona inválida o no disponible**, esquema distinto o claves desconocidas: el servicio **no arranca**.
- **Windows:** `zoneinfo` necesita el paquete `tzdata` instalado en el entorno de despliegue (decisión del Hito B).

## Identidad de generación (zona ligada criptográficamente)

- **Generaciones:** en toda generación no UTC, las entradas incluyen `calendar = {business_tz, month, window_utc}` y su `gen_id` es `SHA-256(entradas)[:32]`.
  - Otra zona, o la misma zona con otras reglas `tzdata`, produce otro `gen_id`: no hay colisiones.
  - Una generación nunca se reutiliza ni se publica bajo otra configuración: `publish` lo rechaza.
- **Registro de publicación:** incluye `business_tz` y el `manifest_sha256` del manifiesto sellado, dentro de la cadena de hashes de publicación.
- **Frozen:** cada Frozen no UTC registra `business_tz`. Un Frozen nunca se reinterpreta con otra zona: si no coincide, el Refresh no publica.
- **Vínculo del canal:** `calendar/<canal>/binding.json`, sellado, escrito una sola vez antes de crear estado.
  - En almacenes nuevos se escribe también para UTC: es el único metadato adicional y no cambia generaciones, Frozen ni publicaciones.
  - Los almacenes **previos a B0-7** con historial no reciben ningún archivo y quedan ligados implícitamente a UTC.
- **Llave de idempotencia del Refresh:** incluye la zona cuando no es UTC.

## Rollback seguro

**Eliminar o editar `operation.json` NO es un rollback.**

Si el canal ya tiene Frozen o publicaciones ligados a una zona, el servicio (CLI y web) **rechaza arrancar** con otra zona (`TimezoneBindingError`). El historial queda intacto.

| Situación | Qué hacer |
|---|---|
| Se cambió o eliminó `operation.json` por error | Restaurar el archivo con la zona registrada en `calendar/<canal>/binding.json` (o UTC si el almacén es anterior a B0-7 y no tiene vínculo). Nada se modificó mientras estuvo rechazado |
| Retirar el **código** B0-7 en una instalación que operó con zona no UTC | **No** revertir el código sobre ese directorio: el código anterior ignoraría la zona y reinterpretaría el mes. Conservar el directorio como archivo de solo lectura y operar con un directorio nuevo |
| Retirar el **código** B0-7 en una instalación que siempre operó en UTC | `git revert`. El comportamiento es idéntico; el único archivo adicional (`calendar/…/binding.json`) es ignorado por el código anterior |
| Operar con **otra zona** en una instalación con historial | Ver el procedimiento siguiente |

Procedimiento para cambiar de zona en una instalación con historial:
1. Detener la web y el worker. No modificar el directorio actual: queda como archivo de solo lectura (Frozen, publicaciones y eventos intactos).
2. Crear un **directorio de datos nuevo** con `rules.json`, `users.json` y `operation.json` con la zona nueva.
3. Copiar el directorio `cs/` tal cual si se desea conservar el seguimiento de Customer Service. Es append-only y sellado; cada versión conserva su mes de vigencia original.
4. Iniciar el nuevo directorio en un mes **sin estado previo**: el primer Refresh registra el Frozen y el vínculo de zona del mes.
5. Registrar el cambio (quién, cuándo, motivo) en la bitácora operativa.

## Verificación

```bash
uv run pytest tests/core/test_core_timezone.py -v
```

Cubre:
- TZ-01…TZ-18 (calendario, Frozen, embarques, reversas, CS, canal operativo, determinismo y compatibilidad);
- ID-01…ID-03 (identidad);
- RB-01…RB-08 (rollback seguro, carreras, manipulación).
