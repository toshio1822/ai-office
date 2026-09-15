"""Exactly-once external publication execution after a durable claim.

Phase 281 is the first external side-effect boundary for the publication
lineage.  It revalidates the exact Phase 278 plan and Phase 279 approval,
preflights the exact exported bytes, creates the Phase 280 claim as the final
write-ahead gate, invokes a caller-owned transport once, and returns only an
in-memory result.  Provider authentication and network behavior remain inside
the supplied transport.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.external_publication import (
    ExternalPublicationApproval,
    ExternalPublicationAttemptClaim,
    ExternalPublicationAttemptClaimError,
    ExternalPublicationPlan,
    ExternalPublicationTarget,
    build_external_publication_attempt_claim,
    claim_external_publication_attempt,
    external_publication_attempt_claim_digest,
    validate_external_publication_approval,
    validate_external_publication_plan,
)

_EXECUTION_ERROR_MESSAGE = "external publication execution is blocked"
_AMBIGUOUS_ERROR_MESSAGE = "external publication execution outcome is ambiguous"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PATH_TYPE = type(Path())
_MAX_PUBLICATION_ID_LENGTH = 512
_MAX_REGENERATION_ID_LENGTH = 128
_MAX_PROVIDER_LENGTH = 128


@dataclass(frozen=True)
class ExternalPublicationExecutionFailureDetail:
    """Detail-safe classification for a blocked execution boundary."""

    classification: str


class ExternalPublicationExecutionError(ValueError):
    """Raised when preflight or result validation blocks publication."""

    _message = _EXECUTION_ERROR_MESSAGE

    def __init__(self, classification: str = "execution") -> None:
        super().__init__(self._message)
        self.detail = ExternalPublicationExecutionFailureDetail(classification)


class ExternalPublicationExecutionAmbiguousError(ExternalPublicationExecutionError):
    """Raised when the transport outcome may already have happened."""

    _message = _AMBIGUOUS_ERROR_MESSAGE


@dataclass(frozen=True)
class ExternalPublicationTransportReceipt:
    """Provider-neutral, secret-free receipt returned by one transport call."""

    publication_id: str

    def __post_init__(self) -> None:
        _validate_transport_receipt(self)


type ExternalPublicationTransport = Callable[
    [ExternalPublicationTarget, bytes], ExternalPublicationTransportReceipt
]


@dataclass(frozen=True)
class ExternalPublicationExecutionResult:
    """Secret-free in-memory result of one published external output."""

    schema_version: Literal["external-publication-execution-result.v1"]
    regeneration_id: str
    publication_attempt_claim_sha256: str
    publication_plan_sha256: str
    publication_approval_sha256: str
    business_output_sha256: str
    output_byte_length: int
    provider: str
    publication_target_sha256: str
    publication_id: str
    status: Literal["published"]

    def __post_init__(self) -> None:
        _validate_execution_result(self)


def execute_approved_external_publication(
    *,
    reconciliation_evidence_path: Path,
    output_path: Path,
    ledger_directory: Path,
    plan: ExternalPublicationPlan,
    approval: ExternalPublicationApproval,
    target: ExternalPublicationTarget,
    transport: ExternalPublicationTransport,
) -> ExternalPublicationExecutionResult:
    """Execute one exact approved publication behind the durable claim gate.

    The sequence is intentionally linear and bounded: fresh plan validation,
    approval validation, expected claim identity, one output read, transport
    preflight, one durable claim creation, and one transport call.  No result
    or receipt is persisted here, and no claim is loaded or compensated after
    the claim boundary.
    """
    _fresh_validate_plan(
        plan,
        reconciliation_evidence_path=reconciliation_evidence_path,
        target=target,
    )
    _fresh_validate_approval(plan, approval)
    expected_claim, expected_claim_sha256 = _build_expected_claim(plan, approval)
    output_bytes = _read_verified_output(output_path, plan)

    if not callable(transport):
        _raise_execution("transport")

    try:
        claimed = claim_external_publication_attempt(
            ledger_directory,
            plan,
            approval,
        )
    except ExternalPublicationAttemptClaimError:
        # Phase 280 has the authoritative fixed/detail-safe claim errors.  In
        # particular, already-consumed and ambiguous persistence remain
        # observable and must not reach transport.
        raise
    except Exception:
        _raise_execution("claim")

    if type(claimed) is not ExternalPublicationAttemptClaim:
        _raise_execution("claim")
    if claimed != expected_claim:
        _raise_execution("claim")

    try:
        receipt = transport(target, output_bytes)
    except Exception:
        # The provider may have accepted the request before the adapter raised;
        # the successful Phase 280 claim is therefore deliberately retained.
        _raise_ambiguous("transport_execution")

    try:
        _validate_transport_receipt(receipt)
    except ExternalPublicationExecutionError:
        _raise_ambiguous("transport_receipt")
    except Exception:
        _raise_ambiguous("transport_receipt")

    try:
        return ExternalPublicationExecutionResult(
            schema_version="external-publication-execution-result.v1",
            regeneration_id=plan.regeneration_id,
            publication_attempt_claim_sha256=expected_claim_sha256,
            publication_plan_sha256=expected_claim.publication_plan_sha256,
            publication_approval_sha256=(expected_claim.publication_approval_sha256),
            business_output_sha256=plan.business_output_sha256,
            output_byte_length=plan.output_byte_length,
            provider=plan.provider,
            publication_target_sha256=plan.publication_target_sha256,
            publication_id=receipt.publication_id,
            status="published",
        )
    except ExternalPublicationExecutionError:
        _raise_execution("result")
    except Exception:
        _raise_execution("result")


def _fresh_validate_plan(
    plan: ExternalPublicationPlan,
    *,
    reconciliation_evidence_path: Path,
    target: ExternalPublicationTarget,
) -> None:
    try:
        validate_external_publication_plan(
            plan,
            reconciliation_evidence_path=reconciliation_evidence_path,
            target=target,
        )
    except Exception:
        _raise_execution("plan")


def _fresh_validate_approval(
    plan: ExternalPublicationPlan,
    approval: ExternalPublicationApproval,
) -> None:
    try:
        validate_external_publication_approval(plan, approval)
    except Exception:
        _raise_execution("approval")


def _build_expected_claim(
    plan: ExternalPublicationPlan,
    approval: ExternalPublicationApproval,
) -> tuple[ExternalPublicationAttemptClaim, str]:
    try:
        expected_claim = build_external_publication_attempt_claim(plan, approval)
    except Exception:
        _raise_execution("claim_expected")
    if type(expected_claim) is not ExternalPublicationAttemptClaim:
        _raise_execution("claim_expected")

    try:
        expected_claim_sha256 = external_publication_attempt_claim_digest(
            expected_claim
        )
    except Exception:
        _raise_execution("claim_digest")
    if not _is_sha256(expected_claim_sha256):
        _raise_execution("claim_digest")
    return expected_claim, expected_claim_sha256


def _read_verified_output(
    output_path: Path,
    plan: ExternalPublicationPlan,
) -> bytes:
    if type(output_path) is not _PATH_TYPE:
        _raise_execution("output_path_type")

    try:
        if (
            output_path.is_symlink()
            or not output_path.exists()
            or output_path.is_dir()
            or not output_path.is_file()
        ):
            _raise_execution("output_target")
    except ExternalPublicationExecutionError:
        raise
    except Exception:
        _raise_execution("output_target")

    try:
        output_bytes = output_path.read_bytes()
    except Exception:
        _raise_execution("output_read")
    if type(output_bytes) is not bytes:
        _raise_execution("output_read")

    try:
        output_digest = sha256(output_bytes).hexdigest()
        output_length = len(output_bytes)
    except Exception:
        _raise_execution("output_binding")
    if (
        output_length != plan.output_byte_length
        or output_digest != plan.business_output_sha256
    ):
        _raise_execution("output_binding")
    return output_bytes


def _validate_transport_receipt(receipt: object) -> None:
    if type(receipt) is not ExternalPublicationTransportReceipt:
        _raise_execution("transport_receipt")
    publication_id = receipt.publication_id
    if (
        type(publication_id) is not str
        or not publication_id
        or publication_id != publication_id.strip()
        or len(publication_id) > _MAX_PUBLICATION_ID_LENGTH
        or any(
            unicodedata.category(character) in {"Cc", "Cs"}
            for character in publication_id
        )
    ):
        _raise_execution("transport_receipt")


def _validate_execution_result(result: object) -> None:
    if type(result) is not ExternalPublicationExecutionResult:
        _raise_execution("result")
    try:
        if (
            type(result.schema_version) is not str
            or result.schema_version != "external-publication-execution-result.v1"
            or type(result.regeneration_id) is not str
            or not result.regeneration_id
            or len(result.regeneration_id) > _MAX_REGENERATION_ID_LENGTH
            or _SLUG_PATTERN.fullmatch(result.regeneration_id) is None
            or type(result.provider) is not str
            or not result.provider
            or len(result.provider) > _MAX_PROVIDER_LENGTH
            or _SLUG_PATTERN.fullmatch(result.provider) is None
            or type(result.status) is not str
            or result.status != "published"
        ):
            _raise_execution("result")
        for digest in (
            result.publication_attempt_claim_sha256,
            result.publication_plan_sha256,
            result.publication_approval_sha256,
            result.business_output_sha256,
            result.publication_target_sha256,
        ):
            if not _is_sha256(digest):
                _raise_execution("result")
        if (
            type(result.output_byte_length) is not int
            or type(result.output_byte_length) is bool
            or result.output_byte_length < 0
        ):
            _raise_execution("result")
        _validate_publication_id(result.publication_id)
    except ExternalPublicationExecutionError:
        _raise_execution("result")
    except Exception:
        _raise_execution("result")


def _validate_publication_id(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > _MAX_PUBLICATION_ID_LENGTH
        or any(unicodedata.category(character) in {"Cc", "Cs"} for character in value)
    ):
        _raise_execution("transport_receipt")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _raise_execution(classification: str) -> NoReturn:
    raise ExternalPublicationExecutionError(classification) from None


def _raise_ambiguous(classification: str) -> NoReturn:
    raise ExternalPublicationExecutionAmbiguousError(classification) from None


__all__ = [
    "ExternalPublicationExecutionAmbiguousError",
    "ExternalPublicationExecutionError",
    "ExternalPublicationExecutionFailureDetail",
    "ExternalPublicationExecutionResult",
    "ExternalPublicationTransport",
    "ExternalPublicationTransportReceipt",
    "execute_approved_external_publication",
]
