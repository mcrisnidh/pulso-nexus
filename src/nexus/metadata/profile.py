"""Perfiles de entorno: las reglas de permitido/prohibido viven en datos versionados."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from importlib import resources

from nexus.metadata.errors import GuardError

_NAME = re.compile(r"^[a-z0-9-]{1,40}$")


@dataclass(frozen=True)
class Profile:
    name: str
    kind: str
    schema_version: int
    host_allowlist: tuple[str, ...]
    host_denylist: tuple[str, ...]
    database_pattern: str
    database_denylist: tuple[str, ...]
    server_name_pattern: str
    allowed_editions: tuple[str, ...]
    lease_ms: int
    heartbeat_ms: int
    claim_ms: int
    backoff_ms: int
    max_attempts: int
    query_timeout_s: int

    def check_host(self, host: str) -> None:
        lowered = host.lower()
        for fragment in self.host_denylist:
            if fragment.lower() in lowered:
                raise GuardError(f"host prohibido por el perfil {self.name}: {host!r}")
        if host not in self.host_allowlist:
            raise GuardError(f"host fuera de la allowlist del perfil {self.name}: {host!r}")

    def check_database(self, database: str) -> None:
        denied = {d.lower() for d in self.database_denylist}
        if database.lower() in denied:
            raise GuardError(f"base prohibida por el perfil {self.name}: {database!r}")
        if not re.fullmatch(self.database_pattern, database):
            raise GuardError(f"base fuera del patrón del perfil {self.name}: {database!r}")


def load_profile(name: str) -> Profile:
    if not _NAME.match(name):
        raise GuardError(f"nombre de perfil inválido: {name!r}")
    try:
        text = (
            resources.files("nexus.metadata").joinpath(f"profiles/{name}.toml").read_text("utf-8")
        )
    except FileNotFoundError as exc:
        raise GuardError(f"perfil inexistente: {name}") from exc
    data = tomllib.loads(text)
    if data.get("name") != name:
        raise GuardError(f"el perfil {name} declara otro nombre")
    return Profile(
        name=str(data["name"]),
        kind=str(data["kind"]),
        schema_version=int(data["schema_version"]),
        host_allowlist=tuple(str(h) for h in data["host_allowlist"]),
        host_denylist=tuple(str(h) for h in data["host_denylist"]),
        database_pattern=str(data["database_pattern"]),
        database_denylist=tuple(str(d) for d in data["database_denylist"]),
        server_name_pattern=str(data["server_name_pattern"]),
        allowed_editions=tuple(str(e) for e in data["allowed_editions"]),
        lease_ms=int(data["lease_ms"]),
        heartbeat_ms=int(data["heartbeat_ms"]),
        claim_ms=int(data["claim_ms"]),
        backoff_ms=int(data["backoff_ms"]),
        max_attempts=int(data["max_attempts"]),
        query_timeout_s=int(data["query_timeout_s"]),
    )
