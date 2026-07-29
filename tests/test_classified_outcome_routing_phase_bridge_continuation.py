"""Focused Phase 73 strict-boundary tests using injected Phase 59 fakes."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_outcome_routing_phase_bridge_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def make_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "a", "instructions": "x"},
                {"id": "two", "name": "Two", "employee": "b", "instructions": "y"},
            ],
        }
    )


def setup(
    tmp_path: Path, index: int = 1, status: str = "succeeded", single: bool = False
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    workflow = make_workflow()
    if single:
        workflow = WorkflowDefinition.model_validate(
            {
                "id": "w",
                "name": "W",
                "description": "D",
                "steps": [
                    {"id": "one", "name": "One", "employee": "a", "instructions": "x"}
                ],
            }
        )
        index = 1
    step = workflow.steps[index - 1]
    state = WorkflowExecutionState(
        "w",
        status,
        step.id,
        index,
        step.employee,
        tuple(item.id for item in workflow.steps[:index] if status == "succeeded"),
        None if status == "succeeded" else "api_error",
    )
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "w",
        step.id,
        index,
        step.employee,
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "output" if status == "succeeded" else None,
        None if status == "succeeded" else "failure",
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_bytes(serialize_runtime_step_event_jsonl(event).encode())
    result = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        step.id,
        index,
        step.employee,
        None if status == "succeeded" else "api_error",
    )
    return result, workflow, state_path, events_path


def complete(
    index: int = 2, step_id: str = "two", employee: str = "b"
) -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        step_id,
        index,
        employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )


def test_success_delegates_once_with_identity_and_returns_exact_decision(
    tmp_path: Path,
):
    result, workflow, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    decision = WorkflowProgressionDecision(
        "prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available"
    )
    calls = []

    def phase59(*args):
        calls.append(args)
        return decision

    returned = route_classified_outcome_routing_phase_bridge_continuation(
        result, workflow, state, events, phase59_function=phase59
    )
    assert returned is decision
    assert calls == [(result, workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == before


def test_final_success_maps_to_exact_completion_decision(tmp_path: Path):
    result, workflow, state, events = setup(tmp_path, single=True)
    decision = complete(index=1, step_id="one", employee="a")
    calls = []
    returned = route_classified_outcome_routing_phase_bridge_continuation(
        result,
        workflow,
        state,
        events,
        phase59_function=lambda *args: calls.append(args) or decision,
    )
    assert returned is decision and len(calls) == 1


def test_failure_and_completion_are_unchanged_zero_call_stops(tmp_path: Path):
    failure, workflow, state, events = setup(tmp_path, status="failed")
    calls = []
    assert (
        route_classified_outcome_routing_phase_bridge_continuation(
            failure,
            workflow,
            state,
            events,
            phase59_function=lambda *args: calls.append(args),
        )
        is failure
    )
    completion_result, workflow, state, events = setup(
        tmp_path / "complete", single=True
    )
    completion_result = complete(index=1, step_id="one", employee="a")
    assert (
        route_classified_outcome_routing_phase_bridge_continuation(
            completion_result,
            workflow,
            state,
            events,
            phase59_function=lambda *args: calls.append(args),
        )
        is completion_result
    )
    assert calls == []


@pytest.mark.parametrize(
    "field",
    [
        "outcome",
        "workflow_id",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "failure_category",
    ],
)
def test_malformed_success_is_rejected_before_dependency(tmp_path: Path, field: str):
    result, workflow, state, events = setup(tmp_path)
    bad = {
        "outcome": "persisted_failure",
        "workflow_id": "bad",
        "current_step_id": "bad",
        "current_step_index": True,
        "current_employee_id": "bad",
        "failure_category": "api_error",
    }[field]
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            replace(result, **{field: bad}),
            workflow,
            state,
            events,
            phase59_function=lambda *_: pytest.fail("called"),
        )
    assert caught.value.detail.classification in {
        "success_contract",
        "failure_contract",
        "outcome_contract",
    }


@pytest.mark.parametrize(
    "value",
    [
        object(),
        WorkflowProgressionDecision(
            "prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "bad"
        ),
    ],
)
def test_unsupported_result_or_malformed_dependency_is_safe(
    tmp_path: Path, value: object
):
    result, workflow, state, events = setup(tmp_path)
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_classified_outcome_routing_phase_bridge_continuation(
            value, workflow, state, events
        )

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=lambda *_: value
        )
    assert caught.value.detail.classification == "progression_contract"


def test_safe_dependency_error_is_preserved_and_unexpected_is_sanitized(tmp_path: Path):
    from ai_office.engine import ClassifiedPersistedOutcomeRoutingPhaseBridgeError

    result, workflow, state, events = setup(tmp_path)
    safe = ClassifiedPersistedOutcomeRoutingPhaseBridgeError("safe")
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeError) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result,
            workflow,
            state,
            events,
            phase59_function=lambda *_: (_ for _ in ()).throw(safe),
        )
    assert caught.value is safe
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result,
            workflow,
            state,
            events,
            phase59_function=lambda *_: (_ for _ in ()).throw(RuntimeError("secret")),
        )
    assert caught.value.detail.classification == "dependency_error"


@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_dependency_mutation_is_compensated_without_retry(tmp_path: Path, target: str):
    result, workflow, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def mutate(*_):
        nonlocal calls
        calls += 1
        if target in ("state", "both"):
            state.write_bytes(b"changed")
        if target in ("events", "both"):
            events.write_bytes(b"changed")
        return object()

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=mutate
        )
    assert caught.value.detail.classification == "outcome_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before
