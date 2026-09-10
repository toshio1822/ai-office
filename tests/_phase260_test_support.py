"""Small test-only builders for Phase260 persisted continuation approvals."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from ai_office.engine import build_persisted_continuation_runtime_facts
from ai_office.invocation import (
    ModelInvocationRequest,
    RuntimeFactsSnapshot,
    approve_model_invocation_execution,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history_with_source_digests,
    serialize_workflow_execution_state_json,
)


def synthetic_continuation_facts(
    *,
    workflow_id: str,
    predecessor_step_id: str,
    predecessor_step_index: int,
    predecessor_employee_id: str,
    completed_step_ids: tuple[str, ...],
    output_text: str,
    response_id: str | None,
    request_id: str | None,
    next_step_index: int,
    provider: str = "openai",
) -> RuntimeFactsSnapshot:
    """Build facts for the exact synthetic persisted snapshot used by a test."""
    state = WorkflowExecutionState(
        workflow_id,
        "succeeded",
        predecessor_step_id,
        predecessor_step_index,
        predecessor_employee_id,
        completed_step_ids,
        None,
    )
    event = RuntimeStepEvent(
        "step_succeeded",
        workflow_id,
        predecessor_step_id,
        predecessor_step_index,
        predecessor_employee_id,
        "running",
        "succeeded",
        provider,
        None,
        response_id,
        request_id,
        output_text,
        None,
    )
    state_sha256 = sha256(
        serialize_workflow_execution_state_json(state).encode("utf-8")
    ).hexdigest()
    return build_persisted_continuation_runtime_facts(
        workflow_id,
        next_step_index,
        LoadedWorkflowExecutionHistory(state, (event,)),
        state_source_sha256=state_sha256,
    )


def approve_persisted_continuation(
    request: ModelInvocationRequest,
    resolved_tools: tuple[object, ...],
    *,
    runtime_facts: RuntimeFactsSnapshot,
    provider: str,
    approved_by: str,
    approval_id: str,
):
    """Bind an approval to the Phase260 request without changing test task data."""
    return approve_model_invocation_execution(
        replace(request, runtime_facts=runtime_facts),
        resolved_tools,  # type: ignore[arg-type]
        provider=provider,
        approved_by=approved_by,
        approval_id=approval_id,
    )


def persisted_facts(
    state_path: Path,
    events_path: Path,
    workflow_id: str,
    next_step_index: int,
) -> RuntimeFactsSnapshot:
    """Derive the exact facts from an existing persisted fixture."""
    history, state_sha256, _events_sha256 = (
        load_workflow_execution_history_with_source_digests(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    )
    return build_persisted_continuation_runtime_facts(
        workflow_id,
        next_step_index,
        history,
        state_source_sha256=state_sha256,
    )
