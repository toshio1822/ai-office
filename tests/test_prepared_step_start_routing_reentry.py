"""Tests for Phase 40 prepared-step start routing."""

from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PreparedStepStartReentryCompatibilityError,
    PreparedStepStartRoutingCompatibilityError,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_prepared_step_start_reentry,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState


class PreparedSubclass(PreparedWorkflowStep):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


class StartSubclass(PreparedStepExecutionStart):
    pass


class RequestSubclass(ModelInvocationRequest):
    pass


class StateSubclass(WorkflowExecutionState):
    pass


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


def prepared(**changes: object) -> PreparedWorkflowStep:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "step_id": "second",
        "step_index": 2,
        "employee_id": "two",
        "employee_instructions": "employee",
        "step_instructions": "b",
        "model": "model",
        "allowed_tool_names": ("tool",),
    }
    values.update(changes)
    return PreparedWorkflowStep(**values)  # type: ignore[arg-type]


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


def started(**changes: object) -> PreparedStepExecutionStart:
    request = ModelInvocationRequest("model", "employee", "b", ("tool",))
    running = WorkflowExecutionState(
        "workflow", "running", "second", 2, "two", ("first",), None
    )
    values: dict[str, object] = {"request": request, "running_state": running}
    values.update(changes)
    return PreparedStepExecutionStart(**values)  # type: ignore[arg-type]


def targets(tmp_path: Path) -> tuple[Path, Path]:
    state, events = tmp_path / "state", tmp_path / "events"
    state.write_bytes(b"state")
    events.write_bytes(b"events")
    return state, events


def test_prepared_routes_once_with_exact_objects_and_same_start(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    supplied, definition, person, expected = (
        prepared(),
        workflow(),
        employee(),
        started(),
    )
    calls: list[tuple[object, ...]] = []

    def phase34(*args: object) -> PreparedStepExecutionStart:
        calls.append(args)
        return expected

    assert (
        route_prepared_step_start_reentry(
            supplied, definition, person, state, events, start_reentry_function=phase34
        )
        is expected
    )
    assert calls == [(definition, person, state, events, supplied)]
    assert (state.read_bytes(), events.read_bytes()) == (b"state", b"events")


def test_complete_stops_without_phase34_and_returns_same_object(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    supplied = complete()
    assert (
        route_prepared_step_start_reentry(
            supplied,
            workflow(),
            None,
            state,
            events,
            start_reentry_function=lambda *_: pytest.fail("Phase 34 must not run"),
        )
        is supplied
    )
    assert (state.read_bytes(), events.read_bytes()) == (b"state", b"events")


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
def test_general_prevalidation_has_zero_calls_and_writes(
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
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original(path, data))[1],
    )
    actual_state = state if state_value is None else state_value
    actual_events = events if event_value is None else event_value
    if state_value == "same":
        actual_state = actual_events = state
    with pytest.raises(PreparedStepStartRoutingCompatibilityError):
        route_prepared_step_start_reentry(
            prepared() if result is None else result,
            workflow() if definition is None else definition,
            employee() if person is None else person,
            actual_state,
            actual_events,
            start_reentry_function=(lambda *_: pytest.fail("called"))
            if function is None
            else function,
        )  # type: ignore[arg-type]
    assert writes == []


@pytest.mark.parametrize(
    "supplied,person",
    [
        (prepared(), None),
        (prepared(workflow_id="other"), employee()),
        (prepared(step_id="other"), employee()),
        (prepared(step_index=1), employee()),
        (prepared(step_index=3), employee()),
        (prepared(employee_id="other"), employee()),
        (prepared(employee_instructions="other"), employee()),
        (prepared(step_instructions="other"), employee()),
        (prepared(model="other"), employee()),
        (prepared(allowed_tool_names=()), employee()),
        (prepared(step_id=""), employee()),
        (prepared(allowed_tool_names=("",)), employee()),
        (complete(), employee()),
        (
            complete(
                decision="prepare_next_step",
                next_step_id="second",
                next_step_index=2,
                next_employee_id="two",
                reason="next_step_available",
            ),
            None,
        ),
        (complete(workflow_id="other"), None),
        (complete(current_step_id="first"), None),
        (complete(next_step_id="other"), None),
        (complete(reason="other"), None),
    ],
)
def test_route_contract_prevalidation_has_zero_calls_and_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supplied: object,
    person: object | None,
) -> None:
    state, events = targets(tmp_path)
    writes: list[Path] = []
    original = Path.write_bytes
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original(path, data))[1],
    )
    with pytest.raises(PreparedStepStartRoutingCompatibilityError):
        route_prepared_step_start_reentry(
            supplied,
            workflow(),
            person,
            state,
            events,
            start_reentry_function=lambda *_: pytest.fail("Phase 34 must not run"),
        )  # type: ignore[arg-type]
    assert writes == []


@pytest.mark.parametrize("field", ["request", "running_state"])
def test_start_wrong_member_type_is_rejected(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    value = started(**{field: object()})
    with pytest.raises(PreparedStepStartRoutingCompatibilityError) as error:
        route_prepared_step_start_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_reentry_function=lambda *_: value,
        )
    assert error.value.detail.classification == "start_contract"


@pytest.mark.parametrize(
    "value",
    [
        started(request=ModelInvocationRequest("other", "employee", "b", ("tool",))),
        started(request=ModelInvocationRequest("model", "other", "b", ("tool",))),
        started(
            request=ModelInvocationRequest("model", "employee", "other", ("tool",))
        ),
        started(request=ModelInvocationRequest("model", "employee", "b", ())),
        started(
            running_state=WorkflowExecutionState(
                "other", "running", "second", 2, "two", ("first",), None
            )
        ),
        started(
            running_state=WorkflowExecutionState(
                "workflow", "succeeded", "second", 2, "two", ("first",), None
            )
        ),
        started(
            running_state=WorkflowExecutionState(
                "workflow", "running", "other", 2, "two", ("first",), None
            )
        ),
        started(
            running_state=WorkflowExecutionState(
                "workflow", "running", "second", 3, "two", ("first",), None
            )
        ),
        started(
            running_state=WorkflowExecutionState(
                "workflow", "running", "second", 2, "other", ("first",), None
            )
        ),
        started(
            running_state=WorkflowExecutionState(
                "workflow", "running", "second", 2, "two", ["first"], None
            )
        ),
        started(
            running_state=WorkflowExecutionState(
                "workflow", "running", "second", 2, "two", ("",), None
            )
        ),
        started(
            running_state=WorkflowExecutionState(
                "workflow", "running", "second", 2, "two", ("second",), None
            )
        ),
        started(
            running_state=WorkflowExecutionState(
                "workflow", "running", "second", 2, "two", ("first",), "runtime"
            )
        ),
    ],
)
def test_start_contract_fields_are_rejected(
    tmp_path: Path, value: PreparedStepExecutionStart
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedStepStartRoutingCompatibilityError):
        route_prepared_step_start_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_reentry_function=lambda *_: value,
        )


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("operation", ["replace", "truncate", "append", "delete"])
def test_dependency_target_changes_are_restored(
    tmp_path: Path, target: str, operation: str
) -> None:
    state, events = targets(tmp_path)
    changed = state if target == "state" else events

    def phase34(*_: object) -> PreparedStepExecutionStart:
        if operation == "delete":
            changed.unlink()
        elif operation == "append":
            changed.write_bytes(changed.read_bytes() + b"new")
        elif operation == "truncate":
            changed.write_bytes(b"")
        else:
            changed.write_bytes(b"new")
        return started()

    with pytest.raises(PreparedStepStartRoutingCompatibilityError) as error:
        route_prepared_step_start_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_reentry_function=phase34,
        )
    assert error.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == (b"state", b"events")


@pytest.mark.parametrize(
    "phase_error, expected_type",
    [
        (
            PreparedStepStartReentryCompatibilityError("history_data"),
            PreparedStepStartReentryCompatibilityError,
        ),
        (RuntimeError("credential=secret"), PreparedStepStartRoutingCompatibilityError),
    ],
)
def test_dependency_errors_are_safe_not_retried_and_unchanged_do_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase_error: Exception,
    expected_type: type[Exception],
) -> None:
    state, events = targets(tmp_path)
    writes: list[Path] = []
    calls = 0
    original = Path.write_bytes
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original(path, data))[1],
    )

    def phase34(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        raise phase_error

    with pytest.raises(expected_type) as error:
        route_prepared_step_start_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_reentry_function=phase34,
        )
    assert calls == 1 and writes == []
    if expected_type is PreparedStepStartReentryCompatibilityError:
        assert error.value is phase_error
    else:
        assert error.value.detail.classification == "dependency_error"
        assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "supplied,definition,person",
    [
        (PreparedSubclass(*prepared().__dict__.values()), workflow(), employee()),
        (DecisionSubclass(**complete().__dict__), workflow(), None),
        (
            prepared(),
            WorkflowSubclass.model_validate(workflow().model_dump()),
            employee(),
        ),
        (
            prepared(),
            workflow(),
            EmployeeSubclass.model_validate(employee().model_dump()),
        ),
    ],
)
def test_exact_input_subclasses_are_rejected_before_call(
    tmp_path: Path, supplied: object, definition: object, person: object | None
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedStepStartRoutingCompatibilityError):
        route_prepared_step_start_reentry(
            supplied,
            definition,
            person,
            state,
            events,
            start_reentry_function=lambda *_: pytest.fail("Phase 34 must not run"),
        )  # type: ignore[arg-type]


def test_completion_captures_targets_without_writes_or_phase34(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, events = targets(tmp_path)
    reads: list[Path] = []
    writes: list[Path] = []
    original_read, original_write = Path.read_bytes, Path.write_bytes
    monkeypatch.setattr(
        Path, "read_bytes", lambda path: (reads.append(path), original_read(path))[1]
    )
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original_write(path, data))[1],
    )
    supplied = complete()
    assert (
        route_prepared_step_start_reentry(
            supplied,
            workflow(),
            None,
            state,
            events,
            start_reentry_function=lambda *_: pytest.fail("Phase 34 must not run"),
        )
        is supplied
    )
    assert reads == [state, events] and writes == []


@pytest.mark.parametrize("target", ["state", "events"])
def test_completion_capture_failure_is_safe_with_zero_writes_and_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    state, events = targets(tmp_path)
    blocked = state if target == "state" else events
    writes: list[Path] = []
    original_read, original_write = Path.read_bytes, Path.write_bytes

    def read(path: Path) -> bytes:
        if path == blocked:
            raise OSError
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original_write(path, data))[1],
    )
    with pytest.raises(PreparedStepStartRoutingCompatibilityError) as error:
        route_prepared_step_start_reentry(
            complete(),
            workflow(),
            None,
            state,
            events,
            start_reentry_function=lambda *_: pytest.fail("Phase 34 must not run"),
        )
    assert error.value.detail.classification == "dependency_error" and writes == []


@pytest.mark.parametrize(
    "supplied",
    [
        complete(current_step_index=1),
        complete(current_employee_id="one"),
        complete(next_step_index=3),
        complete(next_employee_id="other"),
    ],
)
def test_completion_contract_field_mismatches_do_not_call_phase34(
    tmp_path: Path, supplied: WorkflowProgressionDecision
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedStepStartRoutingCompatibilityError):
        route_prepared_step_start_reentry(
            supplied,
            workflow(),
            None,
            state,
            events,
            start_reentry_function=lambda *_: pytest.fail("Phase 34 must not run"),
        )


@pytest.mark.parametrize(
    "value",
    [
        object(),
        StartSubclass(*started().__dict__.values()),
        started(request=RequestSubclass(*started().request.__dict__.values())),
        started(
            running_state=StateSubclass(*started().running_state.__dict__.values())
        ),
    ],
)
def test_exact_phase34_return_types_reject_subclasses(
    tmp_path: Path, value: object
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedStepStartRoutingCompatibilityError) as error:
        route_prepared_step_start_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_reentry_function=lambda *_: value,  # type: ignore[return-value]
        )
    assert error.value.detail.classification == "start_contract"


@pytest.mark.parametrize(
    "kind", ["safe_change", "unexpected_change", "unexpected_delete"]
)
def test_changed_dependency_errors_restore_without_retry(
    tmp_path: Path, kind: str
) -> None:
    state, events = targets(tmp_path)
    expected = PreparedStepStartReentryCompatibilityError("history_data")
    calls = 0

    def phase34(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        if kind == "unexpected_delete":
            events.unlink()
        else:
            state.write_bytes(b"changed")
        if kind == "safe_change":
            raise expected
        raise RuntimeError("credential=secret")

    expected_type = (
        PreparedStepStartReentryCompatibilityError
        if kind == "safe_change"
        else PreparedStepStartRoutingCompatibilityError
    )
    with pytest.raises(expected_type) as error:
        route_prepared_step_start_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_reentry_function=phase34,
        )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (
        b"state",
        b"events",
    )
    if kind == "safe_change":
        assert error.value is expected
    else:
        assert error.value.detail.classification == "dependency_error"
        assert "secret" not in str(error.value)


@pytest.mark.parametrize("target", ["state", "events"])
def test_restoration_failures_are_dependency_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    state, events = targets(tmp_path)
    changed = state if target == "state" else events
    original = Path.write_bytes

    def write(path: Path, data: bytes) -> int:
        if path == changed:
            raise OSError
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)

    def phase34(*_: object) -> PreparedStepExecutionStart:
        changed.unlink()
        return started()

    with pytest.raises(PreparedStepStartRoutingCompatibilityError) as error:
        route_prepared_step_start_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_reentry_function=phase34,
        )
    assert error.value.detail.classification == "dependency_rollback"
