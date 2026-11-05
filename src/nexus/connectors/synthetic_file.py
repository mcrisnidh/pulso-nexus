"""Conector de archivo local para fixtures SINTÉTICOS.

Lee bytes y los devuelve sin interpretarlos, filtrarlos ni transformarlos. No
conserva la ruta absoluta de origen, solo el nombre del archivo.
"""

from __future__ import annotations

from pathlib import Path

from nexus.connectors.base import CaptureResult

NAME = "synthetic_file"
VERSION = "1.0.0"


def capture(path: Path | str, source_id: str = "synthetic") -> CaptureResult:
    source = Path(path)
    content = source.read_bytes()
    return CaptureResult(
        connector=NAME,
        connector_version=VERSION,
        source_id=source_id,
        media_type="text/csv",
        content=content,
        source_metadata={"file_name": source.name},
    )
