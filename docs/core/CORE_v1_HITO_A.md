# CORE v1.0 — Hito A: ejecución local, demostración y rollback

> Solo datos y usuarios **sintéticos**. Sin fuentes corporativas, sin PULSO, MAGNUM ni `PULSO_MAGNUM_STAGE`.
> Web solo en loopback.

## Instalar

Python 3.12 y `uv`. El grupo dev incluye Flask; para instalar solo el runtime web se usa `uv sync --frozen --extra web`.

```bash
uv sync --frozen
```

## Demostración completa

Dos meses sintéticos, web y navegador headless:

```bash
bash scripts/core_a_demo.sh /tmp/core_a_demo
```

Qué recorre:
1. Frozen del mes 1.
2. Refresh.
3. Notas CS, con un conflicto entre dos editores.
4. Current, extracto inválido y extracto tardío anterior al Frozen (genera alerta).
5. Nuevo corte.
6. Refresh fallido, que no publica.
7. Rollback.
8. Reinicio de la aplicación.
9. Mes 2: Frozen nuevo y reinicio mensual de los campos CS.
10. Cadena de publicaciones íntegra.

Después levanta `nexus web` en 127.0.0.1, comprueba el socket, verifica que rechaza 0.0.0.0 y ejecuta 8 comprobaciones en Chromium headless, con capturas.

## Uso manual

Preparar los datos sintéticos (reglas, usuarios y bandeja del mes 1):

```bash
uv run nexus core init-demo --data /tmp/core_a
```

Lanzar un Refresh desde la CLI:

```bash
uv run nexus core refresh --data /tmp/core_a --channel sintetico --now 2031-01-10T20:00:00Z
```

Arrancar la web:

```bash
NEXUS_WEB_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))') uv run nexus web --data /tmp/core_a --channel sintetico --simulate-start 2031-01-10T20:00:00Z
```

- Abrir http://127.0.0.1:8765.
- Usuarios: `lector`, `editorcs`, `editorcs2`, `refresher`, `admin`. La contraseña es `<usuario>-demo` (sintéticos).

Bandeja de fuentes: `DATA/inbox/{oeras_29,portal_cat,fg_inventory,shipments}/*.csv` (contratos en ADR-0011).

## Rollback

| Qué | Cómo |
|---|---|
| Publicación | `nexus core rollback --data DIR --channel sintetico --to <gen_id>`, o botón "Volver a esta" en Historial (administrador). El Frozen y las notas CS no se tocan |
| Código | La rama `feature/core-v1` es aditiva. `main`, `151154b` y E1-A, E1-B y Pilot-20 no dependen de `nexus.core` ni de `nexus.web` |
| Datos de la demostración | Borrar el directorio de datos. Las notas CS viven en `DATA/cs/` y no se borran con ningún rollback |
