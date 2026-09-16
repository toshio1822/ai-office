"""Provider-free post-execution reconciliation closure.

Phase 286 composes the public Phase 283 read-only reconciliation boundary
with the public Phase 284 reconciliation-evidence persistence boundary.  It
owns neither predecessor loading nor evidence serialization and has no
provider, workflow, or filesystem boundary of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.external_publication_execution_reconciliation import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
    reconcile_external_publication_execution,
)
from ai_office.engine.external_publication_execution_reconciliation_evidence import (
    ExternalPublicationExecutionReconciliationEvidenceError,
    persist_external_publication_execution_reconciliation,
)

_ORCHESTRATION_ERROR_MESSAGE = (
    "external publication execution reconciliation orchestration is blocked"
)
Phase283Function = Callable[..., ExternalPublicationExecutionReconciliation]
Phase284PersistenceFunction = Callable[
    [Path, ExternalPublicationExecutionReconciliation], None
]

Classification = Literal[
    "configuration",
    "reconciliation_contract",
    "persistence_contract",
    "result_mutation",
    "dependency_error",
]


@dataclass(frozen=True)
class ExternalPublicationExecutionReconciliationOrchestrationFailureDetail:
    """Detail-safe classification for one Phase 286 failure."""

    classification: Classification


class ExternalPublicationExecutionReconciliationOrchestrationError(ValueError):
    """Raised when the Phase 283/284 composition cannot safely complete."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_ORCHESTRATION_ERROR_MESSAGE)
        self.detail = (
            ExternalPublicationExecutionReconciliationOrchestrationFailureDetail(
                classification
            )
        )


class ExternalPublicationExecutionReconciliationOrchestrationCompatibilityError(
    ExternalPublicationExecutionReconciliationOrchestrationError
):
    """Raised when the Phase 286 orchestration contract is incompatible."""


def reconcile_and_persist_external_publication_execution(
    *,
    claim_path: Path,
    execution_evidence_path: Path,
    reconciliation_evidence_path: Path,
    phase283_function: Phase283Function = reconcile_external_publication_execution,
    phase284_persistence_function: Phase284PersistenceFunction = (
        persist_external_publication_execution_reconciliation
    ),
) -> ExternalPublicationExecutionReconciliation:
    """Reconcile and persist one exact post-execution observation once.

    The route is deliberately linear: one Phase 283 call, an exact result
    snapshot, one Phase 284 persistence call, an exact ``None`` return check,
    and the unchanged Phase 283 object.  Known lower-boundary errors remain
    authoritative and are propagated by identity; no retry or compensation
    is attempted.
    """
    if not callable(phase283_function) or not callable(phase284_persistence_function):
        _raise_orchestration("configuration")

    try:
        reconciliation = phase283_function(
            claim_path=claim_path,
            execution_evidence_path=execution_evidence_path,
        )
    except ExternalPublicationExecutionReconciliationError:
        raise
    except Exception:
        _raise_orchestration("dependency_error")

    before_persistence = _snapshot_and_validate(
        reconciliation, "reconciliation_contract"
    )

    try:
        persisted = phase284_persistence_function(
            reconciliation_evidence_path,
            reconciliation,
        )
    except ExternalPublicationExecutionReconciliationEvidenceError:
        raise
    except Exception:
        _raise_orchestration("dependency_error")

    if persisted is not None:
        _raise_orchestration("persistence_contract")

    after_persistence = _snapshot_and_validate(reconciliation, "result_mutation")
    if after_persistence != before_persistence:
        _raise_orchestration("result_mutation")
    return reconciliation


def _snapshot_and_validate(
    reconciliation: object,
    classification: Classification,
) -> tuple[object, ...]:
    """Validate the exact model and snapshot all five authoritative fields."""
    if type(reconciliation) is not ExternalPublicationExecutionReconciliation:
        _raise_orchestration(classification)
    try:
        snapshot = (
            reconciliation.schema_version,  # type: ignore[union-attr]
            reconciliation.claim_sha256,  # type: ignore[union-attr]
            reconciliation.execution_evidence_sha256,  # type: ignore[union-attr]
            reconciliation.status,  # type: ignore[union-attr]
            reconciliation.mismatched_fields,  # type: ignore[union-attr]
        )
        # Validate the snapshot without replacing the caller-owned object.
        ExternalPublicationExecutionReconciliation(
            schema_version=snapshot[0],
            claim_sha256=snapshot[1],
            execution_evidence_sha256=snapshot[2],
            status=snapshot[3],
            mismatched_fields=snapshot[4],
        )
        return snapshot
    except Exception:
        _raise_orchestration(classification)


def _raise_orchestration(classification: Classification) -> NoReturn:
    raise ExternalPublicationExecutionReconciliationOrchestrationCompatibilityError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationExecutionReconciliationOrchestrationCompatibilityError",
    "ExternalPublicationExecutionReconciliationOrchestrationError",
    "ExternalPublicationExecutionReconciliationOrchestrationFailureDetail",
    "reconcile_and_persist_external_publication_execution",
]
