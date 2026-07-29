"""Phase 62 contract tests using injected Phase 55 fakes only."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStartPersistencePhaseBridgeError,
    PreparedStartPersistenceRoutingPhaseBridgeCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_routing_phase_bridge_reentry,
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


def event(status: str = "succeeded", index: int = 1) -> RuntimeStepEvent:
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
    )


def targets(
    tmp_path: Path, status: str = "succeeded", index: int = 1
) -> tuple[Path, Path]:
    if index == 1:
        state = WorkflowExecutionState(
            "workflow", status, "first", 1, "one", ("first",), None
        )
        events = (event(status, 1),)
    else:
        state = WorkflowExecutionState(
            "workflow",
            status,
            "second",
            2,
            "two",
            ("first", "second") if status == "succeeded" else ("first",),
            None if status == "succeeded" else "api_error",
        )
        events = (event("succeeded", 1), event(status, 2))
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


def assert_rejected(
    tmp_path: Path,
    result: object,
    *,
    definition: object | None = None,
    person: object | None = None,
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

    def dependency_fake(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        return RunningStatePersistenceResult(1)

    supplied_employee = employee() if person is None else person
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_start_persistence_routing_phase_bridge_reentry(
            result,
            workflow() if definition is None else definition,
            supplied_employee,
            actual_state,
            actual_events,
            phase55_function=dependency_fake if dependency is None else dependency,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == classification
    assert calls == 0
    if before is not None:
        assert (actual_state.read_bytes(), actual_events.read_bytes()) == before


def test_start_delegates_once_with_identity_and_exact_allowed_persistence(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    supplied, definition, person = start(), workflow(), employee()
    before_events = events.read_bytes()
    contents = serialize_workflow_execution_state_json(supplied.running_state).encode()
    expected = RunningStatePersistenceResult(len(contents))
    calls = 0

    def phase55(*args: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        assert all(
            actual is expected_arg
            for actual, expected_arg in zip(
                args, (supplied, definition, person, state, events), strict=True
            )
        )
        state.write_bytes(contents)
        return expected

    assert (
        route_prepared_start_persistence_routing_phase_bridge_reentry(
            supplied, definition, person, state, events, phase55_function=phase55
        )
        is expected
    )
    assert (
        calls == 1
        and state.read_bytes() == contents
        and events.read_bytes() == before_events
    )


@pytest.mark.parametrize("route", ["complete", "failure"])
def test_stop_routes_return_same_object_without_dependency(
    tmp_path: Path, route: str
) -> None:
    state, events = targets(
        tmp_path, "succeeded" if route == "complete" else "failed", 2
    )
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
        route_prepared_start_persistence_routing_phase_bridge_reentry(
            result,
            workflow(),
            None,
            state,
            events,
            phase55_function=lambda *_: pytest.fail("must not call Phase 55"),
        )
        is result
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "result, classification",
    [
        (object(), "result_type"),
        (
            replace(
                start(),
                request=ModelInvocationRequest("wrong", "employee", "b", ("tool",)),
            ),
            "start_contract",
        ),
        (
            PersistedExecutionOutcome(
                "persisted_success", "workflow", "second", 2, "two", None
            ),
            "failure_contract",
        ),
    ],
)
def test_rejects_result_and_contract_mismatches_before_call(
    tmp_path: Path, result: object, classification: str
) -> None:
    assert_rejected(tmp_path, result, classification=classification)


@pytest.mark.parametrize(
    "value",
    [WorkflowSubclass, EmployeeSubclass, StartSubclass, RequestSubclass, StateSubclass],
)
def test_rejects_model_subclasses(tmp_path: Path, value: type[object]) -> None:
    supplied = start()
    if value is WorkflowSubclass:
        definition: object = WorkflowSubclass.model_validate(workflow().model_dump())
        assert_rejected(
            tmp_path,
            supplied,
            definition=definition,
            classification="workflow_definition",
        )
    elif value is EmployeeSubclass:
        assert_rejected(
            tmp_path,
            supplied,
            person=EmployeeSubclass.model_validate(employee().model_dump()),
            classification="employee_contract",
        )
    elif value is StartSubclass:
        result: object = StartSubclass(supplied.request, supplied.running_state)
        assert_rejected(tmp_path, result, classification="result_type")
    elif value is RequestSubclass:
        result = PreparedStepExecutionStart(
            RequestSubclass("model", "employee", "b", ("tool",)), supplied.running_state
        )
        assert_rejected(tmp_path, result, classification="start_contract")
    else:
        result = PreparedStepExecutionStart(
            supplied.request,
            StateSubclass(
                supplied.running_state.workflow_id,
                supplied.running_state.status,
                supplied.running_state.current_step_id,
                supplied.running_state.current_step_index,
                supplied.running_state.current_employee_id,
                supplied.running_state.completed_step_ids,
                supplied.running_state.last_failure_category,
            ),
        )
        assert_rejected(tmp_path, result, classification="start_contract")


@pytest.mark.parametrize(
    "field",
    [
        "workflow_id",
        "current_step_id",
        "current_employee_id",
        "status",
        "completed_step_ids",
    ],
)
def test_rejects_running_state_field_mismatch(tmp_path: Path, field: str) -> None:
    supplied = start()
    updates = {field: StringSubclass("running") if field == "status" else "bad"}
    if field == "completed_step_ids":
        updates[field] = ("wrong",)
    assert_rejected(
        tmp_path,
        replace(supplied, running_state=replace(supplied.running_state, **updates)),
        classification="start_contract",
    )


@pytest.mark.parametrize(
    "field", ["model", "system_instructions", "task_instructions", "allowed_tools"]
)
def test_rejects_request_field_mismatch(tmp_path: Path, field: str) -> None:
    supplied = start()
    value: object = StringSubclass("bad") if field != "allowed_tools" else ("bad",)
    assert_rejected(
        tmp_path,
        replace(supplied, request=replace(supplied.request, **{field: value})),
        classification="start_contract",
    )


def test_rejects_nonregular_targets_and_target_conflict(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    assert_rejected(tmp_path, start(), state=tmp_path, classification="state_target")
    assert_rejected(tmp_path, start(), events=tmp_path, classification="event_target")
    assert_rejected(tmp_path, start(), events=state, classification="target_conflict")


@pytest.mark.parametrize(
    "mutation", ["replace", "delete", "truncate", "append", "event"]
)
def test_malformed_dependency_effects_restore_both_targets(
    tmp_path: Path, mutation: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def phase55(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation == "delete":
            state.unlink()
        elif mutation == "truncate":
            state.write_bytes(b"")
        elif mutation == "append":
            state.write_bytes(state.read_bytes() + b"private")
        elif mutation == "event":
            events.write_bytes(b"private")
        else:
            state.write_bytes(b"private")
        return SimpleNamespace(state_bytes_written=1)

    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_start_persistence_routing_phase_bridge_reentry(
            start(), workflow(), employee(), state, events, phase55_function=phase55
        )
    assert caught.value.detail.classification == "persistence_contract" and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
def test_dependency_errors_are_safe_and_not_retried(tmp_path: Path, kind: str) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0
    safe = PreparedStartPersistencePhaseBridgeError("private")

    def phase55(*_: object) -> object:
        nonlocal calls
        calls += 1
        if kind != "malformed":
            state.write_bytes(b"private")
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("private")
        return SimpleNamespace(state_bytes_written=1)

    expected = (
        PreparedStartPersistencePhaseBridgeError
        if kind == "safe"
        else PreparedStartPersistenceRoutingPhaseBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_prepared_start_persistence_routing_phase_bridge_reentry(
            start(), workflow(), employee(), state, events, phase55_function=phase55
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert (
            caught.value.detail.classification == "persistence_contract"
            if kind == "malformed"
            else caught.value.detail.classification == "dependency_error"
        )
        assert "private" not in str(caught.value)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_rollback_failures_attempt_both_targets_and_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    state, events = targets(tmp_path)
    original_write = Path.write_bytes
    calls = 0

    def phase55(*_: object) -> object:
        nonlocal calls
        calls += 1
        original_write(state, b"private")
        original_write(events, b"private")
        raise RuntimeError("private")

    def failing_write(path: Path, data: bytes) -> int:
        if target in ("both",) or path == (state if target == "state" else events):
            raise OSError("rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", failing_write)
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_start_persistence_routing_phase_bridge_reentry(
            start(), workflow(), employee(), state, events, phase55_function=phase55
        )
    assert caught.value.detail.classification == "dependency_rollback" and calls == 1
    assert "private" not in str(caught.value)
