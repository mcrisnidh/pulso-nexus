"""Extrae los informes de interbloqueo (xml_deadlock_report) de la sesión system_health del
SQL Server EFÍMERO de CI y los resume: víctima, procesos (procedimiento, línea, sentencia) y
recursos (objeto, índice, modo). Solo diagnóstico; credenciales efímeras por entorno."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET

from nexus.metadata.connection import connect, settings_from_env

QUERY = """
SELECT CAST(t.target_data AS xml).query('RingBufferTarget/event[@name="xml_deadlock_report"]')
FROM sys.dm_xe_session_targets AS t
JOIN sys.dm_xe_sessions AS s ON s.address = t.event_session_address
WHERE s.name = N'system_health' AND t.target_name = N'ring_buffer'
"""


def summarize(raw: str, limit: int) -> list[dict[str, object]]:
    root = ET.fromstring(f"<r>{raw}</r>")  # noqa: S314 - XML del servidor efímero propio
    out = []
    for event in root.findall("event")[-limit:]:
        graph = event.find(".//deadlock")
        if graph is None:
            continue
        victims = [v.get("id") for v in graph.findall("./victim-list/victimProcess")]
        processes = []
        for p in graph.findall("./process-list/process"):
            frames = [
                {
                    "proc": f.get("procname"),
                    "line": f.get("line"),
                    "stmt": " ".join((f.text or "").split())[:240],
                }
                for f in p.findall("./executionStack/frame")
            ]
            processes.append(
                {
                    "id": p.get("id"),
                    "victim": p.get("id") in victims,
                    "waitresource": p.get("waitresource"),
                    "lockMode": p.get("lockMode"),
                    "isolation": p.get("isolationlevel"),
                    "frames": frames[:3],
                }
            )
        resources = []
        for r in list(graph.find("./resource-list") or []):
            resources.append(
                {
                    "type": r.tag,
                    "object": r.get("objectname"),
                    "index": r.get("indexname"),
                    "mode": r.get("mode"),
                    "owners": [
                        (o.get("id"), o.get("mode")) for o in r.findall("./owner-list/owner")
                    ],
                    "waiters": [
                        (w.get("id"), w.get("mode"), w.get("requestType"))
                        for w in r.findall("./waiter-list/waiter")
                    ],
                }
            )
        out.append(
            {"timestamp": event.get("timestamp"), "processes": processes, "resources": resources}
        )
    return out


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    conn = connect(
        settings_from_env("NEXUS_SQL_ADMIN_USER", "NEXUS_SQL_ADMIN_PASSWORD", database="master"), 60
    )
    try:
        row = conn.cursor().execute(QUERY).fetchone()
    finally:
        conn.close()
    raw = str(row[0]) if row and row[0] is not None else ""
    print(json.dumps({"deadlocks": summarize(raw, limit)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
