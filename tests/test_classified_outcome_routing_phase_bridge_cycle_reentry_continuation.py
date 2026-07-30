"""Focused Phase 87 strict-boundary tests."""

# ruff: noqa: E501

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedOutcomeRoutingPhaseBridgeContinuationError,
    ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


def workflow(single: bool = False) -> WorkflowDefinition:
    steps = [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}]
    if not single:
        steps.append({"id": "two", "name": "Two", "employee": "b", "instructions": "y"})
    return WorkflowDefinition.model_validate({"id": "w", "name": "W", "description": "D", "steps": steps})


def setup(tmp_path: Path, *, single: bool = False, failed: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = workflow(single)
    index = 1 if not single else len(definition.steps)
    step = definition.steps[index - 1]
    status = "failed" if failed else "succeeded"
    state_model = WorkflowExecutionState(
        "w", status, step.id, index, step.employee,
        () if failed else tuple(item.id for item in definition.steps[:index]),
        "api_error" if failed else None,
    )
    event = RuntimeStepEvent(
        "step_failed" if failed else "step_succeeded", "w", step.id, index, step.employee,
        "running", status, "openai", "api_error" if failed else None,
        None if failed else "response", "request", None if failed else "output",
        "failure" if failed else None,
    )
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(serialize_workflow_execution_state_json(state_model).encode())
    events.write_bytes(serialize_runtime_step_event_jsonl(event).encode())
    result = PersistedExecutionOutcome(
        "persisted_failure" if failed else "persisted_success", "w", step.id, index, step.employee,
        "api_error" if failed else None,
    )
    return result, definition, state, events


def prepare() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available")


def complete(single: bool = False) -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete", "w", "one" if single else "two", 1 if single else 2,
        "a" if single else "b", None, None, None, "last_step_succeeded"
    )


def test_public_signature_and_success_routes_once_with_identity(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    expected = prepare()
    calls: list[tuple[object, ...]] = []

    def phase80(*args: object) -> object:
        calls.append(args)
        return expected

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
        result, definition, state, events, phase80_function=phase80
    )
    assert returned is expected and len(calls) == 1
    assert calls[0] == (result, definition, state, events)
    assert all(calls[0][i] is value for i, value in enumerate((result, definition, state, events)))
    assert (state.read_bytes(), events.read_bytes()) == before


def test_final_success_and_completion_mapping(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path, single=True)
    expected = WorkflowProgressionDecision("workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded")
    assert route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
        result, definition, state, events, phase80_function=lambda *_: expected
    ) is expected


def test_failure_and_completion_are_identity_preserving_zero_call_stops(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path, failed=True)
    calls = 0

    def phase80(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=phase80) is result
    result, definition, state, events = setup(tmp_path / "complete", single=True)
    value = complete(single=True)
    assert route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(value, definition, state, events, phase80_function=phase80) is value
    assert calls == 0


@pytest.mark.parametrize("field,value", [
    ("outcome", "persisted_failure"), ("workflow_id", "other"), ("current_step_id", "other"),
    ("current_step_index", True), ("current_employee_id", "other"), ("failure_category", "api_error"),
])
def test_success_fields_and_substitute_models_rejected_before_dependency(tmp_path: Path, field: str, value: object) -> None:
    result, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
            replace(result, **{field: value}), definition, state, events, phase80_function=lambda *_: pytest.fail("called")
        )
    subclass = OutcomeSubclass("persisted_success", "w", "one", 1, "a", None)
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(subclass, definition, state, events)


@pytest.mark.parametrize("value", [object(), DecisionSubclass("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available")])
def test_malformed_or_subclass_dependency_return_rejected_once_and_compensated(tmp_path: Path, value: object) -> None:
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def phase80(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"mutated")
        return value

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=phase80)
    assert calls == 1 and caught.value.detail.classification == "outcome_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_safe_error_identity_after_compensation_and_unexpected_sanitization(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    safe = ClassifiedOutcomeRoutingPhaseBridgeContinuationError("safe")
    before = (state.read_bytes(), events.read_bytes())

    def safe_phase(*_: object) -> object:
        state.write_bytes(b"changed")
        events.write_bytes(b"changed")
        raise safe

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeContinuationError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=safe_phase)
    assert caught.value is safe and (state.read_bytes(), events.read_bytes()) == before
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret")

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=unexpected)
    assert calls == 1 and error.value.detail.classification == "dependency_error"


@pytest.mark.parametrize("which", ["state", "events", "both"])
def test_dependency_mutation_restores_both_targets(tmp_path: Path, which: str) -> None:
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())

    def phase80(*_: object) -> object:
        if which in ("state", "both"):
            state.write_bytes(b"changed-state")
        if which in ("events", "both"):
            events.write_bytes(b"changed-events")
        return prepare()

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=phase80)
    assert error.value.detail.classification == "outcome_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_invalid_targets_are_rejected_before_dependency(tmp_path: Path, kind: str) -> None:
    result, definition, state, events = setup(tmp_path)
    target = state if kind == "missing" else events
    target.unlink()
    if kind == "directory":
        target.mkdir()
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("failure_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_and_classifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_target: str) -> None:
    result, definition, state, events = setup(tmp_path)
    original_write = Path.write_bytes
    attempts: list[Path] = []

    def write(path: Path, data: bytes) -> int:
        attempts.append(path)
        if failure_target in ("state", "both") and path == state:
            raise OSError
        if failure_target == "events" and path == events:
            raise OSError
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)

    def phase80(*_: object) -> object:
        original_write(state, b"changed")
        original_write(events, b"changed")
        return prepare()

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=phase80)
    assert error.value.detail.classification == "dependency_rollback"
    assert state in attempts and events in attempts


def test_no_second_restore_after_rollback_failure_and_target_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, definition, state, events = setup(tmp_path)
    original_read = Path.read_bytes

    def fail(path: Path) -> bytes:
        if path == state:
            raise OSError
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", fail)
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events)
    assert error.value.detail.classification == "state_target"
