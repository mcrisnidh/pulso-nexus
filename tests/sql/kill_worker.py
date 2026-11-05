"""Worker que se mata con SIGKILL en una etapa concreta (B9). Lo lanza la prueba como proceso."""

from __future__ import annotations

import os
import signal

from nexus.metadata.connection import ConnectionSettings
from nexus.metadata.profile import load_profile
from nexus.metadata.sqlserver import SqlServerMetadata
from nexus.pipeline.worker import work_once
from nexus.storage.object_store import LocalObjectStore


def main() -> None:
    stage = os.environ["NEXUS_KILL_STAGE"]
    profile = load_profile("e1b-ephemeral")
    settings = ConnectionSettings(
        host=os.environ["NEXUS_SQL_HOST"],
        port=int(os.environ.get("NEXUS_SQL_PORT", "1433")),
        database=os.environ["NEXUS_SQL_DATABASE"],
        user="nexus_producer",
        password=os.environ["NEXUS_SQL_PRODUCER_PASSWORD"],
    )
    nonce = os.environ["NEXUS_SQL_NONCE"]
    repo = SqlServerMetadata(settings, profile, nonce, actor="victim")
    beat = SqlServerMetadata(settings, profile, nonce, actor="victim-hb")

    def hook(name: str) -> None:
        if name == stage:
            os.kill(os.getpid(), signal.SIGKILL)

    work_once(LocalObjectStore(os.environ["NEXUS_STORE"]), repo, beat, profile, "victim", hook=hook)
    raise SystemExit(f"la etapa {stage} no se alcanzó")


if __name__ == "__main__":
    main()
