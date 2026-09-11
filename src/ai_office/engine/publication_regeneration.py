"""Provider-free control contracts for human-approved regeneration.

This module binds one non-ready publication-readiness audit to one explicit,
future model-invocation identity and execution target.  It deliberately does
not execute a provider or mutate the original workflow artifacts.  Phase 265
adds only the provider-free, durable one-use outer-approval claim ledger.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from ai_office.engine.post_terminal_facts import (
    PublicationReadinessAuditError,
    PublicationReadinessAuditRecord,
    post_terminal_facts_digest,
    publication_readiness_audit_digest,
)
from ai_office.execution_target import (
    SUPPORTED_EXECUTION_PROVIDERS,
    ModelExecutionTarget,
    ModelExecutionTargetError,
    execution_target_fingerprint,
    execution_target_for_name,
    validate_execution_target_for_provider,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    RuntimeFactsSnapshot,
    UpstreamStepOutput,
    build_model_invocation_execution_fingerprint,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition

PublicationRegenerationReadiness = Literal[
    "stale_or_inconsistent",
    "insufficient_evidence",
]

_PUBLICATION_REGENERATION_PLAN_SCHEMA_VERSION = "publication-regeneration-plan.v1"
_PLAN_ERROR_MESSAGE = "publication regeneration plan is invalid"
_APPROVAL_ERROR_MESSAGE = "publication regeneration approval is invalid"
_ATTEMPT_CLAIM_ERROR_MESSAGE = "publication regeneration attempt claim is invalid"
_ATTEMPT_CLAIM_PERSISTENCE_ERROR_MESSAGE = (
    "publication regeneration attempt claim persistence failed"
)
_ATTEMPT_CLAIM_LOAD_ERROR_MESSAGE = (
    "publication regeneration attempt claim could not be loaded"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REGENERATION_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_IDENTIFIER_LENGTH = 128
_MAX_METADATA_LENGTH = 256
_PUBLICATION_REGENERATION_ATTEMPT_SCHEMA_VERSION = (
    "publication-regeneration-attempt.v1"
)
_PUBLICATION_REGENERATION_ATTEMPT_STATE = "claimed"
_ATTEMPT_CLAIM_KEYS = frozenset(
    {
        "approval_id",
        "approved_by",
        "consumption_key",
        "execution_target_sha256",
        "invocation_request_sha256",
        "provider",
        "regeneration_approval_sha256",
        "regeneration_id",
        "regeneration_plan_sha256",
        "schema_version",
        "source_audit_sha256",
        "state",
    }
)
_PLAN_REASON_ORDER = (
    "execution_not_workflow_complete",
    "final_output_missing",
    "final_output_mismatch",
    "claim_contract_missing",
    "claim_contract_mismatch",
)
_PLAN_REASON_CODES = frozenset(_PLAN_REASON_ORDER)


@dataclass(frozen=True)
class PublicationRegenerationFailureDetail:
    """Safe classification for a rejected regeneration control value."""

    classification: str


class PublicationRegenerationError(ValueError):
    """Base error for invalid regeneration control values."""

    def __init__(self, classification: str = "regeneration") -> None:
        super().__init__(_PLAN_ERROR_MESSAGE)
        self.detail = PublicationRegenerationFailureDetail(classification)


class PublicationRegenerationPlanError(PublicationRegenerationError):
    """Raised when a regeneration plan is not exact and internally bound."""


class PublicationRegenerationApprovalError(PublicationRegenerationError):
    """Raised when a regeneration approval is not bound to one exact plan."""

    def __init__(self, classification: str = "approval") -> None:
        ValueError.__init__(self, _APPROVAL_ERROR_MESSAGE)
        self.detail = PublicationRegenerationFailureDetail(classification)


class PublicationRegenerationAttemptClaimError(ValueError):
    """Raised when a one-use regeneration attempt claim is invalid."""

    def __init__(self, classification: str = "claim") -> None:
        super().__init__(_ATTEMPT_CLAIM_ERROR_MESSAGE)
        self.detail = PublicationRegenerationFailureDetail(classification)


class PublicationRegenerationAttemptClaimPersistenceError(
    PublicationRegenerationAttemptClaimError
):
    """Raised when a durable one-use claim cannot be committed safely."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, _ATTEMPT_CLAIM_PERSISTENCE_ERROR_MESSAGE)
        self.detail = PublicationRegenerationFailureDetail(classification)


class PublicationRegenerationAttemptAlreadyConsumedError(
    PublicationRegenerationAttemptClaimPersistenceError
):
    """Raised whenever the approval's authoritative ledger marker exists."""


class PublicationRegenerationAttemptClaimLoadError(
    PublicationRegenerationAttemptClaimError
):
    """Raised when a persisted claim is not an exact canonical record."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, _ATTEMPT_CLAIM_LOAD_ERROR_MESSAGE)
        self.detail = PublicationRegenerationFailureDetail(classification)


@dataclass(frozen=True)
class PublicationRegenerationPlan:
    """Immutable identity of one future regeneration decision."""

    schema_version: Literal["publication-regeneration-plan.v1"]
    regeneration_id: str
    workflow_id: str
    source_audit_sha256: str
    source_post_terminal_facts_sha256: str
    source_business_output_sha256: str | None
    source_readiness: PublicationRegenerationReadiness
    source_reason_codes: tuple[str, ...]
    provider: str
    execution_target_sha256: str
    invocation_request_sha256: str

    def __post_init__(self) -> None:
        _validate_publication_regeneration_plan(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical plan JSON."""
        return publication_regeneration_plan_digest(self)


@dataclass(frozen=True)
class PublicationRegenerationApproval:
    """Explicit outer human approval bound to one exact plan digest.

    This is intentionally separate from ``ModelInvocationExecutionApproval``.
    The existing inner approval binds a future provider request and target; this
    outer value binds the source-audit-to-new-lineage remediation decision.
    """

    approved: Literal[True]
    regeneration_plan_sha256: str
    approved_by: str
    approval_id: str

    def __post_init__(self) -> None:
        _validate_publication_regeneration_approval(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical approval JSON."""
        return publication_regeneration_approval_digest(self)


@dataclass(frozen=True)
class PublicationRegenerationAttemptClaim:
    """Immutable write-ahead identity for one consumed outer approval."""

    schema_version: Literal["publication-regeneration-attempt.v1"]
    consumption_key: str
    regeneration_id: str
    regeneration_plan_sha256: str
    regeneration_approval_sha256: str
    approval_id: str
    approved_by: str
    source_audit_sha256: str
    provider: str
    execution_target_sha256: str
    invocation_request_sha256: str
    state: Literal["claimed"]

    def __post_init__(self) -> None:
        _validate_publication_regeneration_attempt_claim(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical claim JSON."""
        return publication_regeneration_attempt_claim_digest(self)


def build_publication_regeneration_plan(
    audit_record: PublicationReadinessAuditRecord,
    regeneration_id: str,
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    execution_target: ModelExecutionTarget,
) -> PublicationRegenerationPlan:
    """Build one deterministic plan from explicit audit and future inputs.

    The request is never constructed from the audit or from business text.  It
    must already be an explicit caller-supplied ``ModelInvocationRequest``.
    """
    try:
        source_audit_sha256 = _validate_source_audit(audit_record)
        if audit_record.assessment.readiness == "ready":
            _raise_plan("source_ready")
        _validate_regeneration_id(regeneration_id)
        _validate_invocation_request(request)
        _validate_resolved_tools(resolved_tools)
        target = _validate_target(execution_target)
        invocation_fingerprint = build_model_invocation_execution_fingerprint(
            request,
            resolved_tools,
            target,
        )
        return PublicationRegenerationPlan(
            schema_version=_PUBLICATION_REGENERATION_PLAN_SCHEMA_VERSION,
            regeneration_id=regeneration_id,
            workflow_id=audit_record.post_terminal_facts.workflow_id,
            source_audit_sha256=source_audit_sha256,
            source_post_terminal_facts_sha256=post_terminal_facts_digest(
                audit_record.post_terminal_facts
            ),
            source_business_output_sha256=(
                audit_record.assessment.business_output_sha256
            ),
            source_readiness=audit_record.assessment.readiness,
            source_reason_codes=audit_record.assessment.reason_codes,
            provider=target.provider,
            execution_target_sha256=execution_target_fingerprint(target),
            invocation_request_sha256=invocation_fingerprint,
        )
    except PublicationRegenerationError:
        raise
    except Exception:
        _raise_plan("build")


def validate_publication_regeneration_plan(
    plan: PublicationRegenerationPlan,
    audit_record: PublicationReadinessAuditRecord,
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    execution_target: ModelExecutionTarget,
) -> None:
    """Re-derive and authorize the plan against the exact current inputs."""
    try:
        if type(plan) is not PublicationRegenerationPlan:
            _raise_plan("plan_type")
        _validate_publication_regeneration_plan(plan)
        expected = build_publication_regeneration_plan(
            audit_record,
            plan.regeneration_id,
            request,
            resolved_tools,
            execution_target,
        )
        if plan != expected:
            _raise_plan("plan_binding")
    except PublicationRegenerationPlanError:
        raise
    except Exception:
        _raise_plan("validation")


def serialize_publication_regeneration_plan_canonical(
    plan: PublicationRegenerationPlan,
) -> str:
    """Serialize a plan as compact deterministic UTF-8 JSON text."""
    if type(plan) is not PublicationRegenerationPlan:
        _raise_plan("plan_type")
    _validate_publication_regeneration_plan(plan)
    value = {
        "execution_target_sha256": plan.execution_target_sha256,
        "invocation_request_sha256": plan.invocation_request_sha256,
        "provider": plan.provider,
        "regeneration_id": plan.regeneration_id,
        "schema_version": plan.schema_version,
        "source_audit_sha256": plan.source_audit_sha256,
        "source_business_output_sha256": plan.source_business_output_sha256,
        "source_post_terminal_facts_sha256": plan.source_post_terminal_facts_sha256,
        "source_readiness": plan.source_readiness,
        "source_reason_codes": list(plan.source_reason_codes),
        "workflow_id": plan.workflow_id,
    }
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        _raise_plan("serialization")


def publication_regeneration_plan_canonical_bytes(
    plan: PublicationRegenerationPlan,
) -> bytes:
    """Return canonical plan JSON encoded as UTF-8 bytes."""
    return serialize_publication_regeneration_plan_canonical(plan).encode("utf-8")


def publication_regeneration_plan_digest(plan: PublicationRegenerationPlan) -> str:
    """Return the SHA-256 digest of canonical plan JSON bytes."""
    return sha256(publication_regeneration_plan_canonical_bytes(plan)).hexdigest()


def approve_publication_regeneration(
    plan: PublicationRegenerationPlan,
    *,
    approved_by: str,
    approval_id: str,
) -> PublicationRegenerationApproval:
    """Create explicit outer approval for one exact regeneration plan."""
    try:
        if type(plan) is not PublicationRegenerationPlan:
            _raise_approval("plan_type")
        _validate_publication_regeneration_plan(plan)
        _validate_approval_metadata(approved_by, approval_id)
        return PublicationRegenerationApproval(
            approved=True,
            regeneration_plan_sha256=publication_regeneration_plan_digest(plan),
            approved_by=approved_by,
            approval_id=approval_id,
        )
    except PublicationRegenerationApprovalError:
        raise
    except PublicationRegenerationPlanError:
        _raise_approval("plan")
    except Exception:
        _raise_approval("create")


def validate_publication_regeneration_approval(
    plan: PublicationRegenerationPlan,
    approval: PublicationRegenerationApproval,
) -> None:
    """Validate exact outer approval without exposing mismatching values."""
    try:
        if type(plan) is not PublicationRegenerationPlan:
            _raise_approval("plan_type")
        if type(approval) is not PublicationRegenerationApproval:
            _raise_approval("approval_type")
        _validate_publication_regeneration_plan(plan)
        _validate_publication_regeneration_approval(approval)
        if (
            approval.regeneration_plan_sha256
            != publication_regeneration_plan_digest(plan)
        ):
            _raise_approval("plan_binding")
    except PublicationRegenerationApprovalError:
        raise
    except Exception:
        _raise_approval("validation")


def serialize_publication_regeneration_approval_canonical(
    approval: PublicationRegenerationApproval,
) -> str:
    """Serialize the exact Phase264 approval as compact canonical JSON."""
    if type(approval) is not PublicationRegenerationApproval:
        _raise_approval("approval_type")
    _validate_publication_regeneration_approval(approval)
    try:
        return json.dumps(
            {
                "approved": approval.approved,
                "approved_by": approval.approved_by,
                "approval_id": approval.approval_id,
                "regeneration_plan_sha256": approval.regeneration_plan_sha256,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        _raise_approval("serialization")


def publication_regeneration_approval_canonical_bytes(
    approval: PublicationRegenerationApproval,
) -> bytes:
    """Return canonical Phase264 approval JSON encoded as UTF-8 bytes."""
    return serialize_publication_regeneration_approval_canonical(approval).encode(
        "utf-8"
    )


def publication_regeneration_approval_digest(
    approval: PublicationRegenerationApproval,
) -> str:
    """Return the SHA-256 identity of canonical approval JSON bytes."""
    return sha256(
        publication_regeneration_approval_canonical_bytes(approval)
    ).hexdigest()


def publication_regeneration_consumption_key(
    approval_or_id: PublicationRegenerationApproval | str,
) -> str:
    """Derive the lowercase ledger key from one exact approval ID.

    Accepting either the exact approval object or an exact approval-ID string
    keeps the derivation useful at the metadata boundary without coercing
    arbitrary values.  The claim builder always supplies the validated
    approval object.
    """
    if type(approval_or_id) is PublicationRegenerationApproval:
        _validate_publication_regeneration_approval(approval_or_id)
        approval_id = approval_or_id.approval_id
    elif type(approval_or_id) is str:
        _validate_attempt_claim_approval_id(approval_or_id)
        approval_id = approval_or_id
    else:
        _raise_attempt_claim("approval_type")
    return sha256(approval_id.encode("utf-8")).hexdigest()


def publication_regeneration_consumption_key_from_approval_id(
    approval_id: str,
) -> str:
    """Derive the ledger key from an exact approval-ID string."""
    return publication_regeneration_consumption_key(approval_id)


def build_publication_regeneration_attempt_claim(
    plan: PublicationRegenerationPlan,
    approval: PublicationRegenerationApproval,
) -> PublicationRegenerationAttemptClaim:
    """Build one deterministic immutable claim without external access."""
    try:
        if type(plan) is not PublicationRegenerationPlan:
            _raise_attempt_claim("plan_type")
        if type(approval) is not PublicationRegenerationApproval:
            _raise_attempt_claim("approval_type")
        _validate_publication_regeneration_plan(plan)
        try:
            validate_publication_regeneration_approval(plan, approval)
        except PublicationRegenerationApprovalError:
            _raise_attempt_claim("plan_binding")
        return PublicationRegenerationAttemptClaim(
            schema_version=_PUBLICATION_REGENERATION_ATTEMPT_SCHEMA_VERSION,
            consumption_key=publication_regeneration_consumption_key(approval),
            regeneration_id=plan.regeneration_id,
            regeneration_plan_sha256=plan.digest,
            regeneration_approval_sha256=(
                publication_regeneration_approval_digest(approval)
            ),
            approval_id=approval.approval_id,
            approved_by=approval.approved_by,
            source_audit_sha256=plan.source_audit_sha256,
            provider=plan.provider,
            execution_target_sha256=plan.execution_target_sha256,
            invocation_request_sha256=plan.invocation_request_sha256,
            state=_PUBLICATION_REGENERATION_ATTEMPT_STATE,
        )
    except PublicationRegenerationAttemptClaimError:
        raise
    except (PublicationRegenerationApprovalError, PublicationRegenerationPlanError):
        _raise_attempt_claim("source_validation")
    except Exception:
        _raise_attempt_claim("build")


def serialize_publication_regeneration_attempt_claim_canonical(
    claim: PublicationRegenerationAttemptClaim,
) -> str:
    """Serialize one attempt claim as compact deterministic JSON."""
    if type(claim) is not PublicationRegenerationAttemptClaim:
        _raise_attempt_claim("claim_type")
    _validate_publication_regeneration_attempt_claim(claim)
    try:
        return json.dumps(
            {
                "approval_id": claim.approval_id,
                "approved_by": claim.approved_by,
                "consumption_key": claim.consumption_key,
                "execution_target_sha256": claim.execution_target_sha256,
                "invocation_request_sha256": claim.invocation_request_sha256,
                "provider": claim.provider,
                "regeneration_approval_sha256": claim.regeneration_approval_sha256,
                "regeneration_id": claim.regeneration_id,
                "regeneration_plan_sha256": claim.regeneration_plan_sha256,
                "schema_version": claim.schema_version,
                "source_audit_sha256": claim.source_audit_sha256,
                "state": claim.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        _raise_attempt_claim("serialization")


def publication_regeneration_attempt_claim_canonical_bytes(
    claim: PublicationRegenerationAttemptClaim,
) -> bytes:
    """Return canonical attempt-claim JSON encoded as UTF-8 bytes."""
    return serialize_publication_regeneration_attempt_claim_canonical(claim).encode(
        "utf-8"
    )


def publication_regeneration_attempt_claim_digest(
    claim: PublicationRegenerationAttemptClaim,
) -> str:
    """Return the SHA-256 identity of canonical claim JSON bytes."""
    return sha256(
        publication_regeneration_attempt_claim_canonical_bytes(claim)
    ).hexdigest()


def publication_regeneration_attempt_claim_path(
    ledger_directory: Path,
    consumption_key: str,
) -> Path:
    """Return the canonical marker path in an existing caller ledger."""
    _validate_existing_ledger_directory(ledger_directory, _raise_claim_persistence)
    _validate_claim_consumption_key(consumption_key)
    return ledger_directory / f"{consumption_key}.json"


def claim_publication_regeneration_attempt(
    ledger_directory: Path,
    plan: PublicationRegenerationPlan,
    approval: PublicationRegenerationApproval,
) -> PublicationRegenerationAttemptClaim:
    """Durably create one exclusive outer-approval consumption marker."""
    claim = build_publication_regeneration_attempt_claim(plan, approval)
    path = publication_regeneration_attempt_claim_path(
        ledger_directory,
        claim.consumption_key,
    )
    contents = publication_regeneration_attempt_claim_canonical_bytes(claim)
    try:
        handle = path.open("xb")
    except FileExistsError:
        _raise_attempt_already_consumed()
    except OSError:
        _raise_claim_persistence("create")

    try:
        with handle:
            written = handle.write(contents)
            if written != len(contents):
                raise OSError("short claim write")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_claim_directory(ledger_directory)
    except OSError:
        # The exclusive marker is deliberately never deleted after creation.
        _raise_claim_persistence("ambiguous")
    return claim


def load_publication_regeneration_attempt_claim(
    path: Path,
) -> PublicationRegenerationAttemptClaim:
    """Read and strictly revalidate one immutable canonical claim marker."""
    _validate_claim_path(path, _raise_claim_load)
    try:
        contents = path.read_bytes()
    except OSError:
        _raise_claim_load("target")
    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_attempt_claim_keys,
        )
        claim = _parse_publication_regeneration_attempt_claim(value)
        if publication_regeneration_attempt_claim_canonical_bytes(claim) != contents:
            _raise_claim_load("noncanonical")
        return claim
    except PublicationRegenerationAttemptClaimLoadError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateAttemptClaimKeyError):
        _raise_claim_load("parse")
    except PublicationRegenerationAttemptClaimError:
        _raise_claim_load("record")
    except (TypeError, ValueError, AttributeError):
        _raise_claim_load("record")


def _validate_publication_regeneration_plan(
    plan: PublicationRegenerationPlan,
) -> None:
    if type(plan) is not PublicationRegenerationPlan:
        _raise_plan("plan_type")
    if type(plan.schema_version) is not str or (
        plan.schema_version != _PUBLICATION_REGENERATION_PLAN_SCHEMA_VERSION
    ):
        _raise_plan("schema_version")
    _validate_regeneration_id(plan.regeneration_id)
    _validate_bounded_identifier(plan.workflow_id, "workflow_id")
    _validate_digest(plan.source_audit_sha256, "source_audit_digest")
    _validate_digest(
        plan.source_post_terminal_facts_sha256,
        "source_facts_digest",
    )
    if plan.source_business_output_sha256 is not None:
        _validate_digest(
            plan.source_business_output_sha256,
            "source_output_digest",
        )
    if type(plan.source_readiness) is not str or plan.source_readiness not in {
        "stale_or_inconsistent",
        "insufficient_evidence",
    }:
        _raise_plan("source_readiness")
    if (
        type(plan.source_reason_codes) is not tuple
        or not plan.source_reason_codes
        or any(
            type(reason) is not str or reason not in _PLAN_REASON_CODES
            for reason in plan.source_reason_codes
        )
        or len(set(plan.source_reason_codes)) != len(plan.source_reason_codes)
        or tuple(sorted(plan.source_reason_codes, key=_PLAN_REASON_ORDER.index))
        != plan.source_reason_codes
    ):
        _raise_plan("source_reason_codes")
    _validate_source_assessment_shape(
        plan.source_readiness,
        plan.source_reason_codes,
        plan.source_business_output_sha256,
    )
    if (
        type(plan.provider) is not str
        or plan.provider not in SUPPORTED_EXECUTION_PROVIDERS
    ):
        _raise_plan("provider")
    _validate_digest(plan.execution_target_sha256, "target_digest")
    try:
        expected_target_digest = execution_target_fingerprint(
            execution_target_for_name(plan.provider)
        )
    except (ModelExecutionTargetError, TypeError, ValueError):
        _raise_plan("target_binding")
    if plan.execution_target_sha256 != expected_target_digest:
        _raise_plan("target_binding")
    _validate_digest(plan.invocation_request_sha256, "request_digest")


def _validate_publication_regeneration_approval(
    approval: PublicationRegenerationApproval,
) -> None:
    if type(approval) is not PublicationRegenerationApproval:
        _raise_approval("approval_type")
    if type(approval.approved) is not bool or approval.approved is not True:
        _raise_approval("approved")
    _validate_digest(approval.regeneration_plan_sha256, "plan_digest", error="approval")
    _validate_approval_metadata(approval.approved_by, approval.approval_id)


def _validate_publication_regeneration_attempt_claim(
    claim: PublicationRegenerationAttemptClaim,
) -> None:
    if type(claim) is not PublicationRegenerationAttemptClaim:
        _raise_attempt_claim("claim_type")
    if type(claim.schema_version) is not str or (
        claim.schema_version != _PUBLICATION_REGENERATION_ATTEMPT_SCHEMA_VERSION
    ):
        _raise_attempt_claim("schema_version")
    _validate_claim_consumption_key(claim.consumption_key)
    _validate_claim_regeneration_id(claim.regeneration_id)
    _validate_claim_digest(claim.regeneration_plan_sha256, "plan_digest")
    _validate_claim_digest(
        claim.regeneration_approval_sha256,
        "approval_digest",
    )
    _validate_attempt_claim_approval_id(claim.approval_id)
    _validate_attempt_claim_metadata(claim.approved_by, "approved_by")
    _validate_claim_digest(claim.source_audit_sha256, "source_audit_digest")
    if (
        type(claim.provider) is not str
        or claim.provider not in SUPPORTED_EXECUTION_PROVIDERS
    ):
        _raise_attempt_claim("provider")
    _validate_claim_digest(claim.execution_target_sha256, "target_digest")
    try:
        expected_target_digest = execution_target_fingerprint(
            execution_target_for_name(claim.provider)
        )
    except (ModelExecutionTargetError, TypeError, ValueError):
        _raise_attempt_claim("target_binding")
    if claim.execution_target_sha256 != expected_target_digest:
        _raise_attempt_claim("target_binding")
    _validate_claim_digest(claim.invocation_request_sha256, "request_digest")
    if type(claim.state) is not str or claim.state != "claimed":
        _raise_attempt_claim("state")

    try:
        approval = PublicationRegenerationApproval(
            approved=True,
            regeneration_plan_sha256=claim.regeneration_plan_sha256,
            approved_by=claim.approved_by,
            approval_id=claim.approval_id,
        )
    except PublicationRegenerationApprovalError:
        _raise_attempt_claim("approval")
    if claim.consumption_key != publication_regeneration_consumption_key(approval):
        _raise_attempt_claim("consumption_key")
    if claim.regeneration_approval_sha256 != publication_regeneration_approval_digest(
        approval
    ):
        _raise_attempt_claim("approval_binding")


def _validate_claim_regeneration_id(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_IDENTIFIER_LENGTH
        or _REGENERATION_ID_PATTERN.fullmatch(value) is None
    ):
        _raise_attempt_claim("regeneration_id")


def _validate_attempt_claim_metadata(value: object, classification: str) -> None:
    if type(value) is not str or not value or len(value) > _MAX_METADATA_LENGTH:
        _raise_attempt_claim(classification)


def _validate_attempt_claim_approval_id(value: object) -> None:
    _validate_attempt_claim_metadata(value, "approval_id")


def _validate_claim_digest(value: object, classification: str) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        _raise_attempt_claim(classification)


def _validate_claim_consumption_key(value: object) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        _raise_attempt_claim("consumption_key")


def _validate_existing_ledger_directory(
    path: Path,
    error: Callable[[str], None],
) -> None:
    if type(path) is not type(Path()):
        error("ledger_directory_type")
    try:
        if not path.exists() or not path.is_dir():
            error("ledger_directory")
    except OSError:
        error("ledger_directory")


def _validate_claim_path(path: Path, error: Callable[[str], None]) -> None:
    if type(path) is not type(Path()):
        error("path_type")
    try:
        if path.is_dir():
            error("target")
    except OSError:
        error("target")


def _fsync_claim_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reject_duplicate_attempt_claim_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateAttemptClaimKeyError
        result[key] = value
    return result


class _DuplicateAttemptClaimKeyError(ValueError):
    pass


def _parse_publication_regeneration_attempt_claim(
    value: object,
) -> PublicationRegenerationAttemptClaim:
    if type(value) is not dict or frozenset(value) != _ATTEMPT_CLAIM_KEYS:
        _raise_claim_load("keys")
    try:
        return PublicationRegenerationAttemptClaim(
            schema_version=value["schema_version"],
            consumption_key=value["consumption_key"],
            regeneration_id=value["regeneration_id"],
            regeneration_plan_sha256=value["regeneration_plan_sha256"],
            regeneration_approval_sha256=value["regeneration_approval_sha256"],
            approval_id=value["approval_id"],
            approved_by=value["approved_by"],
            source_audit_sha256=value["source_audit_sha256"],
            provider=value["provider"],
            execution_target_sha256=value["execution_target_sha256"],
            invocation_request_sha256=value["invocation_request_sha256"],
            state=value["state"],
        )
    except PublicationRegenerationAttemptClaimError:
        _raise_claim_load("record")
    except (KeyError, TypeError, ValueError):
        _raise_claim_load("record")


def _validate_source_audit(audit_record: object) -> str:
    if type(audit_record) is not PublicationReadinessAuditRecord:
        _raise_plan("source_audit_type")
    try:
        return publication_readiness_audit_digest(audit_record)
    except (PublicationReadinessAuditError, TypeError, ValueError, AttributeError):
        _raise_plan("source_audit")


def _validate_invocation_request(request: object) -> None:
    if type(request) is not ModelInvocationRequest:
        _raise_plan("request_type")
    if not all(
        type(getattr(request, name)) is str
        for name in ("model", "system_instructions", "task_instructions")
    ):
        _raise_plan("request_shape")
    if type(request.allowed_tools) is not tuple or any(
        type(tool_name) is not str for tool_name in request.allowed_tools
    ):
        _raise_plan("request_shape")
    if type(request.upstream_inputs) is not tuple:
        _raise_plan("request_shape")
    for upstream in request.upstream_inputs:
        if type(upstream) is not UpstreamStepOutput:
            _raise_plan("request_shape")
        if (
            type(upstream.workflow_id) is not str
            or type(upstream.step_id) is not str
            or type(upstream.step_index) is not int
            or type(upstream.employee_id) is not str
            or type(upstream.output_text) is not str
        ):
            _raise_plan("request_shape")
    if type(request.runtime_facts) is not RuntimeFactsSnapshot:
        _raise_plan("request_shape")


def _validate_resolved_tools(resolved_tools: object) -> None:
    if type(resolved_tools) is not tuple:
        _raise_plan("tools_type")
    for tool in resolved_tools:
        if type(tool) is not ToolDefinition:
            _raise_plan("tools_type")
        if (
            type(tool.name) is not str
            or type(tool.description) is not str
            or type(tool.parameters) is not tuple
        ):
            _raise_plan("tools_shape")
        for parameter in tool.parameters:
            if type(parameter) is not ToolParameterDefinition or not (
                type(parameter.name) is str
                and type(parameter.description) is str
                and type(parameter.type) is str
                and type(parameter.required) is bool
            ):
                _raise_plan("tools_shape")


def _validate_target(execution_target: object) -> ModelExecutionTarget:
    if type(execution_target) is not ModelExecutionTarget:
        _raise_plan("target_type")
    try:
        return validate_execution_target_for_provider(execution_target)
    except (ModelExecutionTargetError, TypeError, ValueError):
        _raise_plan("target")


def _validate_regeneration_id(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_IDENTIFIER_LENGTH
        or _REGENERATION_ID_PATTERN.fullmatch(value) is None
    ):
        _raise_plan("regeneration_id")


def _validate_bounded_identifier(value: object, classification: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_IDENTIFIER_LENGTH
        or _REGENERATION_ID_PATTERN.fullmatch(value) is None
    ):
        _raise_plan(classification)


def _validate_source_assessment_shape(
    readiness: str,
    reason_codes: tuple[str, ...],
    business_output_sha256: str | None,
) -> None:
    """Keep direct plan construction aligned with Phase 263 semantics."""
    if readiness == "insufficient_evidence":
        if reason_codes == ("claim_contract_missing",):
            if business_output_sha256 is None:
                _raise_plan("source_output_digest")
            return
        if reason_codes in {
            ("execution_not_workflow_complete",),
            ("execution_not_workflow_complete", "final_output_missing"),
        }:
            return
    elif readiness == "stale_or_inconsistent":
        if reason_codes == ("final_output_missing",):
            return
        if reason_codes in {
            ("final_output_mismatch",),
            ("claim_contract_mismatch",),
        } and business_output_sha256 is not None:
            return
    _raise_plan("source_assessment")


def _validate_digest(
    value: object,
    classification: str,
    *,
    error: str = "plan",
) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        if error == "approval":
            _raise_approval(classification)
        _raise_plan(classification)


def _validate_approval_metadata(approved_by: object, approval_id: object) -> None:
    for value, classification in (
        (approved_by, "approved_by"),
        (approval_id, "approval_id"),
    ):
        if (
            type(value) is not str
            or not value
            or len(value) > _MAX_METADATA_LENGTH
        ):
            _raise_approval(classification)


def _raise_plan(classification: str) -> None:
    raise PublicationRegenerationPlanError(classification) from None


def _raise_approval(classification: str) -> None:
    raise PublicationRegenerationApprovalError(classification) from None


def _raise_attempt_claim(classification: str) -> None:
    raise PublicationRegenerationAttemptClaimError(classification) from None


def _raise_claim_persistence(classification: str) -> None:
    raise PublicationRegenerationAttemptClaimPersistenceError(classification) from None


def _raise_attempt_already_consumed() -> None:
    raise PublicationRegenerationAttemptAlreadyConsumedError(
        "already_consumed"
    ) from None


def _raise_claim_load(classification: str) -> None:
    raise PublicationRegenerationAttemptClaimLoadError(classification) from None


__all__ = [
    "PublicationRegenerationApproval",
    "PublicationRegenerationApprovalError",
    "PublicationRegenerationAttemptAlreadyConsumedError",
    "PublicationRegenerationAttemptClaim",
    "PublicationRegenerationAttemptClaimError",
    "PublicationRegenerationAttemptClaimLoadError",
    "PublicationRegenerationAttemptClaimPersistenceError",
    "PublicationRegenerationError",
    "PublicationRegenerationFailureDetail",
    "PublicationRegenerationPlan",
    "PublicationRegenerationPlanError",
    "PublicationRegenerationReadiness",
    "approve_publication_regeneration",
    "build_publication_regeneration_attempt_claim",
    "build_publication_regeneration_plan",
    "claim_publication_regeneration_attempt",
    "load_publication_regeneration_attempt_claim",
    "publication_regeneration_approval_canonical_bytes",
    "publication_regeneration_approval_digest",
    "publication_regeneration_attempt_claim_canonical_bytes",
    "publication_regeneration_attempt_claim_digest",
    "publication_regeneration_attempt_claim_path",
    "publication_regeneration_consumption_key",
    "publication_regeneration_consumption_key_from_approval_id",
    "publication_regeneration_plan_canonical_bytes",
    "publication_regeneration_plan_digest",
    "serialize_publication_regeneration_plan_canonical",
    "serialize_publication_regeneration_approval_canonical",
    "serialize_publication_regeneration_attempt_claim_canonical",
    "validate_publication_regeneration_approval",
    "validate_publication_regeneration_plan",
]
