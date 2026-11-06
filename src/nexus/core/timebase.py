"""B0-7 — Calendario operativo de la planta (puro: sin E/S, sin reloj, sin zona del sistema).

- Los instantes absolutos se almacenan y comparan en UTC (``…Z``), como antes.
- El MES OPERATIVO (Frozen, Current, FG, embarques, mes vigente, vigencia CS) es el del calendario
  local de ``business_tz``.
- La ventana de embarques empieza a la medianoche local del día 1 y termina a la medianoche local
  del día 1 del mes siguiente, ambas convertidas a UTC.

La zona es SIEMPRE explícita (``zoneinfo``); nunca se usa la zona horaria del proceso. ``UTC``
reproduce exactamente el comportamiento previo a B0-7.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nexus.errors import ConfigurationError

UTC_NAME = "UTC"
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_TZ_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+)*$")


class TimezoneBindingError(ConfigurationError):
    """La zona configurada no coincide con la zona a la que ya está ligado el estado del canal
    (Frozen o publicaciones). Se rechaza para no reinterpretar el historial."""


SAFE_PROCEDURE = (
    "Procedimiento seguro: no edite ni elimine operation.json en una instalación con historial. "
    "Para operar con otra zona, cree un directorio de datos NUEVO configurado con esa zona y "
    "conserve el actual como archivo de solo lectura (ver docs/core/B0-7_ZONA_HORARIA.md)."
)


def _iso_z(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class BusinessCalendar:
    """Calendario de negocio de una zona IANA explícita."""

    def __init__(self, tz_name: str) -> None:
        if not isinstance(tz_name, str) or not _TZ_NAME.match(tz_name):
            raise ConfigurationError(f"zona horaria inválida: {tz_name!r}")
        self.name = tz_name
        if tz_name == UTC_NAME:
            self._tz: dt.tzinfo = dt.UTC
        else:
            try:
                self._tz = ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ConfigurationError(
                    f"zona horaria no disponible: {tz_name!r} (¿falta la base tzdata?)"
                ) from exc

    @property
    def is_utc(self) -> bool:
        return self.name == UTC_NAME

    @staticmethod
    def _instant(value: str | dt.datetime) -> dt.datetime:
        moment = (
            dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
        if moment.tzinfo is None:
            raise ConfigurationError("instante sin zona horaria: no se asume ninguna")
        return moment

    def month_of(self, instant: str | dt.datetime) -> str:
        """Mes operativo local (``YYYY-MM``) de un instante absoluto."""
        local = self._instant(instant).astimezone(self._tz)
        return f"{local.year:04d}-{local.month:02d}"

    def window_utc(self, month: str) -> tuple[str, str]:
        """[medianoche local del día 1 de ``month``, medianoche local del día 1 del mes siguiente),
        en UTC."""
        match = _MONTH.match(month)
        if not match:
            raise ConfigurationError(f"mes inválido: {month!r}")
        year, mon = int(match.group(1)), int(match.group(2))
        nxt = (year + 1, 1) if mon == 12 else (year, mon + 1)
        start = dt.datetime(year, mon, 1, tzinfo=self._tz)
        end = dt.datetime(nxt[0], nxt[1], 1, tzinfo=self._tz)
        return _iso_z(start), _iso_z(end)

    def fingerprint(self, month: str) -> dict[str, Any]:
        """Vínculo verificable de la zona con un mes: nombre y límites UTC efectivos (capturan las
        reglas de la base tzdata aplicadas)."""
        start, end = self.window_utc(month)
        return {"business_tz": self.name, "month": month, "window_utc": [start, end]}
