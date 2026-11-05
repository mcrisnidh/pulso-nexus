"""Errores tipados de PULSO NEXUS.

Cada error representa un fallo cerrado: el sistema se detiene en lugar de
continuar con datos dudosos.
"""

from __future__ import annotations


class NexusError(Exception):
    """Base de todos los errores de NEXUS."""


class IntegrityError(NexusError):
    """El contenido leído no coincide con el hash esperado o la estructura fue alterada."""


class ImmutableConflictError(NexusError):
    """Se intentó escribir contenido distinto en una clave inmutable ya existente."""


class UnsupportedFilesystemError(NexusError):
    """El sistema de archivos no ofrece una publicación sin reemplazo segura."""


class InvalidKeyError(NexusError):
    """La clave de objeto no es portable (POSIX y Windows/NTFS) o es insegura."""


class ContractError(NexusError):
    """Los datos no cumplen el contrato o el contrato cambió sin cambiar de versión."""


class NormalizationError(NexusError):
    """La captura no puede normalizarse en su totalidad (p. ej., codificación inválida)."""


class NotReproducibleError(NexusError):
    """Faltan entradas necesarias (p. ej., snapshot ausente) para reproducir una ejecución."""


class ConfigurationError(NexusError):
    """Parámetros o configuración inválidos."""
