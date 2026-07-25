"""Errors raised while validating and resolving tool catalog entries."""


class ToolCatalogError(ValueError):
    """Base error for deterministic tool catalog operations."""


class DuplicateToolNameError(ToolCatalogError):
    """Raised when a catalog has more than one exact tool name."""


class ToolNotFoundError(ToolCatalogError):
    """Raised when an exact tool name is not registered in a catalog."""
