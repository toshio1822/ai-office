"""Tests for Phase 41 persistence routing."""

from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PreparedStartPersistenceRoutingCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_reentry,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
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


def complete(**changes: object) -> WorkflowProgressionDecision:
    values: dict[str, object] = {
        "decision": "workflow_complete",
        "workflow_id": "workflow",
        "current_step_id": "second",
        "current_step_index": 2,
        "current_employee_id": "two",
        "next_step_id": None,
        "next_step_index": None,
        "next_employee_id": None,
        "reason": "last_step_succeeded",
    }
    values.update(changes)
    return WorkflowProgressionDecision(**values)  # type: ignore[arg-type]


def targets(tmp_path: Path) -> tuple[Path, Path]:
    state, events = tmp_path / "state", tmp_path / "events"
    state.write_bytes(b"prior")
    events.write_bytes(b"events")
    return state, events


def persist(
    value: PreparedStepExecutionStart, path: Path
) -> RunningStatePersistenceResult:
    contents = serialize_workflow_execution_state_json(value.running_state).encode()
    path.write_bytes(contents)
    return RunningStatePersistenceResult(len(contents))


def test_persists_exact_start_once_and_returns_same_result(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    supplied, definition, person = start(), workflow(), employee()
    expected = RunningStatePersistenceResult(
        len(serialize_workflow_execution_state_json(supplied.running_state).encode())
    )
    calls: list[tuple[object, ...]] = []

    def phase35(*args: object) -> RunningStatePersistenceResult:
        calls.append(args)
        persist(supplied, state)
        return expected

    assert (
        route_prepared_start_persistence_reentry(
            supplied,
            definition,
            person,
            state,
            events,
            persistence_reentry_function=phase35,
        )
        is expected
    )
    assert calls == [(definition, person, state, events, supplied)]
    assert state.read_text() == serialize_workflow_execution_state_json(
        supplied.running_state
    )
    assert events.read_bytes() == b"events"


def test_complete_returns_same_object_after_target_reads_without_phase35(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    supplied = complete()
    assert (
        route_prepared_start_persistence_reentry(
            supplied,
            workflow(),
            None,
            state,
            events,
            persistence_reentry_function=lambda *_: pytest.fail("called"),
        )
        is supplied
    )
    assert (state.read_bytes(), events.read_bytes()) == (b"prior", b"events")


@pytest.mark.parametrize(
    "result,definition,person,state_value,event_value,function",
    [
        (object(), None, None, None, None, None),
        (None, object(), None, None, None, None),
        (None, None, None, 1, None, None),
        (None, None, None, None, 1, None),
        (None, None, None, "same", "same", None),
        (None, None, None, None, None, 1),
        (None, None, 1, None, None, None),
    ],
)
def test_prevalidation_zero_calls_and_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: object,
    definition: object,
    person: object,
    state_value: object,
    event_value: object,
    function: object,
) -> None:
    state, events = targets(tmp_path)
    writes: list[Path] = []
    original = Path.write_bytes
    monkeypatch.setattr(
        Path, "write_bytes", lambda p, b: (writes.append(p), original(p, b))[1]
    )
    actual_state, actual_events = (
        state if state_value is None else state_value,
        events if event_value is None else event_value,
    )
    if state_value == "same":
        actual_state = actual_events = state
    with pytest.raises(PreparedStartPersistenceRoutingCompatibilityError):
        route_prepared_start_persistence_reentry(
            start() if result is None else result,
            workflow() if definition is None else definition,
            employee() if person is None else person,
            actual_state,
            actual_events,
            persistence_reentry_function=(lambda *_: pytest.fail("called"))
            if function is None
            else function,
        )  # type: ignore[arg-type]
    assert writes == []


@pytest.mark.parametrize("mode", ["wrong_return", "count", "event", "state"])
def test_invalid_phase35_result_restores_original_targets(
    tmp_path: Path, mode: str
) -> None:
    state, events = targets(tmp_path)
    supplied = start()

    def phase35(*_: object) -> object:
        if mode == "event":
            events.write_bytes(b"changed")
        elif mode == "state":
            state.write_bytes(b"wrong")
        else:
            persist(supplied, state)
        return (
            object()
            if mode == "wrong_return"
            else RunningStatePersistenceResult(
                0 if mode == "count" else len(state.read_bytes())
            )
        )

    with pytest.raises(PreparedStartPersistenceRoutingCompatibilityError):
        route_prepared_start_persistence_reentry(
            supplied,
            workflow(),
            employee(),
            state,
            events,
            persistence_reentry_function=phase35,
        )  # type: ignore[arg-type]
    assert (state.read_bytes(), events.read_bytes()) == (b"prior", b"events")


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_dependency_errors_restore_targets_without_retry(
    tmp_path: Path, target: str, operation: str
) -> None:
    state, events = targets(tmp_path)
    changed = state if target == "state" else events
    calls = 0

    def phase35(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        if operation == "delete":
            changed.unlink()
        else:
            changed.write_bytes(b"changed")
        raise RuntimeError("credential=secret")

    with pytest.raises(PreparedStartPersistenceRoutingCompatibilityError) as error:
        route_prepared_start_persistence_reentry(
            start(),
            workflow(),
            employee(),
            state,
            events,
            persistence_reentry_function=phase35,
        )
    assert (
        error.value.detail.classification == "dependency_error"
        and "secret" not in str(error.value)
    )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (
        b"prior",
        b"events",
    )
