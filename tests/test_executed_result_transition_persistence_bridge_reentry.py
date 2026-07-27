"""Focused Phase 50 bridge tests using injected Phase 43 fakes only."""

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ExecutedResultTransitionPersistenceBridgeCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    persist_executed_result_transition_reentry,
    route_executed_result_transition_persistence_bridge_reentry,
)
from ai_office.engine.executed_result_transition_routing_reentry import (
    ExecutedResultTransitionRoutingCompatibilityError,
    ExecutedResultTransitionRoutingError,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": "step",
                    "name": "S",
                    "employee": "employee",
                    "instructions": "do",
                }
            ],
        }
    )


def success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "workflow",
        "step",
        1,
        "employee",
        ModelInvocationSuccess(
            "openai", "response", "request", "done", ("out",), "out"
        ),
    )


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "workflow",
        "step",
        1,
        "employee",
        ModelInvocationFailure(
            "openai", "api_error", "safe", "request", None, None, None
        ),
    )


def setup(tmp_path: Path) -> tuple[Path, Path, bytes, bytes]:
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    before = serialize_workflow_execution_state_json(
        WorkflowExecutionState("workflow", "running", "step", 1, "employee", (), None)
    ).encode()
    state.write_bytes(before)
    events.write_bytes(b"")
    return state, events, before, b""


@pytest.mark.parametrize("result", [success(), failure()])
def test_runtime_routes_once_with_exact_arguments_and_same_result(
    tmp_path: Path, result: object
) -> None:
    state, events, _, _ = setup(tmp_path)
    supplied_workflow = workflow()
    calls = 0
    expected: WorkflowExecutionPersistenceResult | None = None

    def phase43(*args: object) -> WorkflowExecutionPersistenceResult:
        nonlocal calls, expected
        calls += 1
        assert (
            args[0] is result
            and args[1] is supplied_workflow
            and args[2] is state
            and args[3] is events
        )
        expected = persist_executed_result_transition_reentry(*args)  # type: ignore[arg-type]
        return expected

    returned = route_executed_result_transition_persistence_bridge_reentry(
        result,
        supplied_workflow,
        state,
        events,
        transition_routing_function=phase43,
    )
    assert returned is expected and calls == 1


@pytest.mark.parametrize("runtime", [success(), failure()])
def test_malformed_phase43_result_is_compensated(
    tmp_path: Path, runtime: object
) -> None:
    state, events, before_state, before_events = setup(tmp_path)

    def malformed(*_: object) -> object:
        state.write_bytes(b"bad")
        events.write_bytes(b"bad\n")
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistenceBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            runtime, workflow(), state, events, transition_routing_function=malformed
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_phase43_safe_error_identity_is_preserved_after_rollback(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    expected = ExecutedResultTransitionRoutingCompatibilityError("result_type")

    def raises(*_: object) -> object:
        state.write_bytes(b"changed")
        raise expected

    with pytest.raises(ExecutedResultTransitionRoutingError) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            success(), workflow(), state, events, transition_routing_function=raises
        )
    assert caught.value is expected
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_persisted_success_is_rejected_without_phase43_or_writes(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    value = PersistedExecutionOutcome(
        "persisted_success", "workflow", "step", 1, "employee", None
    )
    with pytest.raises(
        ExecutedResultTransitionPersistenceBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            value, workflow(), state, events, transition_routing_function=unexpected
        )
    assert caught.value.detail.classification == "failure_contract" and calls == 0
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_workflow_complete_is_read_only_and_returns_same_object(tmp_path: Path) -> None:
    state, events, _, _ = setup(tmp_path)
    persist_executed_result_transition_reentry(success(), workflow(), state, events)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    value = WorkflowProgressionDecision(
        "workflow_complete",
        "workflow",
        "step",
        1,
        "employee",
        None,
        None,
        None,
        "last_step_succeeded",
    )

    def unexpected(*_: object) -> object:
        raise AssertionError

    returned = route_executed_result_transition_persistence_bridge_reentry(
        value, workflow(), state, events, transition_routing_function=unexpected
    )
    assert returned is value
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_persisted_failure_is_read_only_and_returns_same_object(tmp_path: Path) -> None:
    state, events, _, _ = setup(tmp_path)
    persist_executed_result_transition_reentry(failure(), workflow(), state, events)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    value = PersistedExecutionOutcome(
        "persisted_failure", "workflow", "step", 1, "employee", "api_error"
    )

    def unexpected(*_: object) -> object:
        raise AssertionError

    returned = route_executed_result_transition_persistence_bridge_reentry(
        value, workflow(), state, events, transition_routing_function=unexpected
    )
    assert returned is value
    assert state.read_bytes() == before_state and events.read_bytes() == before_events
