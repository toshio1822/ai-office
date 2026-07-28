"""Phase 55 contract tests using injected Phase 48 fakes only."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStartPersistenceBridgeError,
    PreparedStartPersistencePhaseBridgeCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_phase_bridge_reentry,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "test",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "one",
                    "instructions": "a",
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "two",
                    "instructions": "b",
                },
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "two",
            "name": "Two",
            "role": "role",
            "instructions": "employee",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def start() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "employee", "b", ("tool",)),
        WorkflowExecutionState(
            "workflow", "running", "second", 2, "two", ("first",), None
        ),
    )


def event(status: str, index: int) -> RuntimeStepEvent:
    step, person = ("first", "one") if index == 1 else ("second", "two")
    return RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "workflow",
        step,
        index,
        person,
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "output" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]


def targets(
    tmp_path: Path, status: str = "succeeded", index: int = 1
) -> tuple[Path, Path]:
    step, person = ("first", "one") if index == 1 else ("second", "two")
    complete = ("first",) if index == 1 or status == "failed" else ("first", "second")
    state = WorkflowExecutionState(
        "workflow",
        status,
        step,
        index,
        person,
        complete,
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    events = ([event("succeeded", 1)] if index == 2 else []) + [event(status, index)]
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events)
    )
    return state_path, events_path


def test_start_delegates_once_with_identical_objects_and_exact_persistence(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    supplied, definition, person = start(), workflow(), employee()
    event_before, calls = events.read_bytes(), 0
    contents = serialize_workflow_execution_state_json(supplied.running_state).encode()
    expected = RunningStatePersistenceResult(len(contents))

    def phase48(*args: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        assert all(
            actual is supplied_value
            for actual, supplied_value in zip(
                args, (supplied, definition, person, state, events), strict=True
            )
        )
        state.write_bytes(contents)
        return expected

    assert (
        route_prepared_start_persistence_phase_bridge_reentry(
            supplied, definition, person, state, events, phase48_function=phase48
        )
        is expected
    )
    assert (
        calls == 1
        and state.read_bytes() == contents
        and events.read_bytes() == event_before
    )


@pytest.mark.parametrize("route", ["complete", "failure"])
def test_terminal_routes_stop_unchanged_without_phase48(
    tmp_path: Path, route: str
) -> None:
    status = "succeeded" if route == "complete" else "failed"
    state, events = targets(tmp_path, status, 2)
    result: object = (
        WorkflowProgressionDecision(
            "workflow_complete",
            "workflow",
            "second",
            2,
            "two",
            None,
            None,
            None,
            "last_step_succeeded",
        )
        if route == "complete"
        else PersistedExecutionOutcome(
            "persisted_failure", "workflow", "second", 2, "two", "api_error"
        )
    )
    before = state.read_bytes(), events.read_bytes()
    assert (
        route_prepared_start_persistence_phase_bridge_reentry(
            result,
            workflow(),
            None,
            state,
            events,
            phase48_function=lambda *_: pytest.fail("must not call Phase 48"),
        )
        is result
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "result, classification",
    [
        (object(), "result_type"),
        (
            PersistedExecutionOutcome(
                "persisted_success", "workflow", "second", 2, "two", None
            ),
            "failure_contract",
        ),
    ],
)
def test_rejects_invalid_results_before_phase48(
    tmp_path: Path, result: object, classification: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    with pytest.raises(PreparedStartPersistencePhaseBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_phase_bridge_reentry(
            result,
            workflow(),
            employee(),
            state,
            events,
            phase48_function=lambda *_: pytest.fail("called"),
        )
    assert (
        caught.value.detail.classification == classification
        and (state.read_bytes(), events.read_bytes()) == before
    )


@pytest.mark.parametrize("mutation", ["replace", "delete", "truncate", "append"])
def test_malformed_phase48_effects_restore_both_targets(
    tmp_path: Path, mutation: str
) -> None:
    state, events = targets(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def phase48(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation == "replace":
            state.write_bytes(b"private")
        elif mutation == "delete":
            state.unlink()
        elif mutation == "truncate":
            state.write_bytes(b"")
        else:
            state.write_bytes(state.read_bytes() + b"private")
        events.write_bytes(b"private")
        return SimpleNamespace(state_bytes_written=1)

    with pytest.raises(PreparedStartPersistencePhaseBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_phase_bridge_reentry(
            start(), workflow(), employee(), state, events, phase48_function=phase48
        )
    assert caught.value.detail.classification == "persistence_contract" and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before and "private" not in str(
        caught.value
    )


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
def test_dependency_errors_restore_and_are_safe(tmp_path: Path, kind: str) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = PreparedStartPersistenceBridgeError("private detail")

    def phase48(*_: object) -> object:
        state.write_bytes(b"private")
        events.unlink()
        if kind == "safe":
            raise safe
        raise RuntimeError("private detail")

    expected = (
        PreparedStartPersistenceBridgeError
        if kind == "safe"
        else PreparedStartPersistencePhaseBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_prepared_start_persistence_phase_bridge_reentry(
            start(), workflow(), employee(), state, events, phase48_function=phase48
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert (
            caught.value.detail.classification == "dependency_error"
            and "private" not in str(caught.value)
        )
    assert (state.read_bytes(), events.read_bytes()) == before


def test_rejects_start_substitute_employee_and_invalid_persistence_result(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedStartPersistencePhaseBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_phase_bridge_reentry(
            start(), workflow(), None, state, events
        )
    assert caught.value.detail.classification == "employee_contract"
    before = state.read_bytes(), events.read_bytes()
    with pytest.raises(PreparedStartPersistencePhaseBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_phase_bridge_reentry(
            replace(start(), request=SimpleNamespace()),
            workflow(),
            employee(),
            state,
            events,
        )
    assert (
        caught.value.detail.classification == "start_contract"
        and (state.read_bytes(), events.read_bytes()) == before
    )
