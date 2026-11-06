"""Autenticación enchufable. Hito A: ``DevAuth`` con usuarios SINTÉTICOS de un archivo local
(contraseñas guardadas como hash al cargar). El Hito B la sustituye por SSO/AD corporativo; la
aplicación rechaza ``DevAuth`` en el canal operativo."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

KNOWN_ROLES = frozenset({"reader", "cs_editor", "refresher", "admin"})


@dataclass(frozen=True)
class DevAuth:
    users: dict[str, dict[str, Any]]
    kind: str = "DEV"

    @classmethod
    def from_file(cls, path: Path) -> DevAuth:
        data = json.loads(path.read_text("utf-8"))
        users = {}
        for name, u in data.items():
            roles = sorted(set(u["roles"]))
            if not set(roles) <= KNOWN_ROLES:
                raise ValueError(f"rol desconocido para {name}: {roles}")
            users[name] = {
                "name": name,
                "roles": roles,
                "hash": generate_password_hash(u["password"]),
            }
        return cls(users)

    def verify(self, name: str, password: str) -> bool:
        u = self.users.get(name)
        return bool(u) and check_password_hash(u["hash"], password)  # type: ignore[index]

    def user(self, name: str) -> dict[str, Any] | None:
        u = self.users.get(name)
        return None if u is None else {"name": u["name"], "roles": list(u["roles"])}
