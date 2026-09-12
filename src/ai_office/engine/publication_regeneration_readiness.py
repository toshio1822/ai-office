"""Provider-free, read-only publication readiness for regeneration evidence.

Phase 268 reads one strict Phase 267 result sidecar and one strict Phase 263
readiness-audit sidecar.  It binds the regenerated output to the source
``PostTerminalFacts`` through a dedicated new-lineage claim validator.  The
Phase 262 original-lineage readiness boundary is intentionally not used as the
final authority here: its ``final_output_sha256`` identity belongs only to the
original workflow terminal output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.post_terminal_facts import (
    PostTerminalFacts,
    PublicationClaimContract,
    PublicationReadinessAuditRecord,
    load_publication_readiness_audit,
    post_terminal_facts_digest,
    publication_claim_contract_digest,
    publication_readiness_audit_digest,
    serialize_post_terminal_facts_canonical,
    serialize_publication_claim_contract_canonical,
)
from ai_office.engine.publication_regeneration_result import (
    PublicationRegenerationResultRecord,
    load_publication_regeneration_result,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess

_READINESS_ERROR_MESSAGE = "publication regeneration readiness is invalid"
_READINESS_SCHEMA_VERSION = "publication-regeneration-readiness.v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_READINESS_VALUES = {
    "ready",
    "insufficient_evidence",
    "stale_or_inconsistent",
    "result_failure",
}
_SUCCESS_READINESS_VALUES = {
    "ready",
    "insufficient_evidence",
    "stale_or_inconsistent",
}
_FAILURE_REASON_CODES = ("regeneration_result_failure",)
_CLAIM_MISMATCH_CLASSIFICATIONS = frozenset(
    {
        "source_terminal_status",
        "workflow_mismatch",
        "business_output_mismatch",
        "post_terminal_facts_mismatch",
        "terminal_status_mismatch",
    }
)


@dataclass(frozen=True)
class PublicationRegenerationReadinessFailureDetail:
    """Safe classification for an invalid readiness projection."""

    classification: str


class PublicationRegenerationReadinessError(ValueError):
    """Raised when regeneration readiness evidence cannot be bound safely."""

    def __init__(self, classification: str = "readiness") -> None:
        super().__init__(_READINESS_ERROR_MESSAGE)
        self.detail = PublicationRegenerationReadinessFailureDetail(classification)


@dataclass(frozen=True)
class PublicationRegenerationReadinessAssessment:
    """Immutable readiness identity for one regenerated output lineage."""

    schema_version: Literal["publication-regeneration-readiness.v1"]
    regeneration_id: str
    result_record_sha256: str
    source_audit_sha256: str
    source_post_terminal_facts: PostTerminalFacts
    source_post_terminal_facts_sha256: str
    outcome: Literal["success", "failure"]
    business_output_sha256: str | None
    evaluated_claim_contract: PublicationClaimContract | None
    claim_contract_sha256: str | None
    readiness: Literal[
        "ready",
        "insufficient_evidence",
        "stale_or_inconsistent",
        "result_failure",
    ]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_publication_regeneration_readiness(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical wrapper JSON."""
        return publication_regeneration_readiness_assessment_digest(self)


def validate_publication_regeneration_claim_contract(
    contract: PublicationClaimContract,
    source_post_terminal_facts: PostTerminalFacts,
    regenerated_business_output_sha256: str,
) -> None:
    """Validate a caller claim against regenerated output and source facts.

    This validator intentionally differs from Phase 262's original-lineage
    ``validate_publication_claim_contract``: the source facts' final-output
    digest identifies the original terminal output and is not compared with
    the regenerated output digest here.
    """
    if type(contract) is not PublicationClaimContract:
        _raise_readiness("claim_contract_type")
    if type(source_post_terminal_facts) is not PostTerminalFacts:
        _raise_readiness("source_facts_type")
    try:
        serialize_publication_claim_contract_canonical(contract)
    except Exception:
        _raise_readiness("claim_contract")
    try:
        source_facts_sha256 = post_terminal_facts_digest(source_post_terminal_facts)
    except Exception:
        _raise_readiness("source_facts")
    _validate_digest(regenerated_business_output_sha256, "business_output_digest")

    if source_post_terminal_facts.terminal_status != "workflow_complete":
        _raise_readiness("source_terminal_status")
    if contract.workflow_id != source_post_terminal_facts.workflow_id:
        _raise_readiness("workflow_mismatch")
    if contract.business_output_sha256 != regenerated_business_output_sha256:
        _raise_readiness("business_output_mismatch")
    if contract.post_terminal_facts_sha256 != source_facts_sha256:
        _raise_readiness("post_terminal_facts_mismatch")
    if contract.asserted_terminal_status != source_post_terminal_facts.terminal_status:
        _raise_readiness("terminal_status_mismatch")


def assess_publication_regeneration_result_readiness(
    *,
    result_path: Path,
    source_audit_path: Path,
    claim_contract: PublicationClaimContract | None = None,
) -> PublicationRegenerationReadinessAssessment:
    """Assess one durable regeneration result without providers or writes.

    The result and source audit are strict-loaded in that order.  A success
    result contributes only its exact ``result.text`` and digest; the source
    audit's immutable ``PostTerminalFacts`` are the only runtime evidence.  A
    failure result is represented as ``result_failure`` and never evaluates a
    claim.
    """
    result_record = _load_result(result_path)
    source_audit = _load_source_audit(source_audit_path)
    source_audit_sha256 = publication_readiness_audit_digest(source_audit)
    if result_record.source_audit_sha256 != source_audit_sha256:
        _raise_readiness("source_audit_mismatch")

    facts = source_audit.post_terminal_facts
    facts_sha256 = post_terminal_facts_digest(facts)
    if (
        claim_contract is not None
        and type(claim_contract) is not PublicationClaimContract
    ):
        _raise_readiness("claim_contract_type")

    if type(result_record.result) is ModelInvocationFailure:
        if claim_contract is not None:
            _raise_readiness("claim_contract_inapplicable")
        return _build_assessment(
            result_record=result_record,
            source_audit_sha256=source_audit_sha256,
            source_post_terminal_facts=facts,
            source_post_terminal_facts_sha256=facts_sha256,
            business_output_sha256=None,
            evaluated_claim_contract=None,
            readiness="result_failure",
            reason_codes=_FAILURE_REASON_CODES,
        )

    if type(result_record.result) is not ModelInvocationSuccess:
        _raise_readiness("result_type")
    if result_record.outcome != "success":
        _raise_readiness("result_outcome")

    regenerated_business_output_sha256 = _text_digest(result_record.result.text)
    if result_record.business_output_sha256 != regenerated_business_output_sha256:
        _raise_readiness("business_output_binding")

    if claim_contract is None:
        return _build_assessment(
            result_record=result_record,
            source_audit_sha256=source_audit_sha256,
            source_post_terminal_facts=facts,
            source_post_terminal_facts_sha256=facts_sha256,
            business_output_sha256=regenerated_business_output_sha256,
            evaluated_claim_contract=None,
            readiness="insufficient_evidence",
            reason_codes=("claim_contract_missing",),
        )

    _claim_digest(claim_contract)
    try:
        validate_publication_regeneration_claim_contract(
            claim_contract,
            facts,
            regenerated_business_output_sha256,
        )
    except PublicationRegenerationReadinessError as error:
        if error.detail.classification not in _CLAIM_MISMATCH_CLASSIFICATIONS:
            raise
        return _build_assessment(
            result_record=result_record,
            source_audit_sha256=source_audit_sha256,
            source_post_terminal_facts=facts,
            source_post_terminal_facts_sha256=facts_sha256,
            business_output_sha256=regenerated_business_output_sha256,
            evaluated_claim_contract=claim_contract,
            readiness="stale_or_inconsistent",
            reason_codes=("claim_contract_mismatch",),
        )

    return _build_assessment(
        result_record=result_record,
        source_audit_sha256=source_audit_sha256,
        source_post_terminal_facts=facts,
        source_post_terminal_facts_sha256=facts_sha256,
        business_output_sha256=regenerated_business_output_sha256,
        evaluated_claim_contract=claim_contract,
        readiness="ready",
        reason_codes=(),
    )


def serialize_publication_regeneration_readiness_assessment_canonical(
    assessment: PublicationRegenerationReadinessAssessment,
) -> str:
    """Serialize one regeneration-readiness wrapper as canonical JSON."""
    _validate_publication_regeneration_readiness(assessment)
    try:
        value = {
            "business_output_sha256": assessment.business_output_sha256,
            "claim_contract_sha256": assessment.claim_contract_sha256,
            "evaluated_claim_contract": (
                json.loads(
                    serialize_publication_claim_contract_canonical(
                        assessment.evaluated_claim_contract
                    )
                )
                if assessment.evaluated_claim_contract is not None
                else None
            ),
            "outcome": assessment.outcome,
            "readiness": assessment.readiness,
            "reason_codes": list(assessment.reason_codes),
            "regeneration_id": assessment.regeneration_id,
            "result_record_sha256": assessment.result_record_sha256,
            "schema_version": assessment.schema_version,
            "source_audit_sha256": assessment.source_audit_sha256,
            "source_post_terminal_facts": json.loads(
                serialize_post_terminal_facts_canonical(
                    assessment.source_post_terminal_facts
                )
            ),
            "source_post_terminal_facts_sha256": (
                assessment.source_post_terminal_facts_sha256
            ),
        }
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        _raise_readiness("serialization")


def publication_regeneration_readiness_assessment_canonical_bytes(
    assessment: PublicationRegenerationReadinessAssessment,
) -> bytes:
    """Return canonical wrapper JSON encoded as UTF-8 bytes."""
    return serialize_publication_regeneration_readiness_assessment_canonical(
        assessment
    ).encode("utf-8")


def publication_regeneration_readiness_assessment_digest(
    assessment: PublicationRegenerationReadinessAssessment,
) -> str:
    """Return the SHA-256 digest of canonical wrapper JSON bytes."""
    return sha256(
        publication_regeneration_readiness_assessment_canonical_bytes(assessment)
    ).hexdigest()


def _build_assessment(
    *,
    result_record: PublicationRegenerationResultRecord,
    source_audit_sha256: str,
    source_post_terminal_facts: PostTerminalFacts,
    source_post_terminal_facts_sha256: str,
    business_output_sha256: str | None,
    evaluated_claim_contract: PublicationClaimContract | None,
    readiness: Literal[
        "ready",
        "insufficient_evidence",
        "stale_or_inconsistent",
        "result_failure",
    ],
    reason_codes: tuple[str, ...],
) -> PublicationRegenerationReadinessAssessment:
    return PublicationRegenerationReadinessAssessment(
        schema_version=_READINESS_SCHEMA_VERSION,
        regeneration_id=result_record.regeneration_id,
        result_record_sha256=result_record.digest,
        source_audit_sha256=source_audit_sha256,
        source_post_terminal_facts=source_post_terminal_facts,
        source_post_terminal_facts_sha256=source_post_terminal_facts_sha256,
        outcome=result_record.outcome,
        business_output_sha256=business_output_sha256,
        evaluated_claim_contract=evaluated_claim_contract,
        claim_contract_sha256=(
            _claim_digest(evaluated_claim_contract)
            if evaluated_claim_contract is not None
            else None
        ),
        readiness=readiness,
        reason_codes=reason_codes,
    )


def _load_result(path: Path) -> PublicationRegenerationResultRecord:
    try:
        result = load_publication_regeneration_result(path)
    except Exception:
        _raise_readiness("result_load")
    if type(result) is not PublicationRegenerationResultRecord:
        _raise_readiness("result_type")
    return result


def _load_source_audit(path: Path) -> PublicationReadinessAuditRecord:
    try:
        audit = load_publication_readiness_audit(path)
    except Exception:
        _raise_readiness("source_audit_load")
    if type(audit) is not PublicationReadinessAuditRecord:
        _raise_readiness("source_audit_type")
    return audit


def _claim_digest(contract: PublicationClaimContract) -> str:
    try:
        return publication_claim_contract_digest(contract)
    except Exception:
        _raise_readiness("claim_contract")


def _validate_publication_regeneration_readiness(
    assessment: PublicationRegenerationReadinessAssessment,
) -> None:
    if type(assessment) is not PublicationRegenerationReadinessAssessment:
        _raise_readiness("assessment_type")
    if (
        type(assessment.schema_version) is not str
        or assessment.schema_version != _READINESS_SCHEMA_VERSION
    ):
        _raise_readiness("schema_version")
    if type(assessment.regeneration_id) is not str or not assessment.regeneration_id:
        _raise_readiness("regeneration_id")
    for value, classification in (
        (assessment.result_record_sha256, "result_record_digest"),
        (assessment.source_audit_sha256, "source_audit_digest"),
        (
            assessment.source_post_terminal_facts_sha256,
            "source_facts_digest",
        ),
    ):
        _validate_digest(value, classification)
    if type(assessment.source_post_terminal_facts) is not PostTerminalFacts:
        _raise_readiness("source_facts_type")
    try:
        facts_digest = post_terminal_facts_digest(assessment.source_post_terminal_facts)
    except Exception:
        _raise_readiness("source_facts")
    if assessment.source_post_terminal_facts_sha256 != facts_digest:
        _raise_readiness("source_facts_binding")
    if type(assessment.outcome) is not str or assessment.outcome not in {
        "success",
        "failure",
    }:
        _raise_readiness("outcome")
    if (
        type(assessment.readiness) is not str
        or assessment.readiness not in _READINESS_VALUES
    ):
        _raise_readiness("readiness")
    if type(assessment.reason_codes) is not tuple or any(
        type(reason) is not str or not reason for reason in assessment.reason_codes
    ):
        _raise_readiness("reason_codes")
    if assessment.business_output_sha256 is not None:
        _validate_digest(assessment.business_output_sha256, "business_output_digest")
    if assessment.evaluated_claim_contract is not None:
        if type(assessment.evaluated_claim_contract) is not PublicationClaimContract:
            _raise_readiness("claim_contract_type")
        _claim_digest(assessment.evaluated_claim_contract)
    if assessment.claim_contract_sha256 is not None:
        _validate_digest(assessment.claim_contract_sha256, "claim_contract_digest")
    if assessment.evaluated_claim_contract is None:
        if assessment.claim_contract_sha256 is not None:
            _raise_readiness("claim_contract_binding")
    elif assessment.claim_contract_sha256 != _claim_digest(
        assessment.evaluated_claim_contract
    ):
        _raise_readiness("claim_contract_binding")

    if assessment.outcome == "failure":
        if (
            assessment.business_output_sha256 is not None
            or assessment.evaluated_claim_contract is not None
            or assessment.claim_contract_sha256 is not None
            or assessment.readiness != "result_failure"
            or assessment.reason_codes != _FAILURE_REASON_CODES
        ):
            _raise_readiness("failure_binding")
        return

    if assessment.business_output_sha256 is None:
        _raise_readiness("success_business_output")
    if assessment.readiness not in _SUCCESS_READINESS_VALUES:
        _raise_readiness("success_readiness")
    if assessment.readiness == "ready":
        if (
            assessment.reason_codes != ()
            or assessment.evaluated_claim_contract is None
            or assessment.claim_contract_sha256 is None
        ):
            _raise_readiness("ready_without_claim")
        try:
            validate_publication_regeneration_claim_contract(
                assessment.evaluated_claim_contract,
                assessment.source_post_terminal_facts,
                assessment.business_output_sha256,
            )
        except Exception:
            _raise_readiness("ready_claim_contract_binding")
    elif assessment.readiness == "insufficient_evidence":
        if (
            assessment.reason_codes != ("claim_contract_missing",)
            or assessment.evaluated_claim_contract is not None
            or assessment.claim_contract_sha256 is not None
        ):
            _raise_readiness("insufficient_evidence_binding")
    elif assessment.readiness == "stale_or_inconsistent":
        if (
            assessment.reason_codes != ("claim_contract_mismatch",)
            or assessment.evaluated_claim_contract is None
            or assessment.claim_contract_sha256 is None
        ):
            _raise_readiness("stale_claim_binding")
        try:
            validate_publication_regeneration_claim_contract(
                assessment.evaluated_claim_contract,
                assessment.source_post_terminal_facts,
                assessment.business_output_sha256,
            )
        except PublicationRegenerationReadinessError as error:
            if error.detail.classification not in _CLAIM_MISMATCH_CLASSIFICATIONS:
                _raise_readiness("stale_claim_binding")
        else:
            _raise_readiness("stale_claim_binding")


def _validate_digest(value: object, classification: str) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        _raise_readiness(classification)


def _text_digest(value: str) -> str:
    if type(value) is not str:
        _raise_readiness("business_output_type")
    return sha256(value.encode("utf-8")).hexdigest()


def _raise_readiness(classification: str) -> NoReturn:
    raise PublicationRegenerationReadinessError(classification) from None


__all__ = [
    "PublicationRegenerationReadinessAssessment",
    "PublicationRegenerationReadinessError",
    "PublicationRegenerationReadinessFailureDetail",
    "assess_publication_regeneration_result_readiness",
    "publication_regeneration_readiness_assessment_canonical_bytes",
    "publication_regeneration_readiness_assessment_digest",
    "serialize_publication_regeneration_readiness_assessment_canonical",
    "validate_publication_regeneration_claim_contract",
]
