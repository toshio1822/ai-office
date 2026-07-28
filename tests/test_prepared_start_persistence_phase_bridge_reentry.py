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


class StartSubclass(PreparedStepExecutionStart):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


class RequestSubclass(ModelInvocationRequest):
    pass


class StateSubclass(WorkflowExecutionState):
    pass


class ResultSubclass(RunningStatePersistenceResult):
    pass


class StringSubclass(str):
    pass


_DEFAULT = object()


def assert_rejected(
    tmp_path: Path,
    result: object,
    *,
    definition: object | None = None,
    person: object = _DEFAULT,
    state: object | None = None,
    events: object | None = None,
    dependency: object | None = None,
    classification: str,
) -> None:
    actual_state, actual_events = targets(tmp_path)
    actual_state = actual_state if state is None else state
    actual_events = actual_events if events is None else events
    before = (
        (actual_state.read_bytes(), actual_events.read_bytes())
        if isinstance(actual_state, Path)
        and isinstance(actual_events, Path)
        and actual_state.is_file()
        and actual_events.is_file()
        else None
    )
    calls = 0

    def phase48(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        return RunningStatePersistenceResult(1)

    with pytest.raises(PreparedStartPersistencePhaseBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_phase_bridge_reentry(
            result,
            workflow() if definition is None else definition,
            employee() if person is _DEFAULT else person,
            actual_state,
            actual_events,
            phase48_function=phase48 if dependency is None else dependency,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == classification and calls == 0
    if before is not None:
        assert (actual_state.read_bytes(), actual_events.read_bytes()) == before


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


@pytest.mark.parametrize("value", [2.0, True, "2", None, object(), StringSubclass("2")])
def test_completion_requires_exact_integer_index(tmp_path: Path, value: object) -> None:
    state, events = targets(tmp_path, "succeeded", 2)
    decision = WorkflowProgressionDecision(
        "workflow_complete",
        "workflow",
        "second",
        value,
        "two",
        None,
        None,
        None,
        "last_step_succeeded",
    )  # type: ignore[arg-type]
    assert_rejected(
        tmp_path,
        decision,
        person=None,
        state=state,
        events=events,
        classification="completion_contract",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", StringSubclass("workflow_complete")),
        ("workflow_id", StringSubclass("workflow")),
        ("current_step_id", StringSubclass("second")),
        ("current_employee_id", StringSubclass("two")),
        ("reason", StringSubclass("last_step_succeeded")),
        ("next_step_id", "third"),
        ("next_step_index", 3),
        ("next_employee_id", "three"),
    ],
)
def test_completion_requires_every_exact_field(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path, "succeeded", 2)
    decision = replace(
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
        ),
        **{field: value},
    )
    assert_rejected(
        tmp_path,
        decision,
        person=None,
        state=state,
        events=events,
        classification="completion_contract",
    )


@pytest.mark.parametrize(
    ("part", "field", "value"),
    [
        *(
            ("request", field, value)
            for field in ("model", "system_instructions", "task_instructions")
            for value in ("wrong", StringSubclass("wrong"), 1, None)
        ),
        ("request", "allowed_tools", ["tool"]),
        ("request", "allowed_tools", ("tool", 1)),
        ("request", "allowed_tools", ("wrong",)),
        *(
            ("state", field, value)
            for field in (
                "workflow_id",
                "status",
                "current_step_id",
                "current_employee_id",
            )
            for value in ("wrong", StringSubclass("wrong"), 1, None)
        ),
        *(
            ("state", "current_step_index", value)
            for value in (True, 0, -1, 3, 2.0, "2", None, object())
        ),
        ("state", "completed_step_ids", ["first"]),
        ("state", "completed_step_ids", (1,)),
        ("state", "completed_step_ids", ("second",)),
        ("state", "completed_step_ids", ()),
        ("state", "last_failure_category", "api_error"),
    ],
)
def test_start_rejects_every_nested_field(
    tmp_path: Path, part: str, field: str, value: object
) -> None:
    supplied = start()
    nested = supplied.request if part == "request" else supplied.running_state
    changed = replace(nested, **{field: value})
    result = replace(
        supplied,
        **({"request": changed} if part == "request" else {"running_state": changed}),
    )
    assert_rejected(tmp_path, result, classification="start_contract")


@pytest.mark.parametrize(
    "result",
    [
        StartSubclass(start().request, start().running_state),
        SimpleNamespace(request=start().request, running_state=start().running_state),
    ],
)
@pytest.mark.parametrize(
    "definition, person, classification",
    [
        (
            WorkflowSubclass.model_validate(workflow().model_dump()),
            employee(),
            "workflow_definition",
        ),
        (SimpleNamespace(**workflow().__dict__), employee(), "workflow_definition"),
        (
            workflow(),
            EmployeeSubclass.model_validate(employee().model_dump()),
            "employee_contract",
        ),
        (workflow(), SimpleNamespace(**employee().__dict__), "employee_contract"),
    ],
)
def test_exact_models_reject_subclasses_and_substitutes(
    tmp_path: Path,
    result: object,
    definition: object,
    person: object,
    classification: str,
) -> None:
    assert_rejected(
        tmp_path,
        result,
        definition=definition,
        person=person,
        classification="result_type",
    )
    assert_rejected(
        tmp_path,
        start(),
        definition=definition,
        person=person,
        classification=classification,
    )


def test_top_level_targets_and_dependency_reject_before_writes(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    assert_rejected(
        tmp_path, start(), state=object(), events=events, classification="state_target"
    )
    assert_rejected(
        tmp_path, start(), state=state, events=object(), classification="event_target"
    )
    assert_rejected(
        tmp_path, start(), state=state, events=state, classification="target_conflict"
    )
    assert_rejected(
        tmp_path,
        start(),
        state=tmp_path / "missing",
        events=events,
        classification="state_target",
    )
    assert_rejected(
        tmp_path,
        start(),
        state=state,
        events=tmp_path / "missing",
        classification="event_target",
    )
    assert_rejected(
        tmp_path, start(), dependency=object(), classification="persistence_contract"
    )


@pytest.mark.parametrize(
    "result",
    [
        replace(start(), request=RequestSubclass(*start().request.__dict__.values())),
        replace(
            start(),
            running_state=StateSubclass(*start().running_state.__dict__.values()),
        ),
    ],
)
def test_nested_model_subclasses_reject_without_calls(
    tmp_path: Path, result: object
) -> None:
    assert_rejected(tmp_path, result, classification="start_contract")


@pytest.mark.parametrize(
    "result",
    [
        object(),
        ResultSubclass(1),
        SimpleNamespace(state_bytes_written=1),
        RunningStatePersistenceResult(True),
        RunningStatePersistenceResult(1.0),
        RunningStatePersistenceResult("1"),
        RunningStatePersistenceResult(None),
        RunningStatePersistenceResult(0),
        RunningStatePersistenceResult(-1),
        RunningStatePersistenceResult(999),
    ],
)
def test_all_bad_phase48_results_restore(tmp_path: Path, result: object) -> None:
    state, events = targets(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def phase48(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_text(serialize_workflow_execution_state_json(start().running_state))
        return result

    with pytest.raises(PreparedStartPersistencePhaseBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_phase_bridge_reentry(
            start(), workflow(), employee(), state, events, phase48_function=phase48
        )
    assert caught.value.detail.classification == "persistence_contract" and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("changed", ["state", "events", "both"])
@pytest.mark.parametrize("kind", ["safe", "unexpected"])
def test_restore_attempts_both_targets_after_dependency_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str, kind: str
) -> None:
    state, events = targets(tmp_path)
    original_write, attempts = Path.write_bytes, []
    safe = PreparedStartPersistenceBridgeError("private")

    def phase48(*_: object) -> object:
        if changed in ("state", "both"):
            original_write(state, b"private")
        if changed in ("events", "both"):
            original_write(events, b"private")
        if kind == "safe":
            raise safe
        raise RuntimeError("private")

    def record(path: Path, contents: bytes) -> int:
        attempts.append(path)
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", record)
    expected = (
        PreparedStartPersistenceBridgeError
        if kind == "safe"
        else PreparedStartPersistencePhaseBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_prepared_start_persistence_phase_bridge_reentry(
            start(), workflow(), employee(), state, events, phase48_function=phase48
        )
    assert state in attempts and events in attempts
    if kind == "safe":
        assert caught.value is safe
    else:
        assert (
            caught.value.detail.classification == "dependency_error"
            and "private" not in str(caught.value)
        )


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_restore_failure_attempts_both_and_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    state, events = targets(tmp_path)
    original_write, attempts = Path.write_bytes, []

    def phase48(*_: object) -> object:
        original_write(state, b"private")
        original_write(events, b"private")
        raise RuntimeError("private")

    def fail(path: Path, contents: bytes) -> int:
        attempts.append(path)
        if (
            failed == "both"
            or (failed == "state" and path == state)
            or (failed == "events" and path == events)
        ):
            raise OSError("private path")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", fail)
    with pytest.raises(PreparedStartPersistencePhaseBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_phase_bridge_reentry(
            start(), workflow(), employee(), state, events, phase48_function=phase48
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert (
        state in attempts and events in attempts and "private" not in str(caught.value)
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", "persisted_success"),
        ("workflow_id", "other"),
        ("current_step_id", "first"),
        *(
            ("current_step_index", value)
            for value in (True, 0, -1, 3, 2.0, "2", None, object())
        ),
    ]
    + [
        ("current_employee_id", "one"),
        *(
            ("failure_category", value)
            for value in (StringSubclass("api_error"), None, "other", 1)
        ),
    ],
)
def test_failure_requires_every_exact_field(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path, "failed", 2)
    result = replace(
        PersistedExecutionOutcome(
            "persisted_failure", "workflow", "second", 2, "two", "api_error"
        ),
        **{field: value},
    )
    assert_rejected(
        tmp_path,
        result,
        person=None,
        state=state,
        events=events,
        classification="failure_contract",
    )


@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("mutation", ["replace", "delete", "truncate", "append"])
def test_all_invalid_phase48_target_effects_restore(
    tmp_path: Path, target: str, mutation: str
) -> None:
    state, events = targets(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def phase48(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        for path in (
            (state, events)
            if target == "both"
            else (state,)
            if target == "state"
            else (events,)
        ):
            if mutation == "replace":
                path.write_bytes(b"private")
            elif mutation == "delete":
                path.unlink()
            elif mutation == "truncate":
                path.write_bytes(b"")
            else:
                path.write_bytes(path.read_bytes() + b"private")
        return RunningStatePersistenceResult(1)

    with pytest.raises(PreparedStartPersistencePhaseBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_phase_bridge_reentry(
            start(), workflow(), employee(), state, events, phase48_function=phase48
        )
    assert caught.value.detail.classification == "persistence_contract" and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before
