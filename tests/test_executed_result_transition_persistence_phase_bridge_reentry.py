"""Focused Phase 57 contract tests using injected Phase 50 fakes only."""

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_executed_result_transition_persistence_phase_bridge_reentry,
)
from ai_office.engine.executed_result_transition_persistence_bridge_reentry import (
    ExecutedResultTransitionPersistenceBridgeCompatibilityError,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "step", "name": "S", "employee": "e", "instructions": "do"}
            ],
        }
    )


def success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "step",
        1,
        "e",
        ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out"),
    )


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w",
        "step",
        1,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", "q", 500, None, None),
    )


def setup(tmp_path: Path, status: str = "running") -> tuple[Path, Path, bytes, bytes]:
    state = WorkflowExecutionState(
        "w",
        status,
        "step",
        1,
        "e",
        ("step",) if status == "succeeded" else (),
        None if status != "failed" else "api_error",
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    if status == "succeeded":
        event = RuntimeStepEvent(
            "step_succeeded",
            "w",
            "step",
            1,
            "e",
            "running",
            "succeeded",
            "openai",
            None,
            "r",
            "q",
            "out",
            None,
        )
        events_path.write_text(serialize_runtime_step_event_jsonl(event))
    elif status == "failed":
        event = RuntimeStepEvent(
            "step_failed",
            "w",
            "step",
            1,
            "e",
            "running",
            "failed",
            "openai",
            "api_error",
            None,
            "q",
            None,
            "safe",
        )
        events_path.write_text(serialize_runtime_step_event_jsonl(event))
    else:
        events_path.write_text("")
    return state_path, events_path, state_path.read_bytes(), events_path.read_bytes()


def persist_fake(
    result: object, _workflow: object, state: Path, events: Path
) -> WorkflowExecutionPersistenceResult:
    invocation = result.invocation_result  # type: ignore[union-attr]
    ok = type(result) is StepRuntimeExecutionSuccess
    next_state = WorkflowExecutionState(
        "w",
        "succeeded" if ok else "failed",
        "step",
        1,
        "e",
        ("step",) if ok else (),
        None if ok else invocation.category,
    )
    event = RuntimeStepEvent(
        "step_succeeded" if ok else "step_failed",
        "w",
        "step",
        1,
        "e",
        "running",
        next_state.status,
        "openai",
        None if ok else invocation.category,
        invocation.response_id if ok else None,
        invocation.request_id,
        invocation.text if ok else None,
        None if ok else invocation.message,
    )
    state_bytes = serialize_workflow_execution_state_json(next_state).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    events.write_bytes(events.read_bytes() + event_bytes)
    state.write_bytes(state_bytes)
    return WorkflowExecutionPersistenceResult(
        state, events, len(state_bytes), len(event_bytes)
    )


@pytest.mark.parametrize("result", [success(), failure()])
def test_runtime_delegates_once_with_identity_and_exact_objects(
    tmp_path: Path, result: object
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    supplied_workflow = workflow()
    calls: list[tuple[object, ...]] = []
    expected: WorkflowExecutionPersistenceResult | None = None

    def fake(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert args[0] is result and args[1] is supplied_workflow
        assert args[2] is state and args[3] is events
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    returned = route_executed_result_transition_persistence_phase_bridge_reentry(
        result, supplied_workflow, state, events, phase50_function=fake
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) != (before_state, before_events)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_terminal_routes_return_identity_without_writes_or_dependency(
    tmp_path: Path, kind: str
) -> None:
    state, events, before_state, before_events = setup(
        tmp_path, "succeeded" if kind == "complete" else "failed"
    )
    result = (
        WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            "step",
            1,
            "e",
            None,
            None,
            None,
            "last_step_succeeded",
        )
        if kind == "complete"
        else PersistedExecutionOutcome(
            "persisted_failure", "w", "step", 1, "e", "api_error"
        )
    )
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_executed_result_transition_persistence_phase_bridge_reentry(
            result, workflow(), state, events, phase50_function=unexpected
        )
        is result
    )
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


def test_persisted_success_is_rejected_without_dependency(tmp_path: Path) -> None:
    state, events, before_state, before_events = setup(tmp_path, "succeeded")
    value = PersistedExecutionOutcome("persisted_success", "w", "step", 1, "e", None)
    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            value, workflow(), state, events
        )
    assert caught.value.detail.classification == "failure_contract"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_malformed_dependency_is_compensated_without_retry(tmp_path: Path) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def malformed(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"bad")
        events.write_bytes(b"bad\n")
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            success(), workflow(), state, events, phase50_function=malformed
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


def test_safe_dependency_error_identity_is_preserved_after_rollback(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    expected = ExecutedResultTransitionPersistenceBridgeCompatibilityError(
        "runtime_contract"
    )

    def raises(*_: object) -> object:
        state.write_bytes(b"changed")
        raise expected

    with pytest.raises(
        ExecutedResultTransitionPersistenceBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            success(), workflow(), state, events, phase50_function=raises
        )
    assert caught.value is expected
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
