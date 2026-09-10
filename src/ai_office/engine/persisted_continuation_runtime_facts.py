"""Deterministic v1 runtime facts for one persisted continuation."""

from __future__ import annotations

from hashlib import sha256

from ai_office.invocation import (
    RuntimeFact,
    RuntimeFactProvenance,
    RuntimeFactsError,
    RuntimeFactsSnapshot,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage.workflow_execution_history import LoadedWorkflowExecutionHistory
from ai_office.storage.workflow_execution_persistence import (
    serialize_runtime_step_event_jsonl,
)

_ERROR_MESSAGE = "persisted continuation runtime facts are invalid"


class PersistedContinuationRuntimeFactsError(ValueError):
    """Raised when persisted evidence cannot produce the v1 fact snapshot."""

    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)


def build_persisted_continuation_runtime_facts(
    workflow_id: object,
    next_step_index: object,
    history: object,
    *,
    state_source_sha256: object,
) -> RuntimeFactsSnapshot:
    """Build the selected typed facts for one persisted next-step continuation.

    This is a pure boundary helper.  The caller owns reading the exact state
    bytes and supplies only their digest; this function never reads paths,
    environment variables, network state, or a clock.
    """
    try:
        state, predecessor = _validated_evidence(
            workflow_id, next_step_index, history
        )
        state_provenance = RuntimeFactProvenance(
            origin="persisted_state",
            workflow_id=state.workflow_id,
            source_ref="state",
            source_sha256=state_source_sha256,  # type: ignore[arg-type]
            observed_at=None,
        )
        event_bytes = serialize_runtime_step_event_jsonl(predecessor).encode("utf-8")
        event_provenance = RuntimeFactProvenance(
            origin="persisted_event",
            workflow_id=state.workflow_id,
            source_ref=f"event:{predecessor.step_index}",
            source_sha256=sha256(event_bytes).hexdigest(),
            observed_at=None,
        )
        return RuntimeFactsSnapshot(
            facts=(
                RuntimeFact(
                    key="workflow.status",
                    value_kind="enum",
                    value=state.status,
                    provenance=state_provenance,
                ),
                RuntimeFact(
                    key="workflow.completed_step_count",
                    value_kind="integer",
                    value=len(state.completed_step_ids),
                    provenance=state_provenance,
                ),
                RuntimeFact(
                    key="predecessor.step_id",
                    value_kind="identifier",
                    value=predecessor.step_id,
                    provenance=event_provenance,
                ),
                RuntimeFact(
                    key="predecessor.step_index",
                    value_kind="integer",
                    value=predecessor.step_index,
                    provenance=event_provenance,
                ),
                RuntimeFact(
                    key="predecessor.employee_id",
                    value_kind="identifier",
                    value=predecessor.employee_id,
                    provenance=event_provenance,
                ),
                RuntimeFact(
                    key="predecessor.provider",
                    value_kind="enum",
                    value=predecessor.provider,
                    provenance=event_provenance,
                ),
            )
        )
    except PersistedContinuationRuntimeFactsError:
        raise
    except (RuntimeFactsError, TypeError, ValueError, AttributeError):
        _raise()


def _validated_evidence(
    workflow_id: object,
    next_step_index: object,
    history: object,
) -> tuple[WorkflowExecutionState, RuntimeStepEvent]:
    if type(workflow_id) is not str or not workflow_id:
        _raise()
    if (
        type(next_step_index) is not int
        or next_step_index < 2
    ):
        _raise()
    if type(history) is not LoadedWorkflowExecutionHistory:
        _raise()
    state = history.state
    events = history.events
    if type(state) is not WorkflowExecutionState or type(events) is not tuple:
        _raise()
    if (
        type(state.workflow_id) is not str
        or state.workflow_id != workflow_id
        or type(state.status) is not str
        or state.status != "succeeded"
        or state.last_failure_category is not None
        or type(state.current_step_index) is not int
        or isinstance(state.current_step_index, bool)
        or state.current_step_index != next_step_index - 1
        or type(state.completed_step_ids) is not tuple
        or not state.completed_step_ids
        or any(
            type(value) is not str or not value
            for value in state.completed_step_ids
        )
    ):
        _raise()

    predecessor_index = next_step_index - 1
    candidates = tuple(
        event
        for event in events
        if type(event) is RuntimeStepEvent
        and event.step_index == predecessor_index
        and event.event_type == "step_succeeded"
    )
    if len(candidates) != 1 or not events or candidates[0] is not events[-1]:
        _raise()
    predecessor = candidates[0]
    if not (
        type(predecessor.event_type) is str
        and type(predecessor.workflow_id) is str
        and predecessor.workflow_id == state.workflow_id
        and predecessor.step_id == state.current_step_id
        and type(predecessor.step_index) is int
        and not isinstance(predecessor.step_index, bool)
        and predecessor.step_index == state.current_step_index
        and type(predecessor.employee_id) is str
        and predecessor.employee_id == state.current_employee_id
        and predecessor.previous_status == "running"
        and predecessor.next_status == "succeeded"
        and predecessor.failure_category is None
        and predecessor.message is None
        and type(predecessor.output_text) is str
        and state.completed_step_ids[-1] == predecessor.step_id
        and type(predecessor.provider) is str
        and bool(predecessor.provider)
    ):
        _raise()
    return state, predecessor


def _raise() -> None:
    raise PersistedContinuationRuntimeFactsError() from None


__all__ = [
    "PersistedContinuationRuntimeFactsError",
    "build_persisted_continuation_runtime_facts",
]
