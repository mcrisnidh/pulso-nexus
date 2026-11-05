"""Errores de la capa de metadatos (E1-B)."""

from __future__ import annotations

from nexus.errors import NexusError


class MetadataError(NexusError):
    """Error genérico de la capa de metadatos."""


class FencedError(MetadataError):
    """Token de fencing obsoleto o lease vencido (NEXUS:FENCED, 50010)."""


class ClaimInvalidError(MetadataError):
    """Claim inexistente, ajeno o vencido (NEXUS:CLAIM_INVALID, 50011)."""


class ConflictError(MetadataError):
    """Mismo identificador con contenido distinto (NEXUS:CONFLICT, 50020)."""


class AlreadySealedError(MetadataError):
    """La solicitud ya tiene otra generación sellada (NEXUS:ALREADY_SEALED, 50030)."""


class NotSealedError(MetadataError):
    """Se intenta completar sin generación sellada (NEXUS:NOT_SEALED, 50031)."""


class InvalidOperationError(MetadataError):
    """Parámetro o transición inválidos (NEXUS:INVALID, 50040)."""


class GuardError(MetadataError):
    """El entorno no cumple el perfil: no se escribe nada."""


class InsecureConnectionError(MetadataError):
    """Configuración de conexión insegura (TLS no verificado)."""


class AmbiguousOutcomeError(MetadataError):
    """Se perdió la respuesta: la operación pudo o no confirmarse (commit ambiguo)."""
