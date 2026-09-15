"""Read-only lineage reconciliation for durable external publication records.

Phase 283 compares one strict Phase 280 attempt claim with one strict Phase
282 execution-evidence record.  It does not inspect either sidecar itself or
perform any publication, persistence, repair, retry, or external-state work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.external_publication import (
    ExternalPublicationAttemptClaim,
    external_publication_attempt_claim_digest,
    load_external_publication_attempt_claim,
)
from ai_office.engine.external_publication_execution import (
    ExternalPublicationExecutionResult,
)
from ai_office.engine.external_publication_execution_evidence import (
    external_publication_execution_result_digest,
    load_external_publication_execution_result,
)

_RECONCILIATION_ERROR_MESSAGE = "external publication execution reconciliation failed"
_RECONCILIATION_SCHEMA_VERSION = "external-publication-execution-reconciliation.v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_LINEAGE_FIELDS = (
    "publication_attempt_claim_sha256",
    "regeneration_id",
    "publication_plan_sha256",
    "publication_approval_sha256",
    "business_output_sha256",
    "output_byte_length",
    "provider",
    "publication_target_sha256",
)

ExternalPublicationExecutionLineageField = Literal[
    "publication_attempt_claim_sha256",
    "regeneration_id",
    "publication_plan_sha256",
    "publication_approval_sha256",
    "business_output_sha256",
    "output_byte_length",
    "provider",
    "publication_target_sha256",
]

Classification = Literal[
    "path_type",
    "claim",
    "claim_digest",
    "execution_evidence",
    "execution_evidence_digest",
    "result",
]


@dataclass(frozen=True)
class ExternalPublicationExecutionReconciliationFailureDetail:
    """Detail-safe classification for one reconciliation failure."""

    classification: Classification


class ExternalPublicationExecutionReconciliationError(ValueError):
    """Raised when a safe reconciliation observation cannot be completed."""

    def __init__(self, classification: Classification = "result") -> None:
        super().__init__(_RECONCILIATION_ERROR_MESSAGE)
        self.detail = ExternalPublicationExecutionReconciliationFailureDetail(
            classification
        )


@dataclass(frozen=True)
class ExternalPublicationExecutionReconciliation:
    """One immutable in-memory observation of durable publication lineage."""

    schema_version: Literal["external-publication-execution-reconciliation.v1"]
    claim_sha256: str
    execution_evidence_sha256: str
    status: Literal["matched", "lineage_mismatch"]
    mismatched_fields: tuple[ExternalPublicationExecutionLineageField, ...]

    def __post_init__(self) -> None:
        _validate_result(self)


def reconcile_external_publication_execution(
    *,
    claim_path: Path,
    execution_evidence_path: Path,
) -> ExternalPublicationExecutionReconciliation:
    """Reconcile one strict durable claim with one strict execution record.

    The two strict loaders and their digest helpers are each called once, in
    claim-then-evidence order.  Only their returned objects and the exact
    eight shared lineage bindings are observed; no sidecar is opened here.
    """
    _validate_path_types(claim_path, execution_evidence_path)

    try:
        claim = load_external_publication_attempt_claim(claim_path)
    except Exception:
        _raise_reconciliation("claim")
    if type(claim) is not ExternalPublicationAttemptClaim:
        _raise_reconciliation("claim")

    try:
        claim_sha256 = external_publication_attempt_claim_digest(claim)
    except Exception:
        _raise_reconciliation("claim_digest")
    if not _is_sha256(claim_sha256):
        _raise_reconciliation("claim_digest")

    try:
        execution_evidence = load_external_publication_execution_result(
            execution_evidence_path
        )
    except Exception:
        _raise_reconciliation("execution_evidence")
    if type(execution_evidence) is not ExternalPublicationExecutionResult:
        _raise_reconciliation("execution_evidence")

    try:
        execution_evidence_sha256 = external_publication_execution_result_digest(
            execution_evidence
        )
    except Exception:
        _raise_reconciliation("execution_evidence_digest")
    if not _is_sha256(execution_evidence_sha256):
        _raise_reconciliation("execution_evidence_digest")

    try:
        mismatched_fields = _mismatched_lineage_fields(
            claim,
            claim_sha256,
            execution_evidence,
        )
        status: Literal["matched", "lineage_mismatch"] = (
            "matched" if not mismatched_fields else "lineage_mismatch"
        )
        return ExternalPublicationExecutionReconciliation(
            schema_version=_RECONCILIATION_SCHEMA_VERSION,
            claim_sha256=claim_sha256,
            execution_evidence_sha256=execution_evidence_sha256,
            status=status,
            mismatched_fields=mismatched_fields,
        )
    except ExternalPublicationExecutionReconciliationError:
        raise
    except Exception:
        _raise_reconciliation("result")


def _mismatched_lineage_fields(
    claim: ExternalPublicationAttemptClaim,
    claim_sha256: str,
    execution_evidence: ExternalPublicationExecutionResult,
) -> tuple[ExternalPublicationExecutionLineageField, ...]:
    comparisons: tuple[
        tuple[ExternalPublicationExecutionLineageField, object, object],
        ...,
    ] = (
        (
            "publication_attempt_claim_sha256",
            claim_sha256,
            execution_evidence.publication_attempt_claim_sha256,
        ),
        (
            "regeneration_id",
            claim.regeneration_id,
            execution_evidence.regeneration_id,
        ),
        (
            "publication_plan_sha256",
            claim.publication_plan_sha256,
            execution_evidence.publication_plan_sha256,
        ),
        (
            "publication_approval_sha256",
            claim.publication_approval_sha256,
            execution_evidence.publication_approval_sha256,
        ),
        (
            "business_output_sha256",
            claim.business_output_sha256,
            execution_evidence.business_output_sha256,
        ),
        (
            "output_byte_length",
            claim.output_byte_length,
            execution_evidence.output_byte_length,
        ),
        (
            "provider",
            claim.provider,
            execution_evidence.provider,
        ),
        (
            "publication_target_sha256",
            claim.publication_target_sha256,
            execution_evidence.publication_target_sha256,
        ),
    )
    return tuple(
        field
        for field, claim_value, evidence_value in comparisons
        if claim_value != evidence_value
    )


def _validate_path_types(
    claim_path: object,
    execution_evidence_path: object,
) -> None:
    if (
        type(claim_path) is not _PATH_TYPE
        or type(execution_evidence_path) is not _PATH_TYPE
    ):
        _raise_reconciliation("path_type")


def _validate_result(
    result: object,
) -> None:
    if type(result) is not ExternalPublicationExecutionReconciliation:
        _raise_reconciliation("result")
    try:
        if (
            type(result.schema_version) is not str
            or result.schema_version != _RECONCILIATION_SCHEMA_VERSION
            or not _is_sha256(result.claim_sha256)
            or not _is_sha256(result.execution_evidence_sha256)
            or type(result.status) is not str
            or result.status not in {"matched", "lineage_mismatch"}
            or type(result.mismatched_fields) is not tuple
        ):
            _raise_reconciliation("result")
        for field in result.mismatched_fields:
            if type(field) is not str or field not in _LINEAGE_FIELDS:
                _raise_reconciliation("result")
        if len(set(result.mismatched_fields)) != len(result.mismatched_fields):
            _raise_reconciliation("result")

        canonical_fields = tuple(
            field for field in _LINEAGE_FIELDS if field in result.mismatched_fields
        )
        if result.mismatched_fields != canonical_fields:
            _raise_reconciliation("result")
        if result.status == "matched" and result.mismatched_fields != ():
            _raise_reconciliation("result")
        if result.status == "lineage_mismatch" and not result.mismatched_fields:
            _raise_reconciliation("result")
    except ExternalPublicationExecutionReconciliationError:
        raise
    except Exception:
        _raise_reconciliation("result")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _raise_reconciliation(classification: Classification) -> NoReturn:
    raise ExternalPublicationExecutionReconciliationError(classification) from None


__all__ = [
    "ExternalPublicationExecutionLineageField",
    "ExternalPublicationExecutionReconciliation",
    "ExternalPublicationExecutionReconciliationError",
    "ExternalPublicationExecutionReconciliationFailureDetail",
    "reconcile_external_publication_execution",
]
