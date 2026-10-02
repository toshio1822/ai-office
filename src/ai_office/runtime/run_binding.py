"""Exact immutable Run identity for execution contracts.

A binding is fixed when the value that owns it is constructed and is never
mutated afterwards.  Durable Run identity is exactly ``run_id +
manifest_digest``; Run Input is not part of it because the Run Manifest is its
only authority.  This module contains only value-level validation and read
helpers; it is not a durable authority or a binding sidecar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MANIFEST_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class WorkflowRunBinding:
    """Exact immutable identity of one Run and its pinned Manifest."""

    run_id: str
    manifest_digest: str

    def __post_init__(self) -> None:
        validate_run_binding(self.run_id, self.manifest_digest)


def validate_run_binding(run_id: object, manifest_digest: object) -> None:
    """Validate one complete Run binding without coercion."""
    if type(run_id) is not str or _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("workflow Run identity is invalid")
    if (
        type(manifest_digest) is not str
        or _MANIFEST_DIGEST_PATTERN.fullmatch(manifest_digest) is None
    ):
        raise ValueError("workflow Run Manifest identity is invalid")


def select_run_binding(
    binding: object,
    run_id: object,
    manifest_digest: object,
) -> WorkflowRunBinding | None:
    """Resolve the one immutable binding fixed at construction time.

    Accepts either a complete binding value or the explicit identity fields.
    Mixing inconsistent sources fails closed; a partial identity is rejected.
    This helper only computes the construction-time value; callers store the
    returned binding in ``__init__`` and never mutate it afterwards.
    """
    if binding is not None:
        if type(binding) is not WorkflowRunBinding:
            raise TypeError("workflow Run binding is invalid")
        if run_id is not None and run_id != binding.run_id:
            raise ValueError("workflow Run binding is inconsistent")
        if manifest_digest is not None and manifest_digest != binding.manifest_digest:
            raise ValueError("workflow Run binding is inconsistent")
        return binding
    if run_id is None and manifest_digest is None:
        return None
    if run_id is None or manifest_digest is None:
        raise ValueError("workflow Run binding is incomplete")
    return WorkflowRunBinding(run_id, manifest_digest)


def binding_of(value: object) -> WorkflowRunBinding | None:
    """Read the immutable binding fixed on one execution value, if any."""
    binding = getattr(value, "_run_binding", None)
    if binding is None:
        return None
    if type(binding) is not WorkflowRunBinding:
        raise TypeError("workflow Run binding is invalid")
    return binding


def require_run_binding(value: object) -> WorkflowRunBinding:
    """Reject old Run-less values at a Run-owned operation boundary."""
    binding = binding_of(value)
    if binding is None:
        raise ValueError("workflow execution value is not Run-bound")
    return binding


__all__ = [
    "WorkflowRunBinding",
    "binding_of",
    "require_run_binding",
    "select_run_binding",
    "validate_run_binding",
]
