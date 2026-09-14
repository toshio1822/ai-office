"""Provider-free external publication planning, approval, and claim contracts.

Phase 278 binds one exact, durable Phase 276 reconciliation evidence record
with one explicit secret-free publication target. Phase 279 binds explicit
human approval to the exact plan digest. Phase 280 durably consumes one exact
approval through an exclusive write-ahead claim marker without performing any
provider call or external publication side effect.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.publication_regeneration_export_reconciliation import (
    PublicationRegenerationExportReconciliation,
)
from ai_office.engine.publication_regeneration_export_reconciliation_evidence import (
    load_publication_regeneration_export_reconciliation,
    publication_regeneration_export_reconciliation_digest,
)

_EXTERNAL_PUBLICATION_TARGET_SCHEMA_VERSION = "external-publication-target.v1"
_EXTERNAL_PUBLICATION_PLAN_SCHEMA_VERSION = "external-publication-plan.v1"
_EXTERNAL_PUBLICATION_ATTEMPT_SCHEMA_VERSION = "external-publication-attempt.v1"
_EXTERNAL_PUBLICATION_ATTEMPT_STATE = "claimed"
_TARGET_ERROR_MESSAGE = "external publication target is invalid"
_PLAN_ERROR_MESSAGE = "external publication plan is invalid"
_APPROVAL_ERROR_MESSAGE = "external publication approval is invalid"
_ATTEMPT_CLAIM_ERROR_MESSAGE = "external publication attempt claim is invalid"
_ATTEMPT_CLAIM_PERSISTENCE_ERROR_MESSAGE = (
    "external publication attempt claim persistence failed"
)
_ATTEMPT_ALREADY_CONSUMED_ERROR_MESSAGE = (
    "external publication approval is already consumed"
)
_ATTEMPT_CLAIM_LOAD_ERROR_MESSAGE = (
    "external publication attempt claim could not be loaded"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_PROVIDER_LENGTH = 128
_MAX_DESTINATION_LENGTH = 256
_MAX_APPROVAL_METADATA_LENGTH = 256
_MAX_REGENERATION_ID_LENGTH = 128
_PATH_TYPE = type(Path())
_ATTEMPT_CLAIM_KEYS = frozenset(
    {
        "approval_id",
        "approved_by",
        "business_output_sha256",
        "consumption_key",
        "output_byte_length",
        "provider",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "publication_target_sha256",
        "receipt_sha256",
        "reconciliation_evidence_sha256",
        "regeneration_id",
        "schema_version",
        "state",
    }
)


@dataclass(frozen=True)
class ExternalPublicationFailureDetail:
    """Detail-safe classification for a rejected publication contract."""

    classification: str


class ExternalPublicationError(ValueError):
    """Base class for fixed-message external publication contract errors."""

    _message = "external publication contract is invalid"

    def __init__(self, classification: str = "contract") -> None:
        super().__init__(self._message)
        self.detail = ExternalPublicationFailureDetail(classification)


class ExternalPublicationTargetError(ExternalPublicationError):
    """Raised when an external publication target is not exact and safe."""

    _message = _TARGET_ERROR_MESSAGE


class ExternalPublicationPlanError(ExternalPublicationError):
    """Raised when an external publication plan cannot be safely built."""

    _message = _PLAN_ERROR_MESSAGE


class ExternalPublicationApprovalError(ExternalPublicationError):
    """Raised when an external publication approval is not exact and safe."""

    _message = _APPROVAL_ERROR_MESSAGE


class ExternalPublicationAttemptClaimError(ExternalPublicationError):
    """Raised when an external publication one-use claim is invalid."""

    _message = _ATTEMPT_CLAIM_ERROR_MESSAGE


class ExternalPublicationAttemptClaimPersistenceError(
    ExternalPublicationAttemptClaimError
):
    """Raised when a one-use claim cannot be committed durably."""

    _message = _ATTEMPT_CLAIM_PERSISTENCE_ERROR_MESSAGE


class ExternalPublicationAttemptAlreadyConsumedError(
    ExternalPublicationAttemptClaimPersistenceError
):
    """Raised when the approval's authoritative marker already exists."""

    _message = _ATTEMPT_ALREADY_CONSUMED_ERROR_MESSAGE


class ExternalPublicationAttemptClaimLoadError(ExternalPublicationAttemptClaimError):
    """Raised when a persisted claim is not one exact canonical record."""

    _message = _ATTEMPT_CLAIM_LOAD_ERROR_MESSAGE


@dataclass(frozen=True)
class ExternalPublicationTarget:
    """Immutable, explicit, secret-free identity of one publication target."""

    schema_version: Literal["external-publication-target.v1"]
    provider: str
    destination_id: str

    def __post_init__(self) -> None:
        _validate_target(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical target JSON."""
        return external_publication_target_digest(self)


@dataclass(frozen=True)
class ExternalPublicationPlan:
    """Immutable identity of one future external publication decision."""

    schema_version: Literal["external-publication-plan.v1"]
    regeneration_id: str
    reconciliation_evidence_sha256: str
    receipt_sha256: str
    business_output_sha256: str
    output_byte_length: int
    provider: str
    publication_target_sha256: str

    def __post_init__(self) -> None:
        _validate_plan(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical plan JSON."""
        return external_publication_plan_digest(self)


@dataclass(frozen=True)
class ExternalPublicationApproval:
    """Immutable human approval bound to one exact publication plan digest."""

    approved: Literal[True]
    publication_plan_sha256: str
    approved_by: str
    approval_id: str

    def __post_init__(self) -> None:
        _validate_approval(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical approval JSON."""
        return external_publication_approval_digest(self)


@dataclass(frozen=True)
class ExternalPublicationAttemptClaim:
    """Immutable write-ahead identity for one consumed publication approval."""

    schema_version: Literal["external-publication-attempt.v1"]
    consumption_key: str
    regeneration_id: str
    publication_plan_sha256: str
    publication_approval_sha256: str
    approval_id: str
    approved_by: str
    reconciliation_evidence_sha256: str
    receipt_sha256: str
    business_output_sha256: str
    output_byte_length: int
    provider: str
    publication_target_sha256: str
    state: Literal["claimed"]

    def __post_init__(self) -> None:
        _validate_attempt_claim(self)

    @property
    def digest(self) -> str:
        """Return SHA-256 identity of canonical claim JSON."""
        return external_publication_attempt_claim_digest(self)


def serialize_external_publication_target_canonical(
    target: ExternalPublicationTarget,
) -> str:
    """Serialize one exact target as compact deterministic JSON."""
    _validate_target(target)
    try:
        return json.dumps(
            {
                "destination_id": target.destination_id,
                "provider": target.provider,
                "schema_version": target.schema_version,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except Exception:
        _raise_target("serialization")


def external_publication_target_canonical_bytes(
    target: ExternalPublicationTarget,
) -> bytes:
    """Return exact canonical target JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_target_canonical(target).encode("utf-8")
    except ExternalPublicationTargetError:
        raise
    except Exception:
        _raise_target("encoding")


def external_publication_target_digest(target: ExternalPublicationTarget) -> str:
    """Return SHA-256 over exact canonical target UTF-8 bytes."""
    return sha256(external_publication_target_canonical_bytes(target)).hexdigest()


def build_external_publication_plan(
    *,
    reconciliation_evidence_path: Path,
    target: ExternalPublicationTarget,
) -> ExternalPublicationPlan:
    """Build one plan from one strict matched evidence record and target."""
    _validate_evidence_path(reconciliation_evidence_path)
    try:
        reconciliation = load_publication_regeneration_export_reconciliation(
            reconciliation_evidence_path
        )
    except Exception:
        _raise_plan("evidence_loading")
    if type(reconciliation) is not PublicationRegenerationExportReconciliation:
        _raise_plan("evidence_loading")
    try:
        status = reconciliation.status
    except Exception:
        _raise_plan("evidence_loading")
    if type(status) is not str:
        _raise_plan("evidence_loading")
    if status != "matched":
        _raise_plan("non_matched_evidence")
    try:
        evidence_sha256 = publication_regeneration_export_reconciliation_digest(
            reconciliation
        )
    except Exception:
        _raise_plan("digest")
    if type(evidence_sha256) is not str or not _is_sha256(evidence_sha256):
        _raise_plan("digest")
    _validate_target_for_plan(target)
    try:
        target_sha256 = external_publication_target_digest(target)
    except ExternalPublicationTargetError:
        raise
    except Exception:
        _raise_plan("target_metadata")
    if type(target_sha256) is not str or not _is_sha256(target_sha256):
        _raise_plan("target_metadata")
    try:
        return ExternalPublicationPlan(
            schema_version=_EXTERNAL_PUBLICATION_PLAN_SCHEMA_VERSION,
            regeneration_id=reconciliation.regeneration_id,
            reconciliation_evidence_sha256=evidence_sha256,
            receipt_sha256=reconciliation.receipt_sha256,
            business_output_sha256=reconciliation.expected_business_output_sha256,
            output_byte_length=reconciliation.expected_output_byte_length,
            provider=target.provider,
            publication_target_sha256=target_sha256,
        )
    except ExternalPublicationPlanError:
        _raise_plan("plan_construction")
    except Exception:
        _raise_plan("plan_construction")


def validate_external_publication_plan(
    plan: ExternalPublicationPlan,
    *,
    reconciliation_evidence_path: Path,
    target: ExternalPublicationTarget,
) -> None:
    """Re-derive and validate one plan from fresh explicit inputs."""
    if type(plan) is not ExternalPublicationPlan:
        _raise_plan("plan_type")
    _validate_plan(plan)
    try:
        expected = build_external_publication_plan(
            reconciliation_evidence_path=reconciliation_evidence_path,
            target=target,
        )
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_plan("plan_validation")
    if plan != expected:
        _raise_plan("plan_validation")


def serialize_external_publication_plan_canonical(plan: ExternalPublicationPlan) -> str:
    """Serialize one exact plan as compact deterministic JSON."""
    _validate_plan(plan)
    try:
        return json.dumps(
            {
                "business_output_sha256": plan.business_output_sha256,
                "output_byte_length": plan.output_byte_length,
                "provider": plan.provider,
                "publication_target_sha256": plan.publication_target_sha256,
                "receipt_sha256": plan.receipt_sha256,
                "reconciliation_evidence_sha256": plan.reconciliation_evidence_sha256,
                "regeneration_id": plan.regeneration_id,
                "schema_version": plan.schema_version,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except Exception:
        _raise_plan("serialization")


def external_publication_plan_canonical_bytes(plan: ExternalPublicationPlan) -> bytes:
    """Return exact canonical plan JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_plan_canonical(plan).encode("utf-8")
    except ExternalPublicationPlanError:
        raise
    except Exception:
        _raise_plan("encoding")


def external_publication_plan_digest(plan: ExternalPublicationPlan) -> str:
    """Return SHA-256 over exact canonical plan UTF-8 bytes."""
    return sha256(external_publication_plan_canonical_bytes(plan)).hexdigest()


def approve_external_publication(
    plan: ExternalPublicationPlan,
    *,
    approved_by: str,
    approval_id: str,
) -> ExternalPublicationApproval:
    """Create one in-memory approval bound to one exact publication plan."""
    if type(plan) is not ExternalPublicationPlan:
        _raise_approval("plan_type")
    try:
        _validate_plan(plan)
    except ExternalPublicationPlanError:
        _raise_approval("plan")
    _validate_approval_metadata(approved_by, approval_id)
    try:
        plan_sha256 = external_publication_plan_digest(plan)
    except Exception:
        _raise_approval("digest")
    if not _is_sha256(plan_sha256):
        _raise_approval("digest")
    try:
        return ExternalPublicationApproval(
            approved=True,
            publication_plan_sha256=plan_sha256,
            approved_by=approved_by,
            approval_id=approval_id,
        )
    except ExternalPublicationApprovalError:
        raise
    except Exception:
        _raise_approval("validation")


def validate_external_publication_approval(
    plan: ExternalPublicationPlan,
    approval: ExternalPublicationApproval,
) -> None:
    """Validate one approval against the exact supplied plan digest."""
    if type(plan) is not ExternalPublicationPlan:
        _raise_approval("plan_type")
    if type(approval) is not ExternalPublicationApproval:
        _raise_approval("approval_type")
    try:
        _validate_plan(plan)
    except ExternalPublicationPlanError:
        _raise_approval("plan")
    _validate_approval(approval)
    try:
        plan_sha256 = external_publication_plan_digest(plan)
    except Exception:
        _raise_approval("digest")
    if not _is_sha256(plan_sha256):
        _raise_approval("digest")
    if approval.publication_plan_sha256 != plan_sha256:
        _raise_approval("plan_binding")


def serialize_external_publication_approval_canonical(
    approval: ExternalPublicationApproval,
) -> str:
    """Serialize one exact approval as compact deterministic JSON."""
    if type(approval) is not ExternalPublicationApproval:
        _raise_approval("approval_type")
    try:
        _validate_approval(approval)
    except ExternalPublicationApprovalError:
        raise
    except Exception:
        _raise_approval("validation")
    try:
        return json.dumps(
            {
                "approved": approval.approved,
                "approved_by": approval.approved_by,
                "approval_id": approval.approval_id,
                "publication_plan_sha256": approval.publication_plan_sha256,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except Exception:
        _raise_approval("serialization")


def external_publication_approval_canonical_bytes(
    approval: ExternalPublicationApproval,
) -> bytes:
    """Return exact canonical approval JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_approval_canonical(approval).encode(
            "utf-8"
        )
    except ExternalPublicationApprovalError:
        raise
    except Exception:
        _raise_approval("serialization")


def external_publication_approval_digest(approval: ExternalPublicationApproval) -> str:
    """Return SHA-256 over exact canonical approval UTF-8 bytes."""
    try:
        return sha256(
            external_publication_approval_canonical_bytes(approval)
        ).hexdigest()
    except ExternalPublicationApprovalError:
        raise
    except Exception:
        _raise_approval("digest")


def external_publication_consumption_key(
    approval: ExternalPublicationApproval,
) -> str:
    """Derive one deterministic ledger key from the exact approval ID."""
    if type(approval) is not ExternalPublicationApproval:
        _raise_attempt_claim("approval_type")
    try:
        _validate_approval(approval)
        return sha256(approval.approval_id.encode("utf-8")).hexdigest()
    except ExternalPublicationAttemptClaimError:
        raise
    except ExternalPublicationApprovalError:
        _raise_attempt_claim("approval_binding")
    except Exception:
        _raise_attempt_claim("consumption_key")


def build_external_publication_attempt_claim(
    plan: ExternalPublicationPlan,
    approval: ExternalPublicationApproval,
) -> ExternalPublicationAttemptClaim:
    """Build one deterministic claim without filesystem or external access."""
    if type(plan) is not ExternalPublicationPlan:
        _raise_attempt_claim("plan_type")
    if type(approval) is not ExternalPublicationApproval:
        _raise_attempt_claim("approval_type")
    try:
        _validate_plan(plan)
    except ExternalPublicationPlanError:
        _raise_attempt_claim("plan_binding")
    try:
        validate_external_publication_approval(plan, approval)
    except ExternalPublicationApprovalError:
        _raise_attempt_claim("plan_binding")
    try:
        consumption_key = external_publication_consumption_key(approval)
        approval_sha256 = external_publication_approval_digest(approval)
    except ExternalPublicationAttemptClaimError:
        raise
    except ExternalPublicationApprovalError:
        _raise_attempt_claim("approval_binding")
    except Exception:
        _raise_attempt_claim("claim_binding")
    if not _is_sha256(consumption_key):
        _raise_attempt_claim("consumption_key")
    if not _is_sha256(approval_sha256):
        _raise_attempt_claim("approval_binding")
    try:
        return ExternalPublicationAttemptClaim(
            schema_version=_EXTERNAL_PUBLICATION_ATTEMPT_SCHEMA_VERSION,
            consumption_key=consumption_key,
            regeneration_id=plan.regeneration_id,
            publication_plan_sha256=approval.publication_plan_sha256,
            publication_approval_sha256=approval_sha256,
            approval_id=approval.approval_id,
            approved_by=approval.approved_by,
            reconciliation_evidence_sha256=plan.reconciliation_evidence_sha256,
            receipt_sha256=plan.receipt_sha256,
            business_output_sha256=plan.business_output_sha256,
            output_byte_length=plan.output_byte_length,
            provider=plan.provider,
            publication_target_sha256=plan.publication_target_sha256,
            state=_EXTERNAL_PUBLICATION_ATTEMPT_STATE,
        )
    except ExternalPublicationAttemptClaimError:
        raise
    except Exception:
        _raise_attempt_claim("claim_binding")


def serialize_external_publication_attempt_claim_canonical(
    claim: ExternalPublicationAttemptClaim,
) -> str:
    """Serialize one exact attempt claim as canonical compact JSON."""
    if type(claim) is not ExternalPublicationAttemptClaim:
        _raise_attempt_claim("claim_type")
    _validate_attempt_claim(claim)
    try:
        return json.dumps(
            {
                "approval_id": claim.approval_id,
                "approved_by": claim.approved_by,
                "business_output_sha256": claim.business_output_sha256,
                "consumption_key": claim.consumption_key,
                "output_byte_length": claim.output_byte_length,
                "provider": claim.provider,
                "publication_approval_sha256": claim.publication_approval_sha256,
                "publication_plan_sha256": claim.publication_plan_sha256,
                "publication_target_sha256": claim.publication_target_sha256,
                "receipt_sha256": claim.receipt_sha256,
                "reconciliation_evidence_sha256": claim.reconciliation_evidence_sha256,
                "regeneration_id": claim.regeneration_id,
                "schema_version": claim.schema_version,
                "state": claim.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except ExternalPublicationAttemptClaimError:
        raise
    except Exception:
        _raise_attempt_claim("serialization")


def external_publication_attempt_claim_canonical_bytes(
    claim: ExternalPublicationAttemptClaim,
) -> bytes:
    """Return exact canonical claim JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_attempt_claim_canonical(claim).encode(
            "utf-8"
        )
    except ExternalPublicationAttemptClaimError:
        raise
    except Exception:
        _raise_attempt_claim("serialization")


def external_publication_attempt_claim_digest(
    claim: ExternalPublicationAttemptClaim,
) -> str:
    """Return SHA-256 over exact canonical claim bytes."""
    try:
        return sha256(
            external_publication_attempt_claim_canonical_bytes(claim)
        ).hexdigest()
    except ExternalPublicationAttemptClaimError:
        raise
    except Exception:
        _raise_attempt_claim("serialization")


def external_publication_attempt_claim_path(
    ledger_directory: Path,
    consumption_key: str,
) -> Path:
    """Return the canonical marker path under one explicit existing ledger."""
    _validate_ledger_directory(ledger_directory)
    if not _is_sha256(consumption_key):
        _raise_attempt_claim("consumption_key")
    return ledger_directory / f"{consumption_key}.json"


def claim_external_publication_attempt(
    ledger_directory: Path,
    plan: ExternalPublicationPlan,
    approval: ExternalPublicationApproval,
) -> ExternalPublicationAttemptClaim:
    """Durably create one exclusive write-ahead approval-consumption marker."""
    claim = build_external_publication_attempt_claim(plan, approval)
    path = external_publication_attempt_claim_path(
        ledger_directory,
        claim.consumption_key,
    )
    contents = external_publication_attempt_claim_canonical_bytes(claim)
    try:
        handle = path.open("xb")
    except FileExistsError:
        _raise_attempt_already_consumed()
    except OSError:
        _raise_attempt_claim_persistence("create")

    try:
        with handle:
            written = handle.write(contents)
            if written != len(contents):
                raise OSError("short claim write")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_ledger_directory(ledger_directory)
    except OSError:
        _raise_attempt_claim_persistence("ambiguous")
    return claim


def load_external_publication_attempt_claim(
    path: Path,
) -> ExternalPublicationAttemptClaim:
    """Read and strictly validate one exact canonical persisted claim marker."""
    _validate_claim_path(path)
    try:
        contents = path.read_bytes()
    except OSError:
        _raise_attempt_claim_load("target")
    try:
        text = contents.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_attempt_claim_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
        claim = _parse_attempt_claim(value)
        if external_publication_attempt_claim_canonical_bytes(claim) != contents:
            _raise_attempt_claim_load("noncanonical")
        return claim
    except ExternalPublicationAttemptClaimLoadError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateAttemptClaimKeyError,
        _NonstandardJsonConstantError,
    ):
        _raise_attempt_claim_load("parse")
    except ExternalPublicationAttemptClaimError:
        _raise_attempt_claim_load("record")
    except (TypeError, ValueError, AttributeError, KeyError):
        _raise_attempt_claim_load("record")


def _validate_target_for_plan(target: object) -> None:
    try:
        _validate_target(target)
    except ExternalPublicationTargetError:
        raise
    except Exception:
        _raise_plan("target_metadata")


def _validate_target(target: object) -> None:
    if type(target) is not ExternalPublicationTarget:
        _raise_target("target_type")
    try:
        if (
            type(target.schema_version) is not str
            or target.schema_version != _EXTERNAL_PUBLICATION_TARGET_SCHEMA_VERSION
        ):
            _raise_target("target_metadata")
        _validate_provider(target.provider, _raise_target)
        if (
            type(target.destination_id) is not str
            or not target.destination_id
            or target.destination_id != target.destination_id.strip()
            or len(target.destination_id) > _MAX_DESTINATION_LENGTH
            or any(
                unicodedata.category(character) in {"Cc", "Cs"}
                for character in target.destination_id
            )
        ):
            _raise_target("target_metadata")
    except ExternalPublicationTargetError:
        raise
    except Exception:
        _raise_target("target_metadata")


def _validate_plan(plan: object) -> None:
    if type(plan) is not ExternalPublicationPlan:
        _raise_plan("plan_type")
    try:
        if (
            type(plan.schema_version) is not str
            or plan.schema_version != _EXTERNAL_PUBLICATION_PLAN_SCHEMA_VERSION
        ):
            _raise_plan("plan_metadata")
        _validate_regeneration_id(plan.regeneration_id)
        for value in (
            plan.reconciliation_evidence_sha256,
            plan.receipt_sha256,
            plan.business_output_sha256,
            plan.publication_target_sha256,
        ):
            if not _is_sha256(value):
                _raise_plan("plan_metadata")
        if (
            type(plan.output_byte_length) is not int
            or type(plan.output_byte_length) is bool
            or plan.output_byte_length < 0
        ):
            _raise_plan("plan_metadata")
        _validate_provider(plan.provider, _raise_plan)
    except ExternalPublicationPlanError:
        raise
    except Exception:
        _raise_plan("plan_metadata")


def _validate_provider(value: object, error: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_PROVIDER_LENGTH
        or _SLUG_PATTERN.fullmatch(value) is None
    ):
        if error is _raise_target:
            _raise_target("target_metadata")
        _raise_plan("plan_metadata")


def _validate_regeneration_id(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_REGENERATION_ID_LENGTH
        or _SLUG_PATTERN.fullmatch(value) is None
    ):
        _raise_plan("plan_metadata")


def _validate_evidence_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_plan("evidence_loading")


def _validate_approval(approval: object) -> None:
    if type(approval) is not ExternalPublicationApproval:
        _raise_approval("approval_type")
    try:
        if type(approval.approved) is not bool or approval.approved is not True:
            _raise_approval("approval_metadata")
        if not _is_sha256(approval.publication_plan_sha256):
            _raise_approval("digest")
        _validate_approval_metadata(approval.approved_by, approval.approval_id)
    except ExternalPublicationApprovalError:
        raise
    except Exception:
        _raise_approval("validation")


def _validate_approval_metadata(approved_by: object, approval_id: object) -> None:
    for value, classification in (
        (approved_by, "approved_by"),
        (approval_id, "approval_id"),
    ):
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > _MAX_APPROVAL_METADATA_LENGTH
        ):
            _raise_approval(classification)
        if any(
            unicodedata.category(character) in {"Cc", "Cs"}
            for character in value
        ):
            _raise_approval("approval_metadata")


def _validate_attempt_claim(claim: object) -> None:
    if type(claim) is not ExternalPublicationAttemptClaim:
        _raise_attempt_claim("claim_type")
    if (
        type(claim.schema_version) is not str
        or claim.schema_version != _EXTERNAL_PUBLICATION_ATTEMPT_SCHEMA_VERSION
    ):
        _raise_attempt_claim("schema_version")
    if not _is_sha256(claim.consumption_key):
        _raise_attempt_claim("consumption_key")
    if (
        type(claim.state) is not str
        or claim.state != _EXTERNAL_PUBLICATION_ATTEMPT_STATE
    ):
        _raise_attempt_claim("state")
    if not _is_sha256(claim.publication_plan_sha256):
        _raise_attempt_claim("plan_binding")
    if not _is_sha256(claim.publication_approval_sha256):
        _raise_attempt_claim("approval_binding")
    try:
        plan = ExternalPublicationPlan(
            schema_version=_EXTERNAL_PUBLICATION_PLAN_SCHEMA_VERSION,
            regeneration_id=claim.regeneration_id,
            reconciliation_evidence_sha256=claim.reconciliation_evidence_sha256,
            receipt_sha256=claim.receipt_sha256,
            business_output_sha256=claim.business_output_sha256,
            output_byte_length=claim.output_byte_length,
            provider=claim.provider,
            publication_target_sha256=claim.publication_target_sha256,
        )
    except ExternalPublicationPlanError:
        _raise_attempt_claim("plan_binding")
    try:
        plan_digest = external_publication_plan_digest(plan)
    except Exception:
        _raise_attempt_claim("plan_binding")
    if not _is_sha256(plan_digest) or plan_digest != claim.publication_plan_sha256:
        _raise_attempt_claim("plan_binding")
    try:
        approval = ExternalPublicationApproval(
            approved=True,
            publication_plan_sha256=claim.publication_plan_sha256,
            approved_by=claim.approved_by,
            approval_id=claim.approval_id,
        )
    except ExternalPublicationApprovalError:
        _raise_attempt_claim("approval_binding")
    try:
        approval_digest = external_publication_approval_digest(approval)
    except Exception:
        _raise_attempt_claim("approval_binding")
    if (
        not _is_sha256(approval_digest)
        or approval_digest != claim.publication_approval_sha256
    ):
        _raise_attempt_claim("approval_binding")
    try:
        consumption_key = external_publication_consumption_key(approval)
    except Exception:
        _raise_attempt_claim("consumption_key")
    if not _is_sha256(consumption_key) or consumption_key != claim.consumption_key:
        _raise_attempt_claim("consumption_key")


def _validate_ledger_directory(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_attempt_claim_persistence("ledger_directory_type")
    try:
        if path.is_symlink() or not path.exists() or not path.is_dir():
            _raise_attempt_claim_persistence("ledger_directory")
    except ExternalPublicationAttemptClaimPersistenceError:
        raise
    except OSError:
        _raise_attempt_claim_persistence("ledger_directory")


def _validate_claim_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_attempt_claim_load("path_type")
    try:
        if path.is_symlink() or not path.exists() or not path.is_file():
            _raise_attempt_claim_load("target")
    except ExternalPublicationAttemptClaimLoadError:
        raise
    except OSError:
        _raise_attempt_claim_load("target")


def _fsync_ledger_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_attempt_claim(value: object) -> ExternalPublicationAttemptClaim:
    if type(value) is not dict or frozenset(value) != _ATTEMPT_CLAIM_KEYS:
        _raise_attempt_claim_load("keys")
    try:
        return ExternalPublicationAttemptClaim(
            schema_version=value["schema_version"],
            consumption_key=value["consumption_key"],
            regeneration_id=value["regeneration_id"],
            publication_plan_sha256=value["publication_plan_sha256"],
            publication_approval_sha256=value["publication_approval_sha256"],
            approval_id=value["approval_id"],
            approved_by=value["approved_by"],
            reconciliation_evidence_sha256=value["reconciliation_evidence_sha256"],
            receipt_sha256=value["receipt_sha256"],
            business_output_sha256=value["business_output_sha256"],
            output_byte_length=value["output_byte_length"],
            provider=value["provider"],
            publication_target_sha256=value["publication_target_sha256"],
            state=value["state"],
        )
    except ExternalPublicationAttemptClaimError:
        _raise_attempt_claim_load("record")
    except (KeyError, TypeError, ValueError, AttributeError):
        _raise_attempt_claim_load("record")


def _reject_duplicate_attempt_claim_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateAttemptClaimKeyError
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    raise _NonstandardJsonConstantError(value)


class _DuplicateAttemptClaimKeyError(ValueError):
    pass


class _NonstandardJsonConstantError(ValueError):
    pass


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _raise_target(classification: str) -> NoReturn:
    raise ExternalPublicationTargetError(classification) from None


def _raise_plan(classification: str) -> NoReturn:
    raise ExternalPublicationPlanError(classification) from None


def _raise_approval(classification: str) -> NoReturn:
    raise ExternalPublicationApprovalError(classification) from None


def _raise_attempt_claim(classification: str) -> NoReturn:
    raise ExternalPublicationAttemptClaimError(classification) from None


def _raise_attempt_claim_persistence(classification: str) -> NoReturn:
    raise ExternalPublicationAttemptClaimPersistenceError(classification) from None


def _raise_attempt_already_consumed() -> NoReturn:
    raise ExternalPublicationAttemptAlreadyConsumedError("already_consumed") from None


def _raise_attempt_claim_load(classification: str) -> NoReturn:
    raise ExternalPublicationAttemptClaimLoadError(classification) from None


__all__ = [
    "ExternalPublicationError",
    "ExternalPublicationFailureDetail",
    "ExternalPublicationApproval",
    "ExternalPublicationApprovalError",
    "ExternalPublicationAttemptAlreadyConsumedError",
    "ExternalPublicationAttemptClaim",
    "ExternalPublicationAttemptClaimError",
    "ExternalPublicationAttemptClaimLoadError",
    "ExternalPublicationAttemptClaimPersistenceError",
    "ExternalPublicationPlan",
    "ExternalPublicationPlanError",
    "ExternalPublicationTarget",
    "ExternalPublicationTargetError",
    "approve_external_publication",
    "build_external_publication_attempt_claim",
    "build_external_publication_plan",
    "claim_external_publication_attempt",
    "external_publication_approval_canonical_bytes",
    "external_publication_approval_digest",
    "external_publication_attempt_claim_canonical_bytes",
    "external_publication_attempt_claim_digest",
    "external_publication_attempt_claim_path",
    "external_publication_consumption_key",
    "external_publication_plan_canonical_bytes",
    "external_publication_plan_digest",
    "external_publication_target_canonical_bytes",
    "external_publication_target_digest",
    "load_external_publication_attempt_claim",
    "serialize_external_publication_approval_canonical",
    "serialize_external_publication_attempt_claim_canonical",
    "serialize_external_publication_plan_canonical",
    "serialize_external_publication_target_canonical",
    "validate_external_publication_approval",
    "validate_external_publication_plan",
]
