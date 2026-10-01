"""Direct Run/Manifest identity validation for execution contracts.

The binding is carried by the state, event, request, and runtime-result values
that own it.  This module contains only the small value-level validation and
copy helpers; it is not a second durable authority or a binding sidecar.
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
    run_input: str | None = None

    def __post_init__(self) -> None:
        validate_run_binding(self.run_id, self.manifest_digest, self.run_input)

    @property
    def identity(self) -> tuple[str, str, str | None]:
        """Return the complete immutable Run/Manifest value identity."""
        return self.run_id, self.manifest_digest, self.run_input


def validate_run_binding(
    run_id: object,
    manifest_digest: object,
    run_input: object = None,
) -> None:
    """Validate an optional complete Run binding without coercion."""
    if type(run_id) is not str or _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("workflow Run identity is invalid")
    if (
        type(manifest_digest) is not str
        or _MANIFEST_DIGEST_PATTERN.fullmatch(manifest_digest) is None
    ):
        raise ValueError("workflow Run Manifest identity is invalid")
    if run_input is not None and type(run_input) is not str:
        raise TypeError("workflow Run input must be a string")


def validate_optional_run_binding(
    run_id: object,
    manifest_digest: object,
    run_input: object = None,
) -> None:
    """Allow the legacy unbound in-memory shape, but reject partial binding."""
    if run_id is None and manifest_digest is None:
        if run_input is not None:
            raise ValueError("workflow Run input is unbound")
        return
    if run_id is None or manifest_digest is None:
        raise ValueError("workflow Run binding is incomplete")
    validate_run_binding(run_id, manifest_digest, run_input)


def attach_run_binding(
    value: object,
    binding: WorkflowRunBinding,
) -> object:
    """Attach identity directly to an immutable execution value.

    State/event/request/result classes expose these attributes through their
    public properties.  No separate mutable binding record is consulted.
    """
    if type(binding) is not WorkflowRunBinding:
        raise TypeError("workflow Run binding is invalid")
    object.__setattr__(value, "_run_id", binding.run_id)
    object.__setattr__(value, "_manifest_digest", binding.manifest_digest)
    object.__setattr__(value, "_run_input", binding.run_input)
    return value


def binding_of(value: object) -> WorkflowRunBinding | None:
    """Read the direct identity carried by one execution value."""
    run_id = getattr(value, "_run_id", None)
    manifest_digest = getattr(value, "_manifest_digest", None)
    run_input = getattr(value, "_run_input", None)
    if run_id is None and manifest_digest is None:
        if run_input is not None:
            raise ValueError("workflow Run input is unbound")
        return None
    if run_id is None or manifest_digest is None:
        raise ValueError("workflow Run binding is incomplete")
    return WorkflowRunBinding(run_id, manifest_digest, run_input)


def bind_run_value(value: object, binding: WorkflowRunBinding) -> object:
    """Compatibility spelling for the direct-value attachment helper."""
    return attach_run_binding(value, binding)


def require_run_binding(value: object) -> WorkflowRunBinding:
    """Reject old Run-less values at a Run-owned operation boundary."""
    binding = binding_of(value)
    if binding is None:
        raise ValueError("workflow execution value is not Run-bound")
    return binding


def bindings_match(left: object, right: object) -> bool:
    """Return true only for two complete, exact Run/Manifest identities."""
    try:
        left_binding = binding_of(left)
        right_binding = binding_of(right)
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        left_binding is not None
        and right_binding is not None
        and left_binding.identity == right_binding.identity
    )


def run_binding_of(value: object) -> WorkflowRunBinding | None:
    """Explicit alias useful to callers that want to name the boundary."""
    return binding_of(value)


__all__ = [
    "WorkflowRunBinding",
    "attach_run_binding",
    "bind_run_value",
    "binding_of",
    "bindings_match",
    "require_run_binding",
    "run_binding_of",
    "validate_optional_run_binding",
    "validate_run_binding",
]
