# Comparación visual antes / después — CORE v1.0 UI

Mismo juego **sintético** de enero 2031. Contiene:
- 5 ITEM, 2 de ellos con demanda desconocida;
- FG no certificable resuelto en el corte del 20;
- alerta de precedencia del Frozen;
- un Refresh fallido;
- notas CS.

Lo sirven, en cada caso:
- **Antes:** commit `b2d6a76` (Hito A).
- **Después:** esta intervención.

Las capturas se hicieron con `scripts/core_ui_screens.js` (mismas rutas, mismos tamaños, usuario `admin`).

| Pantalla | Antes | Después |
|---|---|---|
| Junta, 1366 × 768 | ![](before/laptop1366_resumen.png) | ![](after/laptop1366_resumen.png) |
| Junta, 1920 × 1080 | ![](before/desktop1920_resumen.png) | ![](after/desktop1920_resumen.png) |
| Junta, tablet 820 px | ![](before/tablet_resumen.png) | ![](after/tablet_resumen.png) |
| Junta, móvil 390 px | ![](before/mobile390_resumen.png) | ![](after/mobile390_resumen.png) |
| Detalle de ITEM y Customer Service, 1366 | ![](before/laptop1366_item.png) | ![](after/laptop1366_item.png) |
| Detalle de ITEM, móvil 390 | ![](before/mobile390_item.png) | ![](after/mobile390_item.png) |
| Estado del Refresh (fallido), 1366 | ![](before/laptop1366_refresh.png) | ![](after/laptop1366_refresh.png) |
| Historial de publicaciones, 1366 | ![](before/laptop1366_historial.png) | ![](after/laptop1366_historial.png) |
| Historial CS, 1366 | ![](before/laptop1366_cs_historial.png) | ![](after/laptop1366_cs_historial.png) |

## Cambios principales

| Aspecto | Antes | Después |
|---|---|---|
| Indicadores | 6 tarjetas iguales | 4 principales destacadas (Frozen, Embarcado, Cubierto FG, Sin cubrir) y 2 de apoyo; avisos de "Parcial: N ITEM sin dato" |
| Cobertura | Sin indicador gráfico | Barra embarcado / cubierto FG / sin cubrir. Solo aparece si las cifras son conocidas y cuadran exactamente; si no, se rotula el subconjunto o se declara no disponible. Mini barra por ITEM |
| Tabla | Cada celda con dos etiquetas de estado; 1366 px con desplazamiento horizontal | Celdas compactas alineadas a la derecha; "Sin cubrir" en rojo; excepciones como distintivos por ITEM; cabe completa en 1366 y 1920 sin desplazamiento horizontal (medido); en móvil pasa a tarjetas |
| Búsqueda y filtros | No había | ITEM, cobertura, estatus CS, solo excepciones y orden. Formulario GET renderizado en servidor |
| Customer Service | Un único formulario con selector de tipo y un campo de texto libre | Formularios separados: nota (textarea), estatus (selector con el vocabulario permitido), fecha CS (selector de fecha), adelanto (cantidad y fecha). Campos vigentes, referencia del mes anterior y última nota visibles; confirmación al guardar; historial con retiro |
| Refresh | Estado interno ("COMPLETED") y JSON de eventos | Pendiente, en ejecución, publicando, publicado, sin cambios, no publicado o fallido. Se **comprueba** contra el historial y nunca se supone. Recarga automática sin JavaScript |
| Fallos | Sin aviso en la junta | Aviso en la junta: la última actualización no se publicó, con su motivo, y se siguen mostrando los datos del último corte válido |
| Rollback | Botón directo | Pantalla de confirmación que explica qué cambia y qué no |
| Fechas y cantidades | ISO-8601 y números sin formato | "20 ene 2031 · 18:00 UTC", "Enero 2031", separador de miles |
| Detalle técnico | Mezclado con la vista principal | En secciones plegables: generación, hashes, procedencia, líneas y limitaciones. Nada se eliminó |
| Accesibilidad | Sin enlace de salto ni foco visible definido | "Saltar al contenido", foco visible (3 px), etiquetas en todos los campos, `caption` y `scope` en tablas, contraste WCAG AA medido |
