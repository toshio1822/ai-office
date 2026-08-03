"""Focused tests for the Phase 108 classified persisted-outcome progression boundary."""

# ruff: noqa: E501,E701,E702
import inspect
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_outcome_cycle_closure_continuation_boundary,
    route_classified_persisted_outcome_progression_cycle_continuation_boundary,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow(single: bool = False) -> WorkflowDefinition:
    steps = [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}]
    if not single:
        steps.append({"id": "two", "name": "Two", "employee": "b", "instructions": "y"})
    return WorkflowDefinition.model_validate({"id": "w", "name": "W", "description": "D", "steps": steps})

def setup(tmp_path: Path, *, single: bool = False, failed: bool = False, complete: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = workflow(single)
    index = len(definition.steps) if single or complete else 1
    step = definition.steps[index - 1]
    
    if complete:
        status = "succeeded"
        state_model = WorkflowExecutionState("w", status, step.id, index, step.employee, tuple(item.id for item in definition.steps), None)
        event = RuntimeStepEvent("step_succeeded", "w", step.id, index, step.employee, "running", status, "openai", None, "response", "request", "output", None)
        result = WorkflowProgressionDecision("workflow_complete", "w", step.id, index, step.employee, None, None, None, "last_step_succeeded")
    else:
        status = "failed" if failed else "succeeded"
        state_model = WorkflowExecutionState("w", status, step.id, index, step.employee, () if failed else tuple(item.id for item in definition.steps[:index]), "api_error" if failed else None)
        event = RuntimeStepEvent("step_failed" if failed else "step_succeeded", "w", step.id, index, step.employee, "running", status, "openai", "api_error" if failed else None, None if failed else "response", "request", None if failed else "output", "failure" if failed else None)
        result = PersistedExecutionOutcome("persisted_failure" if failed else "persisted_success", "w", step.id, index, step.employee, "api_error" if failed else None)
        
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(serialize_workflow_execution_state_json(state_model).encode())
    history_events = [event]
    if complete and len(definition.steps) > 1:
        first = definition.steps[0]
        history_events.insert(
            0,
            RuntimeStepEvent(
                "step_succeeded", "w", first.id, 1, first.employee, "running",
                "succeeded", "openai", None, "response-1", "request-1",
                "output-1", None,
            ),
        )
    events.write_bytes("".join(serialize_runtime_step_event_jsonl(item) for item in history_events).encode())
    return result, definition, state, events

def test_public_signature_default_identity_and_canonical_order(tmp_path: Path) -> None:
    signature = inspect.signature(route_classified_persisted_outcome_progression_cycle_continuation_boundary)
    parameters = list(signature.parameters.values())
    assert [p.name for p in parameters[:4]] == ["result", "workflow", "state_path", "events_path"]
    assert [p.kind for p in parameters[:4]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 4
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[4].name == "phase101_function"
    assert parameters[4].default is route_classified_outcome_cycle_closure_continuation_boundary

def test_persisted_success_delegates_to_phase101(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path, failed=False)
    
    calls = []
    expected = WorkflowProgressionDecision("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available")
    
    def fake_phase101(*args):
        calls.append(args)
        return expected
        
    returned = route_classified_persisted_outcome_progression_cycle_continuation_boundary(
        result, definition, state, events, phase101_function=fake_phase101
    )
    
    assert returned is expected
    assert len(calls) == 1
    assert calls[0] == (result, definition, state, events)

def test_persisted_failure_returns_unchanged(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path, failed=True)
    
    calls = 0
    def fake_phase101(*args):
        nonlocal calls
        calls += 1
        return result
        
    returned = route_classified_persisted_outcome_progression_cycle_continuation_boundary(
        result, definition, state, events, phase101_function=fake_phase101
    )
    
    assert returned is result
    assert calls == 0

def test_workflow_complete_returns_unchanged(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path, complete=True)
    
    calls = 0
    def fake_phase101(*args):
        nonlocal calls
        calls += 1
        return result
        
    returned = route_classified_persisted_outcome_progression_cycle_continuation_boundary(
        result, definition, state, events, phase101_function=fake_phase101
    )
    
    assert returned is result
    assert calls == 0

def test_invalid_input_rejected(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError):
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(
            "invalid", definition, state, events
        )
