"""PULSO NEXUS CORE v1.0 (Hito A).

Componentes:

- C1 ``refresh``/``state``: Frozen mensual inmutable (29 OERAS + Portal CAT), primera
  captura cronológica válida.
- C2 ``sources``/``refresh``: captura Current e inventario FG con validación por fuente.
- C3 ``shipments``: adaptador contractual de embarques físicos (nunca facturación).
- C4 ``coverage``: cálculo puro de demanda, atendida, pendiente, FG, cubierta y sin cubrir.
- C5 ``refresh`` / ``state``: worker Refresh (cola, lease y fencing de E1-B) y publicación atómica.
- C6 ``nexus.web``: web mínima (Flask, solo loopback).
- C7 ``cs``: notas y seguimiento de Customer Service, append-only e independiente de las
  generaciones.

Reutiliza E1-A (ObjectStore, sellado, hashing), E1-B (worker, cola, lease) y Pilot-20 (lectura de
tablas con procedencia, reglas por clave exacta, decisión Portal, modelo de certeza). No usa WO,
routing, producción ni capacidad.
"""
