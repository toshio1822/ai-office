"""Explicit, Run-bound recovery decisions derived from durable workflow evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from ai_office.engine.workflow_approval_evidence import (
    RecoveryApprovalEvidence,
    load_recovery_approval_evidence,
    validate_recovery_approval_evidence,
)
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    load_workflow_run_manifest,
)
from ai_office.execution_evidence import (
    ExecutionAttemptEvidence,
    execution_normalized_result_evidence_path,
    execution_raw_response_evidence_path,
    list_run_execution_evidence,
    load_normalized_result_evidence,
    load_raw_response_evidence,
)
from ai_office.runtime import WorkflowRunBinding, binding_of
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history_with_source_digests,
)

RecoveryAction = Literal[
    "complete_result",
    "complete_raw_response",
    "retry_failed",
    "retry_ambiguous",
]


class WorkflowRecoveryError(ValueError):
    """Raised when recovery is not authorized by the current durable Run evidence."""

    def __init__(self, classification: str = "recovery") -> None:
        super().__init__("workflow recovery is unavailable")
        self.classification = classification


@dataclass(frozen=True)
class WorkflowRecoveryAssessment:
    """Safe decision derived from one exact state/history/evidence snapshot."""

    eligible: bool
    reason: str
    action: RecoveryAction | None
    run_id: str
    manifest_digest: str
    workflow_id: str
    state_status: str
    state_sha256: str
    events_sha256: str
    step_id: str | None
    step_index: int | None
    employee_id: str | None
    previous_attempt_id: str | None
    previous_attempt_evidence_sha256: str | None
    invocation_fingerprint: str | None
    provider: str | None
    execution_target_fingerprint: str | None
    raw_response_evidence_sha256: str | None
    normalized_result_evidence_sha256: str | None

    @property
    def digest(self) -> str:
        return sha256(_canonical_bytes(_assessment_dict(self))).hexdigest()


def assess_workflow_recovery(
    store_root: Path, run_id: str
) -> WorkflowRecoveryAssessment:
    """Derive the only safe completion/retry action without writing to the Run."""
    try:
        store = WorkflowRunManifestStore(store_root)
        manifest = load_workflow_run_manifest(store, run_id)
        binding = WorkflowRunBinding(manifest.run_id, manifest.digest)
        state_path, events_path = store.execution_paths(run_id)
        history, state_digest, events_digest = (
            load_workflow_execution_history_with_source_digests(
                WorkflowExecutionPersistenceTargets(
                    state_path, events_path, binding=binding
                )
            )
        )
        state = history.state
        if (
            binding_of(state) != binding
            or state.workflow_id != manifest.workflow_id
            or state.status not in {"running", "failed"}
        ):
            return _ineligible(
                run_id,
                manifest.digest,
                manifest.workflow_id,
                state.status,
                state.current_step_id,
                state.current_step_index,
                state.current_employee_id,
                state_digest,
                events_digest,
                "not_recoverable_state",
            )

        attempts = list_run_execution_evidence(store.root, run_id)
        same_step = tuple(
            attempt
            for attempt in attempts
            if _matches_state_step(attempt, state, binding)
        )
        if not same_step:
            return _ineligible(
                run_id,
                manifest.digest,
                manifest.workflow_id,
                state.status,
                state.current_step_id,
                state.current_step_index,
                state.current_employee_id,
                state_digest,
                events_digest,
                "no_attempt_evidence",
            )

        latest = _latest_attempt(same_step)
        raw_path = execution_raw_response_evidence_path(
            store.root, run_id, latest.attempt_id
        )
        result_path = execution_normalized_result_evidence_path(
            store.root, run_id, latest.attempt_id
        )
        raw_digest: str | None = None
        result_digest: str | None = None
        if raw_path.exists() or raw_path.is_symlink():
            raw_digest = load_raw_response_evidence(
                store.root, run_id, latest.attempt_id
            ).digest
        if result_path.exists() or result_path.is_symlink():
            result_digest = load_normalized_result_evidence(
                store.root, run_id, latest.attempt_id
            ).digest

        linked_terminal = any(
            event.event_type in {"step_succeeded", "step_failed"}
            and event.execution_attempt_id == latest.attempt_id
            and event.execution_attempt_evidence_sha256 == latest.digest
            and event.workflow_id == latest.workflow_id
            and event.step_id == latest.step_id
            and event.step_index == latest.step_index
            and event.employee_id == latest.employee_id
            for event in history.events
        )
        if result_digest is not None and not linked_terminal:
            if state.status != "running":
                raise WorkflowRecoveryError("history")
            action: RecoveryAction = "complete_result"
            reason = "durable_result_requires_terminal_persistence"
        elif raw_digest is not None and result_digest is None and not linked_terminal:
            if state.status != "running":
                raise WorkflowRecoveryError("history")
            action = "complete_raw_response"
            reason = "durable_raw_response_requires_normalization"
        elif state.status == "failed" and linked_terminal:
            action = "retry_failed"
            reason = "terminal_failure_requires_explicit_retry"
        elif state.status == "running" and result_digest is None:
            action = "retry_ambiguous"
            reason = "claimed_attempt_has_no_durable_result"
        elif state.status == "failed" and not linked_terminal and result_digest is None:
            action = "retry_ambiguous"
            reason = "claimed_attempt_has_no_recovery_start_or_terminal_result"
        else:
            return _ineligible(
                run_id,
                manifest.digest,
                manifest.workflow_id,
                state.status,
                state.current_step_id,
                state.current_step_index,
                state.current_employee_id,
                state_digest,
                events_digest,
                "evidence_not_recoverable",
            )

        return WorkflowRecoveryAssessment(
            eligible=True,
            reason=reason,
            action=action,
            run_id=run_id,
            manifest_digest=manifest.digest,
            workflow_id=manifest.workflow_id,
            state_status=state.status,
            state_sha256=state_digest,
            events_sha256=events_digest,
            step_id=state.current_step_id,
            step_index=state.current_step_index,
            employee_id=state.current_employee_id,
            previous_attempt_id=latest.attempt_id,
            previous_attempt_evidence_sha256=latest.digest,
            invocation_fingerprint=latest.invocation_fingerprint,
            provider=latest.provider,
            execution_target_fingerprint=latest.execution_target_fingerprint,
            raw_response_evidence_sha256=raw_digest,
            normalized_result_evidence_sha256=result_digest,
        )
    except WorkflowRecoveryError:
        raise
    except Exception:
        raise WorkflowRecoveryError("evidence") from None


def validate_workflow_recovery_authorization(
    store_root: Path,
    assessment: object,
    approval: object,
) -> RecoveryApprovalEvidence:
    """Re-read the Run and approval immediately before recovery can take effect."""
    if type(assessment) is not WorkflowRecoveryAssessment or not assessment.eligible:
        raise WorkflowRecoveryError("decision")
    try:
        current = assess_workflow_recovery(store_root, assessment.run_id)
        if current != assessment:
            raise WorkflowRecoveryError("stale")
        store = WorkflowRunManifestStore(store_root)
        persisted = load_recovery_approval_evidence(
            store, assessment.run_id, getattr(approval, "approval_id", "")
        )
        validate_recovery_approval_evidence(persisted, current)
        if persisted != approval:
            raise WorkflowRecoveryError("approval")
        return persisted
    except WorkflowRecoveryError:
        raise
    except Exception:
        raise WorkflowRecoveryError("approval") from None


def _latest_attempt(
    attempts: tuple[ExecutionAttemptEvidence, ...],
) -> ExecutionAttemptEvidence:
    by_id = {attempt.attempt_id: attempt for attempt in attempts}
    children = {
        attempt.previous_attempt_id
        for attempt in attempts
        if attempt.previous_attempt_id is not None
    }
    leaves = tuple(
        attempt for attempt in attempts if attempt.attempt_id not in children
    )
    if len(leaves) != 1 or len(by_id) != len(attempts):
        raise WorkflowRecoveryError("lineage")
    return leaves[0]


def _matches_state_step(
    attempt: ExecutionAttemptEvidence,
    state: object,
    binding: WorkflowRunBinding,
) -> bool:
    return (
        attempt.run_id == binding.run_id
        and attempt.manifest_digest == binding.manifest_digest
        and attempt.workflow_id == getattr(state, "workflow_id", None)
        and attempt.step_id == getattr(state, "current_step_id", None)
        and attempt.step_index == getattr(state, "current_step_index", None)
        and attempt.employee_id == getattr(state, "current_employee_id", None)
    )


def _ineligible(
    run_id: str,
    manifest_digest: str,
    workflow_id: str,
    state_status: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    state_digest: str,
    events_digest: str,
    reason: str,
) -> WorkflowRecoveryAssessment:
    return WorkflowRecoveryAssessment(
        eligible=False,
        reason=reason,
        action=None,
        run_id=run_id,
        manifest_digest=manifest_digest,
        workflow_id=workflow_id,
        state_status=state_status,
        state_sha256=state_digest,
        events_sha256=events_digest,
        step_id=step_id,
        step_index=step_index,
        employee_id=employee_id,
        previous_attempt_id=None,
        previous_attempt_evidence_sha256=None,
        invocation_fingerprint=None,
        provider=None,
        execution_target_fingerprint=None,
        raw_response_evidence_sha256=None,
        normalized_result_evidence_sha256=None,
    )


def _assessment_dict(value: WorkflowRecoveryAssessment) -> dict[str, object]:
    return {
        "action": value.action,
        "eligible": value.eligible,
        "employee_id": value.employee_id,
        "events_sha256": value.events_sha256,
        "execution_target_fingerprint": value.execution_target_fingerprint,
        "invocation_fingerprint": value.invocation_fingerprint,
        "manifest_digest": value.manifest_digest,
        "normalized_result_evidence_sha256": value.normalized_result_evidence_sha256,
        "previous_attempt_evidence_sha256": value.previous_attempt_evidence_sha256,
        "previous_attempt_id": value.previous_attempt_id,
        "provider": value.provider,
        "raw_response_evidence_sha256": value.raw_response_evidence_sha256,
        "reason": value.reason,
        "run_id": value.run_id,
        "state_sha256": value.state_sha256,
        "state_status": value.state_status,
        "step_id": value.step_id,
        "step_index": value.step_index,
        "workflow_id": value.workflow_id,
    }


def _canonical_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "RecoveryAction",
    "WorkflowRecoveryAssessment",
    "WorkflowRecoveryError",
    "assess_workflow_recovery",
    "validate_workflow_recovery_authorization",
]
