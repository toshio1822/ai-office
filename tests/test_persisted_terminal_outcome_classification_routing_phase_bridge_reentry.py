"""Focused Phase 65 boundary tests using injected Phase 58 fakes only."""

# The test names and contract fixtures intentionally use the full public names.
# ruff: noqa: E501

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError,
    PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError,
    WorkflowProgressionDecision,
    route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class ResultSubclass(WorkflowExecutionPersistenceResult):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


def workflow(two: bool = False) -> WorkflowDefinition:
    steps = [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}]
    if two:
        steps.append({"id": "two", "name": "Two", "employee": "b", "instructions": "y"})
    return WorkflowDefinition.model_validate(
        {"id": "w", "name": "W", "description": "D", "steps": steps}
    )


def setup(
    tmp_path: Path, *, status: str = "succeeded", two: bool = False, index: int = 1
) -> tuple[Path, Path, WorkflowExecutionPersistenceResult, WorkflowDefinition, bytes, bytes]:
    definition = workflow(two)
    step = definition.steps[index - 1]
    state_model = WorkflowExecutionState(
        "w", status, step.id, index, step.employee,
        tuple(item.id for item in definition.steps[:index]) if status == "succeeded"
        else tuple(item.id for item in definition.steps[: index - 1]),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "w", step.id, index, step.employee, "running", status, "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None, "request",
        "out" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]
    state_bytes = serialize_workflow_execution_state_json(state_model).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(state_bytes)
    events.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(state, events, len(state_bytes), len(event_bytes))
    return state, events, result, definition, state_bytes, event_bytes


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded"
    )


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "w", "one", 1, "a", "api_error")


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_valid_persistence_routes_delegate_once_and_return_exact_outcome(
    tmp_path: Path, status: str
) -> None:
    state, events, result, definition, before_state, before_events = setup(tmp_path, status=status)
    expected = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w", "one", 1, "a", None if status == "succeeded" else "api_error",
    )
    calls: list[tuple[object, ...]] = []

    def phase58(*args: object) -> PersistedExecutionOutcome:
        calls.append(args)
        assert args == (result, definition, state, events)
        return expected

    returned = route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry(
        result, definition, state, events, phase58_function=phase58
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_stop_routes_return_supplied_object_without_phase58(tmp_path: Path, status: str) -> None:
    state, events, _, definition, before_state, before_events = setup(tmp_path, status=status)
    supplied = completion() if status == "succeeded" else failure()
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry(
        supplied, definition, state, events, phase58_function=unexpected
    ) is supplied
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "value",
    [object(), ResultSubclass(Path("s"), Path("e"), 1, 1),
     OutcomeSubclass("persisted_failure", "w", "one", 1, "a", "api_error"),
     DecisionSubclass("workflow_complete", "w", "one", 1, "a", None, None, None,
                      "last_step_succeeded")],
)
def test_exact_result_types_are_required_without_dependency_call(tmp_path: Path, value: object) -> None:
    state, events, _, definition, before_state, before_events = setup(tmp_path)
    with pytest.raises(PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError):
        route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry(
            value, definition, state, events, phase58_function=lambda *_: object()
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("value", [WorkflowSubclass.model_validate(workflow().model_dump()), SimpleNamespace(id="w")])
def test_exact_workflow_type_is_required(tmp_path: Path, value: object) -> None:
    state, events, result, _, before_state, before_events = setup(tmp_path)
    with pytest.raises(PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError):
        route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry(
            result, value, state, events
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    ("which", "operation"), [("state", "is_file"), ("events", "is_file"),
                               ("state", "read_bytes"), ("events", "read_bytes")]
)
def test_state_and_event_oserrors_are_classified_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, operation: str
) -> None:
    state, events, result, definition, before_state, before_events = setup(tmp_path)
    target = state if which == "state" else events
    original = getattr(Path, operation)

    def fail(path: Path) -> object:
        if path == target:
            raise OSError("secret path")
        return original(path)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(
        (PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError,
         PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError)
    ) as caught:
        route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry(
            result, definition, state, events
        )
    assert caught.value.detail.classification == ("state_target" if which == "state" else "event_target")
    monkeypatch.undo()
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    ("field", "value"), [("state_path", Path("other")), ("events_path", Path("other")),
                            ("state_bytes_written", 0), ("event_bytes_appended", -1),
                            ("state_bytes_written", True), ("event_bytes_appended", "1")]
)
def test_persistence_contract_is_prevalidated(tmp_path: Path, field: str, value: object) -> None:
    state, events, result, definition, before_state, before_events = setup(tmp_path)
    with pytest.raises(PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError):
        route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry(
            replace(result, **{field: value}), definition, state, events,
            phase58_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("value", [
    object(), OutcomeSubclass("persisted_success", "w", "one", 1, "a", None)
])
def test_malformed_phase58_return_is_rejected_without_retry(tmp_path: Path, value: object) -> None:
    state, events, result, definition, before_state, before_events = setup(tmp_path)
    calls = 0

    def phase58(*_: object) -> object:
        nonlocal calls
        calls += 1
        return value

    with pytest.raises(
        (PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError,
         PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError)
    ) as caught:
        route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry(
            result, definition, state, events, phase58_function=phase58
        )
    assert caught.value.detail.classification == "outcome_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_failed_outcome_category_must_match_persisted_state_without_retry(
    tmp_path: Path,
) -> None:
    state, events, result, definition, before_state, before_events = setup(
        tmp_path, status="failed"
    )
    returned = replace(failure(), failure_category="transport_error")
    calls = 0

    def phase58(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        return returned

    with pytest.raises(
        PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry(
            result, definition, state, events, phase58_function=phase58
        )
    assert caught.value.detail.classification == "outcome_contract"
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_dependency_errors_and_mutations_are_compensated_without_retry(
    tmp_path: Path, kind: str, mutation: str
) -> None:
    state, events, result, definition, before_state, before_events = setup(tmp_path)
    calls = 0
    safe = PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError("terminal_contract")

    def phase58(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"changed state")
        if mutation in ("events", "both"):
            events.write_bytes(b"changed events")
        if kind == "safe":
            raise safe
        raise RuntimeError("secret provider response")

    with pytest.raises(
        (PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError,
         PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError)
    ) as caught:
        route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry(
            result, definition, state, events, phase58_function=phase58
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
        assert "secret" not in str(caught.value)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_dependency_mutation_with_malformed_return_is_restored(tmp_path: Path) -> None:
    state, events, result, definition, before_state, before_events = setup(tmp_path)

    def phase58(*_: object) -> object:
        state.write_bytes(b"changed")
        events.write_bytes(b"changed")
        return object()

    with pytest.raises(PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError):
        route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry(
            result, definition, state, events, phase58_function=phase58
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
