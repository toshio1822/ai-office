"""Phase 78 cycle continuation boundary contract tests."""

# ruff: noqa: E501

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    persist_executed_result_transition_reentry,
    route_executed_result_transition_persistence_routing_phase_bridge_cycle_continuation,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {"id": "w", "name": "W", "description": "D", "steps": [
            {"id": "step", "name": "S", "employee": "e", "instructions": "do"}
        ]}
    )


def success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w", "step", 1, "e", ModelInvocationSuccess("openai", "response", "request", "done", ("out",), "out")
    )


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w", "step", 1, "e", ModelInvocationFailure("openai", "api_error", "safe", "request", 500, None, None)
    )


def targets(tmp_path: Path, status: str = "running") -> tuple[Path, Path]:
    state = WorkflowExecutionState("w", status, "step", 1, "e", ("step",) if status == "succeeded" else (), None if status != "failed" else "api_error")
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    if status == "running":
        events_path.write_text("")
    else:
        events_path.write_text(serialize_runtime_step_event_jsonl(RuntimeStepEvent(
            "step_succeeded" if status == "succeeded" else "step_failed",
            "w", "step", 1, "e", "running", status, "openai",
            None if status == "succeeded" else "api_error",
            "response" if status == "succeeded" else None,
            "request", "out" if status == "succeeded" else None,
            None if status == "succeeded" else "safe",
        )))
    return state_path, events_path


@pytest.mark.parametrize("result", [success(), failure()])
def test_runtime_routes_delegate_once_with_identity_and_return_exact_result(
    tmp_path: Path, result: object
) -> None:
    state, events = targets(tmp_path)
    supplied_workflow = workflow()
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def phase71(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert all(actual is wanted for actual, wanted in zip(
            args, (result, supplied_workflow, state, events), strict=True
        ))
        expected = persist_executed_result_transition_reentry(*args)
        return expected

    returned = route_executed_result_transition_persistence_routing_phase_bridge_cycle_continuation(
        result, supplied_workflow, state, events, phase71_function=phase71
    )
    assert returned is expected
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_terminal_routes_return_supplied_identity_without_dependency(
    tmp_path: Path, kind: str
) -> None:
    state, events = targets(tmp_path, "succeeded" if kind == "complete" else "failed")
    result: object = (
        WorkflowProgressionDecision("workflow_complete", "w", "step", 1, "e", None, None, None, "last_step_succeeded")
        if kind == "complete"
        else PersistedExecutionOutcome("persisted_failure", "w", "step", 1, "e", "api_error")
    )
    before = state.read_bytes(), events.read_bytes()
    returned = route_executed_result_transition_persistence_routing_phase_bridge_cycle_continuation(
        result, workflow(), state, events, phase71_function=lambda *_: pytest.fail("called")
    )
    assert returned is result
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("value", [object(), WorkflowProgressionDecision("next_step", "w", "step", 1, "e", "step", 1, "e", "x")])
def test_invalid_results_are_rejected_before_dependency(tmp_path: Path, value: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationCompatibilityError):
        route_executed_result_transition_persistence_routing_phase_bridge_cycle_continuation(
            value, workflow(), state, events, phase71_function=lambda *_: pytest.fail("called")
        )
