"""Adaptadores de fuentes de planificación (lectura de archivos; sin reglas de negocio).

Cada fuente se describe en un *layout* declarativo (ruta relativa, delimitador, granularidad,
corte declarado). Un adaptador nunca falla de forma global: si el archivo no existe o no se puede
leer, la tabla queda ``available=False`` con su motivo y el motor marca esa etapa como
``BLOCKED_SOURCE``.
"""
