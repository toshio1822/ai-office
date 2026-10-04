"""Run-bound immutable evidence for Business, Execution, and Recovery Approval.

The three approval purposes remain distinct. Publication Approval has a
separate contract and is never accepted by these records.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    load_workflow_run_manifest,
)
from ai_office.execution_target import (
    ModelExecutionTarget,
    execution_target_fingerprint,
    validate_execution_target_for_provider,
)
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationRequest,
    validate_model_invocation_execution_approval,
)
from ai_office.runtime import WorkflowRunBinding, binding_of

_BUSINESS_SCHEMA_VERSION = "workflow-business-approval-evidence.v1"
_EXECUTION_SCHEMA_VERSION = "workflow-execution-approval-evidence.v1"
_RECOVERY_SCHEMA_VERSION = "workflow-recovery-approval-evidence.v1"
_BUSINESS_PREFIX = "business-approval"
_EXECUTION_PREFIX = "execution-approval"
_RECOVERY_PREFIX = "recovery-approval"
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DEFINITION_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())


class WorkflowApprovalEvidenceError(ValueError):
    """Raised when approval evidence is not an exact safe contract."""

    def __init__(self, classification: str = "contract") -> None:
        super().__init__("workflow approval evidence is invalid")
        self.classification = classification


class WorkflowApprovalEvidencePersistenceError(WorkflowApprovalEvidenceError):
    """Raised when evidence persistence is unavailable or ambiguous."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, "workflow approval evidence persistence failed")
        self.classification = classification


class WorkflowApprovalEvidenceConflictError(WorkflowApprovalEvidencePersistenceError):
    """Raised when immutable evidence identity already contains different bytes."""


class WorkflowApprovalEvidenceLoadError(WorkflowApprovalEvidenceError):
    """Raised when persisted evidence is malformed or noncanonical."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, "workflow approval evidence could not be loaded")
        self.classification = classification


@dataclass(frozen=True)
class BusinessApprovalEvidence:
    """Affirmative Business Approval for one exact Run progression point."""

    schema_version: Literal["workflow-business-approval-evidence.v1"]
    purpose: Literal["business_approval"]
    approved: bool
    run_id: str
    manifest_digest: str
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    progression_from_step_id: str | None
    progression_from_step_index: int | None
    approved_by: str
    approval_id: str

    def __post_init__(self) -> None:
        _validate_business(self)

    @property
    def digest(self) -> str:
        return sha256(business_approval_evidence_canonical_bytes(self)).hexdigest()


@dataclass(frozen=True)
class ExecutionApprovalEvidence:
    """Validated Execution Approval for one exact invocation and target."""

    schema_version: Literal["workflow-execution-approval-evidence.v1"]
    purpose: Literal["execution_approval"]
    approved: bool
    run_id: str
    manifest_digest: str
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    provider: str
    execution_target_fingerprint: str
    request_fingerprint: str
    approved_by: str
    approval_id: str

    def __post_init__(self) -> None:
        _validate_execution(self)

    @property
    def digest(self) -> str:
        return sha256(execution_approval_evidence_canonical_bytes(self)).hexdigest()


@dataclass(frozen=True)
class RecoveryApprovalEvidence:
    """Affirmative Recovery Approval for one exact persisted decision."""

    schema_version: Literal["workflow-recovery-approval-evidence.v1"]
    purpose: Literal["recovery_approval"]
    approved: bool
    run_id: str
    manifest_digest: str
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    recovery_action: str
    recovery_decision_sha256: str
    state_sha256: str
    events_sha256: str
    previous_attempt_id: str
    previous_attempt_evidence_sha256: str
    approved_by: str
    approval_id: str

    def __post_init__(self) -> None:
        _validate_recovery(self)

    @property
    def digest(self) -> str:
        return sha256(recovery_approval_evidence_canonical_bytes(self)).hexdigest()


def build_recovery_approval_evidence(
    assessment: object,
    *,
    approved_by: str,
    approval_id: str,
) -> RecoveryApprovalEvidence:
    """Bind operator approval to one read-only authoritative recovery decision."""
    try:
        if assessment.eligible is not True or assessment.action not in {
            "complete_result",
            "complete_raw_response",
            "retry_failed",
            "retry_ambiguous",
        }:
            _raise("recovery_decision")
        values = {
            "run_id": assessment.run_id,
            "manifest_digest": assessment.manifest_digest,
            "workflow_id": assessment.workflow_id,
            "step_id": assessment.step_id,
            "step_index": assessment.step_index,
            "employee_id": assessment.employee_id,
            "recovery_action": assessment.action,
            "recovery_decision_sha256": assessment.digest,
            "state_sha256": assessment.state_sha256,
            "events_sha256": assessment.events_sha256,
            "previous_attempt_id": assessment.previous_attempt_id,
            "previous_attempt_evidence_sha256": (
                assessment.previous_attempt_evidence_sha256
            ),
        }
    except (AttributeError, TypeError):
        _raise("recovery_decision")
    return RecoveryApprovalEvidence(
        schema_version=_RECOVERY_SCHEMA_VERSION,
        purpose="recovery_approval",
        approved=True,
        **values,
        approved_by=approved_by,
        approval_id=approval_id,
    )


def validate_recovery_approval_evidence(
    evidence: object, assessment: object
) -> RecoveryApprovalEvidence:
    """Reject an approval that does not authorize the exact current decision."""
    if type(evidence) is not RecoveryApprovalEvidence:
        _raise("recovery_approval")
    try:
        exact = (
            evidence.run_id == assessment.run_id
            and evidence.manifest_digest == assessment.manifest_digest
            and evidence.workflow_id == assessment.workflow_id
            and evidence.step_id == assessment.step_id
            and evidence.step_index == assessment.step_index
            and evidence.employee_id == assessment.employee_id
            and evidence.recovery_action == assessment.action
            and evidence.recovery_decision_sha256 == assessment.digest
            and evidence.state_sha256 == assessment.state_sha256
            and evidence.events_sha256 == assessment.events_sha256
            and evidence.previous_attempt_id == assessment.previous_attempt_id
            and evidence.previous_attempt_evidence_sha256
            == assessment.previous_attempt_evidence_sha256
        )
    except (AttributeError, TypeError):
        exact = False
    if not exact:
        _raise("recovery_binding")
    return evidence


def approve_business_step(
    *,
    binding: WorkflowRunBinding,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    approved_by: str,
    approval_id: str,
    progression_from_step_id: str | None = None,
    progression_from_step_index: int | None = None,
) -> BusinessApprovalEvidence:
    """Build one affirmative Business Approval without any external effect."""
    if type(binding) is not WorkflowRunBinding:
        _raise("run_binding")
    return BusinessApprovalEvidence(
        schema_version=_BUSINESS_SCHEMA_VERSION,
        purpose="business_approval",
        approved=True,
        run_id=binding.run_id,
        manifest_digest=binding.manifest_digest,
        workflow_id=workflow_id,
        step_id=step_id,
        step_index=step_index,
        employee_id=employee_id,
        progression_from_step_id=progression_from_step_id,
        progression_from_step_index=progression_from_step_index,
        approved_by=approved_by,
        approval_id=approval_id,
    )


def build_execution_approval_evidence(
    request: ModelInvocationRequest,
    approval: ModelInvocationExecutionApproval,
    *,
    resolved_tools: tuple[object, ...] = (),
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    target: ModelExecutionTarget,
) -> ExecutionApprovalEvidence:
    """Build durable evidence only after the exact approval validates."""
    binding = binding_of(request)
    if binding is None:
        _raise("run_binding")
    try:
        target = validate_execution_target_for_provider(
            target, provider=approval.provider
        )
        validate_model_invocation_execution_approval(
            request,
            resolved_tools,  # type: ignore[arg-type]
            approval,
            provider=approval.provider,
            execution_target=target,
        )
    except Exception:
        _raise("execution_approval")
    return _execution_evidence_from_approval(
        binding,
        workflow_id,
        step_id,
        step_index,
        employee_id,
        target,
        approval,
    )


def build_execution_approval_evidence_for_tools(
    request: ModelInvocationRequest,
    resolved_tools: tuple[object, ...],
    approval: ModelInvocationExecutionApproval,
    *,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    target: ModelExecutionTarget,
) -> ExecutionApprovalEvidence:
    """Build evidence after the public execution approval contract validates."""
    binding = binding_of(request)
    if binding is None or type(resolved_tools) is not tuple:
        _raise("run_binding" if binding is None else "execution_approval")
    try:
        target = validate_execution_target_for_provider(
            target, provider=approval.provider
        )
        validate_model_invocation_execution_approval(
            request,
            resolved_tools,  # type: ignore[arg-type]
            approval,
            provider=approval.provider,
            execution_target=target,
        )
    except Exception:
        _raise("execution_approval")
    return _execution_evidence_from_approval(
        binding,
        workflow_id,
        step_id,
        step_index,
        employee_id,
        target,
        approval,
    )


def validate_business_approval_evidence(
    evidence: object,
    *,
    binding: WorkflowRunBinding,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    progression_from_step_id: str | None,
    progression_from_step_index: int | None,
) -> BusinessApprovalEvidence:
    """Validate one Business Approval against the exact pinned step meaning."""
    if type(evidence) is not BusinessApprovalEvidence:
        _raise("business_approval")
    assert isinstance(evidence, BusinessApprovalEvidence)
    if (
        evidence.run_id != binding.run_id
        or evidence.manifest_digest != binding.manifest_digest
        or evidence.workflow_id != workflow_id
        or evidence.step_id != step_id
        or evidence.step_index != step_index
        or evidence.employee_id != employee_id
        or evidence.progression_from_step_id != progression_from_step_id
        or evidence.progression_from_step_index != progression_from_step_index
    ):
        _raise("business_binding")
    return evidence


def validate_execution_approval_evidence(
    evidence: object,
    *,
    binding: WorkflowRunBinding,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    provider: str,
    execution_target_fingerprint_value: str,
    request_fingerprint: str,
) -> ExecutionApprovalEvidence:
    """Validate persisted Execution Approval evidence for one exact invocation."""
    if type(evidence) is not ExecutionApprovalEvidence:
        _raise("execution_approval")
    assert isinstance(evidence, ExecutionApprovalEvidence)
    if (
        evidence.run_id != binding.run_id
        or evidence.manifest_digest != binding.manifest_digest
        or evidence.workflow_id != workflow_id
        or evidence.step_id != step_id
        or evidence.step_index != step_index
        or evidence.employee_id != employee_id
        or evidence.provider != provider
        or evidence.execution_target_fingerprint != execution_target_fingerprint_value
        or evidence.request_fingerprint != request_fingerprint
    ):
        _raise("execution_binding")
    return evidence


def persist_business_approval_evidence(
    store: WorkflowRunManifestStore,
    evidence: BusinessApprovalEvidence,
) -> BusinessApprovalEvidence:
    """Exclusively persist or idempotently accept one Business Approval."""
    _validate_store_and_manifest(store, evidence.run_id, evidence.manifest_digest)
    _persist_evidence(
        _evidence_path(store, evidence.run_id, _BUSINESS_PREFIX, evidence.approval_id),
        business_approval_evidence_canonical_bytes(evidence),
    )
    return evidence


def persist_execution_approval_evidence(
    store: WorkflowRunManifestStore,
    evidence: ExecutionApprovalEvidence,
) -> ExecutionApprovalEvidence:
    """Exclusively persist or idempotently accept one Execution Approval."""
    _validate_store_and_manifest(store, evidence.run_id, evidence.manifest_digest)
    _persist_evidence(
        _evidence_path(store, evidence.run_id, _EXECUTION_PREFIX, evidence.approval_id),
        execution_approval_evidence_canonical_bytes(evidence),
    )
    return evidence


def persist_recovery_approval_evidence(
    store: WorkflowRunManifestStore,
    evidence: RecoveryApprovalEvidence,
) -> RecoveryApprovalEvidence:
    """Exclusively persist or idempotently accept one Recovery Approval."""
    _validate_store_and_manifest(store, evidence.run_id, evidence.manifest_digest)
    _persist_evidence(
        _evidence_path(store, evidence.run_id, _RECOVERY_PREFIX, evidence.approval_id),
        recovery_approval_evidence_canonical_bytes(evidence),
    )
    return evidence


def load_business_approval_evidence(
    store: WorkflowRunManifestStore, run_id: str, approval_id: str
) -> BusinessApprovalEvidence:
    """Strictly load one Business Approval from the authoritative Run namespace."""
    manifest = _validate_store_and_manifest(store, run_id, None)
    path = _evidence_path(store, run_id, _BUSINESS_PREFIX, approval_id)
    value = _load_evidence(
        path,
        expected_run_id=run_id,
        expected_approval_id=approval_id,
        expected_prefix=_BUSINESS_PREFIX,
    )
    if type(value) is not BusinessApprovalEvidence:
        _raise_load("purpose")
    if value.manifest_digest != manifest.digest:
        _raise_load("manifest")
    return value


def load_execution_approval_evidence(
    store: WorkflowRunManifestStore, run_id: str, approval_id: str
) -> ExecutionApprovalEvidence:
    """Strictly load one Execution Approval from the authoritative Run namespace."""
    manifest = _validate_store_and_manifest(store, run_id, None)
    path = _evidence_path(store, run_id, _EXECUTION_PREFIX, approval_id)
    value = _load_evidence(
        path,
        expected_run_id=run_id,
        expected_approval_id=approval_id,
        expected_prefix=_EXECUTION_PREFIX,
    )
    if type(value) is not ExecutionApprovalEvidence:
        _raise_load("purpose")
    if value.manifest_digest != manifest.digest:
        _raise_load("manifest")
    return value


def load_recovery_approval_evidence(
    store: WorkflowRunManifestStore, run_id: str, approval_id: str
) -> RecoveryApprovalEvidence:
    """Strictly load one Recovery Approval from the authoritative Run namespace."""
    manifest = _validate_store_and_manifest(store, run_id, None)
    path = _evidence_path(store, run_id, _RECOVERY_PREFIX, approval_id)
    value = _load_evidence(
        path,
        expected_run_id=run_id,
        expected_approval_id=approval_id,
        expected_prefix=_RECOVERY_PREFIX,
    )
    if type(value) is not RecoveryApprovalEvidence:
        _raise_load("purpose")
    if value.manifest_digest != manifest.digest:
        _raise_load("manifest")
    return value


def find_business_approval_evidence(
    store: WorkflowRunManifestStore,
    *,
    binding: WorkflowRunBinding,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    progression_from_step_id: str | None,
    progression_from_step_index: int | None,
) -> BusinessApprovalEvidence | None:
    """Find the sole exact durable Business Approval, or fail on ambiguity."""
    _validate_store_and_manifest(store, binding.run_id, binding.manifest_digest)
    matches: list[BusinessApprovalEvidence] = []
    prefix = f"{binding.run_id}.{_BUSINESS_PREFIX}."
    for path in sorted(store.root.glob(f"{prefix}*.json"), key=lambda item: item.name):
        value = _load_evidence(
            path,
            expected_run_id=binding.run_id,
            expected_approval_id=None,
            expected_prefix=_BUSINESS_PREFIX,
        )
        if type(value) is not BusinessApprovalEvidence:
            _raise_load("purpose")
        try:
            validate_business_approval_evidence(
                value,
                binding=binding,
                workflow_id=workflow_id,
                step_id=step_id,
                step_index=step_index,
                employee_id=employee_id,
                progression_from_step_id=progression_from_step_id,
                progression_from_step_index=progression_from_step_index,
            )
        except WorkflowApprovalEvidenceError:
            continue
        matches.append(value)
    if len(matches) > 1:
        _raise("ambiguous")
    return matches[0] if matches else None


def list_run_approval_evidence(
    store: WorkflowRunManifestStore, run_id: str
) -> tuple[
    BusinessApprovalEvidence | ExecutionApprovalEvidence | RecoveryApprovalEvidence,
    ...,
]:
    """Read all strict approval evidence for one Run without external effects."""
    _validate_store_and_manifest(store, run_id, None)
    values: list[
        BusinessApprovalEvidence | ExecutionApprovalEvidence | RecoveryApprovalEvidence
    ] = []
    for prefix in (_BUSINESS_PREFIX, _EXECUTION_PREFIX, _RECOVERY_PREFIX):
        marker = f"{run_id}.{prefix}."
        for path in sorted(
            store.root.glob(f"{marker}*.json"), key=lambda item: item.name
        ):
            value = _load_evidence(
                path,
                expected_run_id=run_id,
                expected_approval_id=None,
                expected_prefix=prefix,
            )
            if (
                prefix == _BUSINESS_PREFIX
                and type(value) is not BusinessApprovalEvidence
            ):
                _raise_load("purpose")
            if (
                prefix == _EXECUTION_PREFIX
                and type(value) is not ExecutionApprovalEvidence
            ):
                _raise_load("purpose")
            if (
                prefix == _RECOVERY_PREFIX
                and type(value) is not RecoveryApprovalEvidence
            ):
                _raise_load("purpose")
            values.append(value)
    return tuple(values)


def business_approval_evidence_canonical_bytes(
    evidence: BusinessApprovalEvidence,
) -> bytes:
    _validate_business(evidence)
    return _canonical_bytes(_business_dict(evidence))


def execution_approval_evidence_canonical_bytes(
    evidence: ExecutionApprovalEvidence,
) -> bytes:
    _validate_execution(evidence)
    return _canonical_bytes(_execution_dict(evidence))


def recovery_approval_evidence_canonical_bytes(
    evidence: RecoveryApprovalEvidence,
) -> bytes:
    _validate_recovery(evidence)
    return _canonical_bytes(_recovery_dict(evidence))


def _execution_evidence_from_approval(
    binding: WorkflowRunBinding,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    target: ModelExecutionTarget,
    approval: ModelInvocationExecutionApproval,
) -> ExecutionApprovalEvidence:
    return ExecutionApprovalEvidence(
        schema_version=_EXECUTION_SCHEMA_VERSION,
        purpose="execution_approval",
        approved=True,
        run_id=binding.run_id,
        manifest_digest=binding.manifest_digest,
        workflow_id=workflow_id,
        step_id=step_id,
        step_index=step_index,
        employee_id=employee_id,
        provider=approval.provider,
        execution_target_fingerprint=execution_target_fingerprint(target),
        request_fingerprint=approval.request_fingerprint,
        approved_by=approval.approved_by,
        approval_id=approval.approval_id,
    )


def _business_dict(evidence: BusinessApprovalEvidence) -> dict[str, object]:
    return {
        "approved": evidence.approved,
        "approved_by": evidence.approved_by,
        "approval_id": evidence.approval_id,
        "employee_id": evidence.employee_id,
        "manifest_digest": evidence.manifest_digest,
        "progression_from_step_id": evidence.progression_from_step_id,
        "progression_from_step_index": evidence.progression_from_step_index,
        "purpose": evidence.purpose,
        "run_id": evidence.run_id,
        "schema_version": evidence.schema_version,
        "step_id": evidence.step_id,
        "step_index": evidence.step_index,
        "workflow_id": evidence.workflow_id,
    }


def _execution_dict(evidence: ExecutionApprovalEvidence) -> dict[str, object]:
    return {
        "approved": evidence.approved,
        "approved_by": evidence.approved_by,
        "approval_id": evidence.approval_id,
        "employee_id": evidence.employee_id,
        "execution_target_fingerprint": evidence.execution_target_fingerprint,
        "manifest_digest": evidence.manifest_digest,
        "provider": evidence.provider,
        "purpose": evidence.purpose,
        "request_fingerprint": evidence.request_fingerprint,
        "run_id": evidence.run_id,
        "schema_version": evidence.schema_version,
        "step_id": evidence.step_id,
        "step_index": evidence.step_index,
        "workflow_id": evidence.workflow_id,
    }


def _recovery_dict(evidence: RecoveryApprovalEvidence) -> dict[str, object]:
    return {
        "approved": evidence.approved,
        "approved_by": evidence.approved_by,
        "approval_id": evidence.approval_id,
        "employee_id": evidence.employee_id,
        "events_sha256": evidence.events_sha256,
        "manifest_digest": evidence.manifest_digest,
        "previous_attempt_evidence_sha256": evidence.previous_attempt_evidence_sha256,
        "previous_attempt_id": evidence.previous_attempt_id,
        "purpose": evidence.purpose,
        "recovery_action": evidence.recovery_action,
        "recovery_decision_sha256": evidence.recovery_decision_sha256,
        "run_id": evidence.run_id,
        "schema_version": evidence.schema_version,
        "state_sha256": evidence.state_sha256,
        "step_id": evidence.step_id,
        "step_index": evidence.step_index,
        "workflow_id": evidence.workflow_id,
    }


def _validate_business(value: object) -> None:
    if type(value) is not BusinessApprovalEvidence:
        _raise("business_type")
    assert isinstance(value, BusinessApprovalEvidence)
    if value.schema_version != _BUSINESS_SCHEMA_VERSION:
        _raise("schema_version")
    if value.purpose != "business_approval" or value.approved is not True:
        _raise("purpose")
    _validate_common(value)
    _validate_definition_id(value.workflow_id)
    _validate_definition_id(value.step_id)
    _validate_definition_id(value.employee_id)
    _validate_step_index(value.step_index)
    if (value.progression_from_step_id is None) != (
        value.progression_from_step_index is None
    ):
        _raise("progression")
    if value.progression_from_step_id is not None:
        _validate_definition_id(value.progression_from_step_id)
        _validate_step_index(value.progression_from_step_index)
    _validate_metadata(value.approved_by, "approved_by")
    _validate_metadata(value.approval_id, "approval_id")


def _validate_execution(value: object) -> None:
    if type(value) is not ExecutionApprovalEvidence:
        _raise("execution_type")
    assert isinstance(value, ExecutionApprovalEvidence)
    if value.schema_version != _EXECUTION_SCHEMA_VERSION:
        _raise("schema_version")
    if value.purpose != "execution_approval" or value.approved is not True:
        _raise("purpose")
    _validate_common(value)
    _validate_definition_id(value.workflow_id)
    _validate_definition_id(value.step_id)
    _validate_definition_id(value.employee_id)
    _validate_step_index(value.step_index)
    _validate_metadata(value.provider, "provider")
    _validate_sha256(value.execution_target_fingerprint, "target")
    _validate_sha256(value.request_fingerprint, "request")
    _validate_metadata(value.approved_by, "approved_by")
    _validate_metadata(value.approval_id, "approval_id")


def _validate_recovery(value: object) -> None:
    if type(value) is not RecoveryApprovalEvidence:
        _raise("recovery_type")
    assert isinstance(value, RecoveryApprovalEvidence)
    if value.schema_version != _RECOVERY_SCHEMA_VERSION:
        _raise("schema_version")
    if value.purpose != "recovery_approval" or value.approved is not True:
        _raise("purpose")
    _validate_common(value)
    _validate_definition_id(value.workflow_id)
    _validate_definition_id(value.step_id)
    _validate_definition_id(value.employee_id)
    _validate_step_index(value.step_index)
    if value.recovery_action not in {
        "complete_result",
        "complete_raw_response",
        "retry_failed",
        "retry_ambiguous",
    }:
        _raise("recovery_action")
    for field, digest in (
        ("decision", value.recovery_decision_sha256),
        ("state", value.state_sha256),
        ("events", value.events_sha256),
        ("attempt", value.previous_attempt_id),
        ("attempt_evidence", value.previous_attempt_evidence_sha256),
    ):
        _validate_sha256(digest, field)
    _validate_metadata(value.approved_by, "approved_by")
    _validate_metadata(value.approval_id, "approval_id")


def _validate_common(value: object) -> None:
    _validate_run_id(value.run_id)
    _validate_sha256(value.manifest_digest, "manifest")


def _validate_step_index(value: object) -> None:
    if type(value) is not int or isinstance(value, bool) or value < 1:
        _raise("step")


def _validate_definition_id(value: object) -> None:
    if type(value) is not str or _DEFINITION_ID_PATTERN.fullmatch(value) is None:
        _raise("identity")


def _validate_run_id(value: object) -> None:
    if type(value) is not str or _RUN_ID_PATTERN.fullmatch(value) is None:
        _raise("run_id")


def _validate_metadata(value: object, classification: str) -> None:
    if not isinstance(value, str) or value == "":
        _raise(classification)


def _validate_sha256(value: object, classification: str) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        _raise(classification)


def _canonical_bytes(value: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _raise("serialization")


def _evidence_path(
    store: WorkflowRunManifestStore, run_id: str, prefix: str, approval_id: str
) -> Path:
    if type(store) is not WorkflowRunManifestStore:
        _raise("store")
    if type(run_id) is not str or _RUN_ID_PATTERN.fullmatch(run_id) is None:
        _raise("run_id")
    if prefix not in {_BUSINESS_PREFIX, _EXECUTION_PREFIX, _RECOVERY_PREFIX}:
        _raise("purpose")
    _validate_metadata(approval_id, "approval_id")
    return store.root / f"{run_id}.{prefix}.{_approval_storage_key(approval_id)}.json"


def _validate_store_and_manifest(
    store: WorkflowRunManifestStore,
    run_id: str,
    manifest_digest: str | None,
) -> object:
    if type(store) is not WorkflowRunManifestStore:
        _raise("store")
    try:
        manifest = load_workflow_run_manifest(store, run_id)
    except Exception:
        _raise("manifest")
    if manifest_digest is not None and manifest.digest != manifest_digest:
        _raise("manifest")
    return manifest


def _persist_evidence(path: Path, contents: bytes) -> None:
    try:
        handle = path.open("xb")
    except FileExistsError:
        _accept_existing_evidence(path, contents)
        return
    except OSError:
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    try:
        with handle:
            written = handle.write(contents)
            if written != len(contents):
                raise OSError("short evidence write")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(path.parent)
    except Exception:
        # The target is retained after exclusive creation.  Strict load decides
        # whether an interrupted write remains usable after restart.
        _raise_persistence("ambiguous")


def _accept_existing_evidence(path: Path, contents: bytes) -> None:
    """Accept exact existing bytes without following or replacing a target."""
    try:
        if path.is_symlink() or not path.is_file():
            _raise_persistence("target")
        existing = path.read_bytes()
    except WorkflowApprovalEvidencePersistenceError:
        raise
    except Exception:
        _raise_persistence("target")
    if existing != contents:
        _raise_conflict()


def _load_evidence(
    path: Path,
    *,
    expected_run_id: str,
    expected_approval_id: str | None,
    expected_prefix: str,
) -> BusinessApprovalEvidence | ExecutionApprovalEvidence | RecoveryApprovalEvidence:
    if type(path) is not _PATH_TYPE or path.is_symlink() or not path.is_file():
        _raise_load("target")
    storage_key = _storage_key_from_path(path, expected_run_id, expected_prefix)
    try:
        contents = path.read_bytes()
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except Exception:
        _raise_load("parse")
    try:
        if type(value) is not dict:
            _raise_load("record")
        purpose = value.get("purpose")
        if purpose == "business_approval":
            if frozenset(value) != frozenset(_business_dict_keys()):
                _raise_load("fields")
            record: BusinessApprovalEvidence | ExecutionApprovalEvidence = (
                BusinessApprovalEvidence(**value)  # type: ignore[arg-type]
            )
        elif purpose == "execution_approval":
            if frozenset(value) != frozenset(_execution_dict_keys()):
                _raise_load("fields")
            record = ExecutionApprovalEvidence(**value)  # type: ignore[arg-type]
        elif purpose == "recovery_approval":
            if frozenset(value) != frozenset(_recovery_dict_keys()):
                _raise_load("fields")
            record = RecoveryApprovalEvidence(**value)  # type: ignore[arg-type]
        else:
            _raise_load("purpose")
    except WorkflowApprovalEvidenceLoadError:
        raise
    except (TypeError, ValueError, AttributeError):
        _raise_load("record")
    if (
        record.run_id != expected_run_id
        or (
            expected_approval_id is not None
            and record.approval_id != expected_approval_id
        )
        or _approval_storage_key(record.approval_id) != storage_key
    ):
        _raise_load("identity")
    if _canonical_bytes(_record_dict(record)) != contents:
        _raise_load("noncanonical")
    return record


def _approval_storage_key(approval_id: str) -> str:
    """Derive a safe filename without narrowing valid approval metadata.

    Keep the original readable sidecar name for the established safe identifier
    shape.  Approval metadata itself is intentionally only required to be a
    non-empty string, so values that cannot be one filename component use their
    SHA-256 storage key instead.
    """
    _validate_metadata(approval_id, "approval_id")
    if _SAFE_FILENAME_PATTERN.fullmatch(approval_id) is not None:
        return approval_id
    try:
        return sha256(approval_id.encode("utf-8")).hexdigest()
    except (UnicodeError, TypeError):
        _raise("approval_id")


def _storage_key_from_path(path: Path, run_id: str, prefix: str) -> str:
    """Extract a safe or hashed approval identity from one sidecar path."""
    marker = f"{run_id}.{prefix}."
    name = path.name
    if not name.startswith(marker) or not name.endswith(".json"):
        _raise_load("identity")
    storage_key = name.removeprefix(marker).removesuffix(".json")
    if (
        _SAFE_FILENAME_PATTERN.fullmatch(storage_key) is None
        and _SHA256_PATTERN.fullmatch(storage_key) is None
    ):
        _raise_load("identity")
    return storage_key


def _record_dict(
    value: (
        BusinessApprovalEvidence
        | ExecutionApprovalEvidence
        | RecoveryApprovalEvidence
    ),
) -> dict[str, object]:
    if type(value) is BusinessApprovalEvidence:
        return _business_dict(value)
    if type(value) is ExecutionApprovalEvidence:
        return _execution_dict(value)
    if type(value) is RecoveryApprovalEvidence:
        return _recovery_dict(value)
    _raise_load("purpose")


def _business_dict_keys() -> tuple[str, ...]:
    return (
        "approved",
        "approved_by",
        "approval_id",
        "employee_id",
        "manifest_digest",
        "progression_from_step_id",
        "progression_from_step_index",
        "purpose",
        "run_id",
        "schema_version",
        "step_id",
        "step_index",
        "workflow_id",
    )


def _execution_dict_keys() -> tuple[str, ...]:
    return (
        "approved",
        "approved_by",
        "approval_id",
        "employee_id",
        "execution_target_fingerprint",
        "manifest_digest",
        "provider",
        "purpose",
        "request_fingerprint",
        "run_id",
        "schema_version",
        "step_id",
        "step_index",
        "workflow_id",
    )


def _recovery_dict_keys() -> tuple[str, ...]:
    return (
        "approved",
        "approved_by",
        "approval_id",
        "employee_id",
        "events_sha256",
        "manifest_digest",
        "previous_attempt_evidence_sha256",
        "previous_attempt_id",
        "purpose",
        "recovery_action",
        "recovery_decision_sha256",
        "run_id",
        "schema_version",
        "state_sha256",
        "step_id",
        "step_index",
        "workflow_id",
    )


class _DuplicateKeyError(ValueError):
    pass


class _NonStandardJSONConstantError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    raise _NonStandardJSONConstantError(value)


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _raise(classification: str) -> NoReturn:
    raise WorkflowApprovalEvidenceError(classification) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise WorkflowApprovalEvidencePersistenceError(classification) from None


def _raise_conflict() -> NoReturn:
    raise WorkflowApprovalEvidenceConflictError("conflict") from None


def _raise_load(classification: str) -> NoReturn:
    raise WorkflowApprovalEvidenceLoadError(classification) from None


__all__ = [
    "BusinessApprovalEvidence",
    "ExecutionApprovalEvidence",
    "RecoveryApprovalEvidence",
    "WorkflowApprovalEvidenceConflictError",
    "WorkflowApprovalEvidenceError",
    "WorkflowApprovalEvidenceLoadError",
    "WorkflowApprovalEvidencePersistenceError",
    "approve_business_step",
    "build_execution_approval_evidence",
    "build_execution_approval_evidence_for_tools",
    "build_recovery_approval_evidence",
    "business_approval_evidence_canonical_bytes",
    "execution_approval_evidence_canonical_bytes",
    "recovery_approval_evidence_canonical_bytes",
    "find_business_approval_evidence",
    "list_run_approval_evidence",
    "load_business_approval_evidence",
    "load_execution_approval_evidence",
    "load_recovery_approval_evidence",
    "persist_business_approval_evidence",
    "persist_execution_approval_evidence",
    "persist_recovery_approval_evidence",
    "validate_business_approval_evidence",
    "validate_execution_approval_evidence",
    "validate_recovery_approval_evidence",
]
