# ADR-0010 — Flask como web mínima de CORE v1.0

- **Estado:** aceptado para el Hito A (prototipo local). Revisión de seguridad de TI pendiente para el Hito B.
- **Decisión:** Flask 3.1.x con Werkzeug 3.1.x, versiones fijadas en `uv.lock` (3.1.3 / 3.1.9).
  - Se usan plantillas Jinja2 server-rendered, sin JavaScript (X12) y sin motores en la web (V2).
  - Se declara como extra opcional `web`; las dependencias de ejecución del núcleo siguen siendo `pyarrow` y `pyodbc`.
  - También está en el grupo `dev` para que sus pruebas corran siempre.
- **Por qué:** dependencia pequeña y madura que encaja con el monolito Python.

## Controles (Hito A), todos con prueba automática

| Control | Implementación | Prueba |
|---|---|---|
| Solo loopback | `nexus web` rechaza cualquier host que no sea 127.0.0.1, ::1 o localhost | `test_security_config_refusals`; `core_a_demo.sh` verifica el socket en `/proc/net/tcp` |
| Sin debug | `debug=False` y `use_reloader=False`; los errores muestran una página sin traza | Error 404/500 propio |
| Autenticación | `DevAuth`: usuarios sintéticos con hash; **rechazada en el canal operativo** | `test_security_config_refusals` |
| Roles | Lector, editor CS, refresher y administrador; denegación por defecto | `test_roles_default_deny` |
| CSRF | Token de sesión en todos los POST | `test_headers_auth_and_csrf` |
| Cookies | `HttpOnly` y `SameSite=Strict` (`Secure` en el Hito B con TLS) | `test_headers_auth_and_csrf` |
| Cabeceras | CSP sin `unsafe-inline`, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `no-store` | Pruebas y navegador headless (sin violaciones de CSP) |
| Escapado | Autoescape de Jinja2; ninguna plantilla usa `style=` ni `<script>` | `test_cs_via_web_…`, `test_templates_have_no_inline_styles_or_scripts` |
| Secreto | `NEXUS_WEB_SECRET` desde el entorno, de 32 caracteres o más | `test_security_config_refusals` |
| Datos | La web solo lee lo publicado, con verificación de SHA-256 y fallo cerrado; import-linter impide que importe cálculo o adaptadores | `test_integrity_error_fails_closed_per_item`, contrato C6 |

## Pendiente para el Hito B

- SSO/AD corporativo.
- TLS.
- Servidor WSGI de producción: el servidor de desarrollo de Werkzeug queda prohibido fuera de loopback.
- Red interna únicamente.
- Revisión de avisos de seguridad de las versiones fijadas.
