"""Tests for the Phase 47 bridge using injected Phase 40 fakes only."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    PreparedStepStartBridgeCompatibilityError,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_prepared_step_start_bridge_reentry,
)
from ai_office.engine.prepared_step_start_routing_reentry import (
    PreparedStepStartRoutingCompatibilityError,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class PreparedSubclass(PreparedWorkflowStep):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
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


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
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


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_failure", "workflow", "second", 2, "two", "api_error"
    )


def terminal_event(status: str, index: int = 1) -> RuntimeStepEvent:
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
    state = WorkflowExecutionState(
        "workflow",
        status,
        step,
        index,
        person,
        ("first",) if index == 1 or status == "failed" else ("first", "second"),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    events = [terminal_event("succeeded", 1)] if index == 2 else []
    events.append(terminal_event(status, index))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events)
    )
    return state_path, events_path


def started() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "employee", "b", ("tool",)),
        WorkflowExecutionState(
            "workflow", "running", "second", 2, "two", ("first",), None
        ),
    )


def test_prepared_delegates_once_with_exact_objects_and_same_start(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    supplied, definition, person, expected = (
        prepared(),
        workflow(),
        employee(),
        started(),
    )
    calls = 0

    def phase40(*args: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        actual_result, actual_workflow, actual_employee, actual_state, actual_events = (
            args
        )
        assert actual_result is supplied and actual_workflow is definition
        assert (
            actual_employee is person
            and actual_state is state
            and actual_events is events
        )
        return expected

    assert (
        route_prepared_step_start_bridge_reentry(
            supplied, definition, person, state, events, start_routing_function=phase40
        )
        is expected
    )
    assert calls == 1


@pytest.mark.parametrize("result_kind", ["completion", "failure"])
def test_terminal_routes_stop_with_same_object_and_strict_history(
    tmp_path: Path, result_kind: str
) -> None:
    status = "succeeded" if result_kind == "completion" else "failed"
    state, events = targets(tmp_path, status, 2)
    supplied: object = completion() if result_kind == "completion" else failure()
    before = state.read_bytes(), events.read_bytes()
    assert (
        route_prepared_step_start_bridge_reentry(
            supplied,
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: pytest.fail("Phase 40 must not run"),
        )
        is supplied
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("route", ["prepared", "completion", "failure"])
@pytest.mark.parametrize(
    "mode", ["running", "failed", "wrong_completed", "extra_event"]
)
def test_persisted_history_contract_rejects_before_phase40(
    tmp_path: Path, route: str, mode: str
) -> None:
    status, index = ("succeeded", 1) if route == "prepared" else ("succeeded", 2)
    if route == "failure":
        status = "failed"
    state, events = targets(tmp_path, status, index)
    if mode in {"running", "failed"}:
        replacement_status = mode if mode != status else "running"
        current = WorkflowExecutionState(
            "workflow",
            replacement_status,
            "first" if index == 1 else "second",
            index,
            "one" if index == 1 else "two",
            ("first",) if index == 1 else ("first", "second"),
            "api_error" if replacement_status == "failed" else None,
        )  # type: ignore[arg-type]
        state.write_text(serialize_workflow_execution_state_json(current))
    elif mode == "wrong_completed":
        current = WorkflowExecutionState(
            "workflow",
            status,
            "first" if index == 1 else "second",
            index,
            "one" if index == 1 else "two",
            (),
            None if status == "succeeded" else "api_error",
        )  # type: ignore[arg-type]
        state.write_text(serialize_workflow_execution_state_json(current))
    else:
        events.write_bytes(
            events.read_bytes() + events.read_bytes().splitlines(keepends=True)[0]
        )
    result: object = prepared() if route == "prepared" else completion()
    if route == "failure":
        result = failure()
    calls, writes = 0, []
    original = Path.write_bytes

    def phase40(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        raise AssertionError

    def record(path: Path, data: bytes) -> int:
        writes.append(path)
        return original(path, data)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
            route_prepared_step_start_bridge_reentry(
                result,
                workflow(),
                employee(),
                state,
                events,
                start_routing_function=phase40,
            )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0 and writes == []


@pytest.mark.parametrize("mode", ["invalid_state", "invalid_events", "wrong_terminal"])
def test_terminal_history_rejection_precedes_phase40(tmp_path: Path, mode: str) -> None:
    state, events = targets(tmp_path, "succeeded", 2)
    if mode == "invalid_state":
        state.write_bytes(b"not-json")
    elif mode == "invalid_events":
        events.write_bytes(b"not-json\n")
    else:
        events.write_text(
            serialize_runtime_step_event_jsonl(terminal_event("failed", 2))
        )
    calls, writes = 0, []
    original = Path.write_bytes

    def record(path: Path, data: bytes) -> int:
        writes.append(path)
        return original(path, data)

    def phase40(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
            route_prepared_step_start_bridge_reentry(
                completion(),
                workflow(),
                employee(),
                state,
                events,
                start_routing_function=phase40,
            )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0 and writes == []


@pytest.mark.parametrize("result_kind", ["completion", "failure"])
@pytest.mark.parametrize(
    "changes",
    [
        {"failure_category": "api_error"},
        {"response_id": None},
        {"response_id": 1},
        {"output_text": None},
        {"output_text": 1},
        {"message": "unexpected"},
    ],
)
def test_earlier_success_event_requires_existing_success_contract(
    tmp_path: Path, result_kind: str, changes: dict[str, object]
) -> None:
    status = "succeeded" if result_kind == "completion" else "failed"
    state, events = targets(tmp_path, status, 2)
    altered = replace(terminal_event("succeeded", 1), **changes)
    events.write_text(
        serialize_runtime_step_event_jsonl(altered)
        + serialize_runtime_step_event_jsonl(terminal_event(status, 2))
    )
    calls, writes = 0, []
    original = Path.write_bytes

    def phase40(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        raise AssertionError

    def record(path: Path, data: bytes) -> int:
        writes.append(path)
        return original(path, data)

    supplied: object = completion() if result_kind == "completion" else failure()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
            route_prepared_step_start_bridge_reentry(
                supplied,
                workflow(),
                employee(),
                state,
                events,
                start_routing_function=phase40,
            )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0 and writes == []


@pytest.mark.parametrize(
    "field", ["workflow_id", "step_id", "step_index", "employee_id"]
)
def test_phase40_return_contract_is_checked(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    value = started()
    changes = {
        "workflow_id": "wrong",
        "current_step_id": "wrong",
        "current_step_index": 1,
        "current_employee_id": "wrong",
    }
    names = {
        "workflow_id": "workflow_id",
        "step_id": "current_step_id",
        "step_index": "current_step_index",
        "employee_id": "current_employee_id",
    }
    bad_state = WorkflowExecutionState(
        **{**value.running_state.__dict__, names[field]: changes[names[field]]}
    )
    calls = 0

    def phase40(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return PreparedStepExecutionStart(value.request, bad_state)

    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=phase40,
        )
    assert caught.value.detail.classification == "start_contract" and calls == 1


@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
def test_dependency_mutation_is_compensated(
    tmp_path: Path, target: str, operation: str
) -> None:
    state, events = targets(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"changed")
        else:
            path.write_bytes(b"changed")

    def phase40(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            mutate(state)
        if target in {"events", "both"}:
            mutate(events)
        return started()

    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=phase40,
        )
    assert caught.value.detail.classification == "dependency_error"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_safe_error_identity_and_unexpected_error_sanitization(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    safe = PreparedStepStartRoutingCompatibilityError("prepared_step_contract")
    with pytest.raises(PreparedStepStartRoutingCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: (_ for _ in ()).throw(safe),
        )
    assert caught.value is safe
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: (_ for _ in ()).throw(
                RuntimeError("/private/path provider output failure")
            ),
        )
    assert caught.value.detail.classification == "dependency_error"
    assert "/private" not in str(caught.value) and "output" not in str(caught.value)


@pytest.mark.parametrize(
    (
        "result",
        "definition",
        "person",
        "state_value",
        "events_value",
        "function",
        "classification",
    ),
    [
        (object(), None, None, None, None, None, "result_type"),
        (
            PreparedSubclass(**prepared().__dict__),
            None,
            None,
            None,
            None,
            None,
            "result_type",
        ),
        (
            PersistedExecutionOutcome(
                "persisted_success", "workflow", "first", 1, "one", None
            ),
            None,
            None,
            None,
            None,
            None,
            "failure_contract",
        ),
        (
            prepared(),
            WorkflowSubclass(**workflow().__dict__),
            None,
            None,
            None,
            None,
            "workflow_definition",
        ),
        (
            prepared(),
            None,
            EmployeeSubclass(**employee().__dict__),
            None,
            None,
            None,
            "employee_contract",
        ),
        (prepared(), None, None, 1, None, None, "state_target"),
        (prepared(), None, None, None, 1, None, "event_target"),
        (prepared(), None, None, "conflict", "conflict", None, "target_conflict"),
        (prepared(), None, None, "missing", None, None, "state_target"),
        (prepared(), None, None, None, "missing", None, "event_target"),
        (prepared(), None, None, None, None, object(), "start_contract"),
        (
            prepared(step_index=True),
            None,
            None,
            "missing",
            "missing",
            None,
            "prepared_step_contract",
        ),
        (
            WorkflowProgressionDecision(
                "prepare_next_step",
                "workflow",
                "first",
                1,
                "one",
                "second",
                2,
                "two",
                "next_step_available",
            ),
            None,
            None,
            "missing",
            "missing",
            None,
            "completion_contract",
        ),
        (
            PersistedExecutionOutcome(
                "persisted_failure", "wrong", "second", 2, "two", "api_error"
            ),
            None,
            None,
            "missing",
            "missing",
            None,
            "failure_contract",
        ),
    ],
)
def test_prevalidation_rejections_do_not_call_or_write(
    tmp_path: Path,
    result: object,
    definition: object | None,
    person: object | None,
    state_value: object | None,
    events_value: object | None,
    function: object | None,
    classification: str,
) -> None:
    state, events = targets(tmp_path)
    if state_value == "missing":
        state.unlink()
    elif state_value == "conflict":
        events = state
    elif state_value is not None:
        state = state_value  # type: ignore[assignment]
    if events_value == "missing":
        events.unlink(missing_ok=True)
    elif events_value is not None and events_value != "conflict":
        events = events_value  # type: ignore[assignment]
    calls, writes = 0, []
    original = Path.write_bytes

    def phase40(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        raise AssertionError

    def record(path: Path, data: bytes) -> int:
        writes.append(path)
        return original(path, data)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
            route_prepared_step_start_bridge_reentry(
                result,
                definition if definition is not None else workflow(),
                person if person is not None else employee(),
                state,
                events,
                start_routing_function=(function if function is not None else phase40),
            )
    assert caught.value.detail.classification == classification
    assert calls == 0 and writes == []


@pytest.mark.parametrize(
    "changes",
    [
        {"step_index": 1},
        {"step_index": True},
        {"step_index": 0},
        {"step_index": -1},
        {"step_index": 3},
        {"workflow_id": "wrong"},
        {"step_id": "wrong"},
        {"employee_id": "wrong"},
        {"employee_instructions": "wrong"},
        {"step_instructions": "wrong"},
        {"model": "wrong"},
        {"allowed_tool_names": ("wrong",)},
        {"allowed_tool_names": ["tool"]},
        {"allowed_tool_names": ("tool", 1)},
    ],
)
def test_prepared_step_contract_rejections_precede_target_reads(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events = targets(tmp_path)
    writes, calls = [], 0
    original = Path.write_bytes

    def record(path: Path, data: bytes) -> int:
        writes.append(path)
        return original(path, data)

    def phase40(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
            route_prepared_step_start_bridge_reentry(
                prepared(**changes),
                workflow(),
                employee(),
                state,
                events,
                start_routing_function=phase40,
            )
    assert caught.value.detail.classification == "prepared_step_contract"
    assert calls == 0 and writes == []


@pytest.mark.parametrize(
    "result",
    [
        WorkflowProgressionDecision(
            "workflow_complete",
            "wrong",
            "second",
            2,
            "two",
            None,
            None,
            None,
            "last_step_succeeded",
        ),
        WorkflowProgressionDecision(
            "workflow_complete",
            "workflow",
            "wrong",
            2,
            "two",
            None,
            None,
            None,
            "last_step_succeeded",
        ),
        WorkflowProgressionDecision(
            "workflow_complete",
            "workflow",
            "second",
            True,
            "two",
            None,
            None,
            None,
            "last_step_succeeded",
        ),
        WorkflowProgressionDecision(
            "workflow_complete",
            "workflow",
            "second",
            2,
            "wrong",
            None,
            None,
            None,
            "last_step_succeeded",
        ),
        WorkflowProgressionDecision(
            "workflow_complete",
            "workflow",
            "second",
            2,
            "two",
            "next",
            None,
            None,
            "last_step_succeeded",
        ),
        WorkflowProgressionDecision(
            "workflow_complete",
            "workflow",
            "second",
            2,
            "two",
            None,
            None,
            None,
            "wrong",
        ),
    ],
)
def test_completion_contract_rejections_do_not_call_or_write(
    tmp_path: Path, result: WorkflowProgressionDecision
) -> None:
    state, events = targets(tmp_path, "succeeded", 2)
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            result,
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: pytest.fail("Phase 40 must not run"),
        )
    assert caught.value.detail.classification == "completion_contract"


@pytest.mark.parametrize(
    "result",
    [
        PersistedExecutionOutcome(
            "persisted_success", "workflow", "second", 2, "two", None
        ),
        PersistedExecutionOutcome(
            "persisted_failure", "wrong", "second", 2, "two", "api_error"
        ),
        PersistedExecutionOutcome(
            "persisted_failure", "workflow", "wrong", 2, "two", "api_error"
        ),
        PersistedExecutionOutcome(
            "persisted_failure", "workflow", "second", True, "two", "api_error"
        ),
        PersistedExecutionOutcome(
            "persisted_failure", "workflow", "second", 0, "two", "api_error"
        ),
        PersistedExecutionOutcome(
            "persisted_failure", "workflow", "second", 3, "two", "api_error"
        ),
        PersistedExecutionOutcome(
            "persisted_failure", "workflow", "second", 2, "wrong", "api_error"
        ),
        PersistedExecutionOutcome(
            "persisted_failure", "workflow", "second", 2, "two", None
        ),
        PersistedExecutionOutcome(
            "persisted_failure", "workflow", "second", 2, "two", "wrong"
        ),
    ],
)
def test_failure_contract_rejections_do_not_call_or_write(
    tmp_path: Path, result: PersistedExecutionOutcome
) -> None:
    state, events = targets(tmp_path, "failed", 2)
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            result,
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: pytest.fail("Phase 40 must not run"),
        )
    assert caught.value.detail.classification == "failure_contract"


def test_malformed_phase40_return_is_not_returned(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: object(),
        )
    assert caught.value.detail.classification == "start_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_return_subclass_is_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    value = started()
    subclass = StartSubclass(value.request, value.running_state)
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: subclass,
        )
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize(
    ("request_value", "classification"),
    [
        (ModelInvocationRequest("wrong", "employee", "b", ("tool",)), "start_contract"),
        (ModelInvocationRequest("model", "wrong", "b", ("tool",)), "start_contract"),
        (
            ModelInvocationRequest("model", "employee", "wrong", ("tool",)),
            "start_contract",
        ),
        (
            ModelInvocationRequest("model", "employee", "b", ("wrong",)),
            "start_contract",
        ),
    ],
)
def test_phase40_request_field_contract_is_checked(
    tmp_path: Path, request_value: ModelInvocationRequest, classification: str
) -> None:
    state, events = targets(tmp_path)
    value = started()
    malformed = PreparedStepExecutionStart(request_value, value.running_state)
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: malformed,
        )
    assert caught.value.detail.classification == classification


@pytest.mark.parametrize(
    "value",
    [
        PreparedStepExecutionStart(
            RequestSubclass("model", "employee", "b", ("tool",)),
            WorkflowExecutionState(
                "workflow", "running", "second", 2, "two", ("first",), None
            ),
        ),
        PreparedStepExecutionStart(
            ModelInvocationRequest("model", "employee", "b", ("tool",)),
            StateSubclass("workflow", "running", "second", 2, "two", ("first",), None),
        ),
    ],
)
def test_phase40_nested_subclasses_are_rejected(
    tmp_path: Path, value: PreparedStepExecutionStart
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: value,
        )
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "ready"},
        {"status": "succeeded"},
        {"status": "failed"},
        {"completed_step_ids": ()},
        {"last_failure_category": "api_error"},
    ],
)
def test_phase40_running_state_contract_is_checked(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events = targets(tmp_path)
    value = started()
    malformed = PreparedStepExecutionStart(
        value.request,
        WorkflowExecutionState(**{**value.running_state.__dict__, **changes}),
    )
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: malformed,
        )
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize("error_kind", ["safe", "unexpected"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_dependency_error_after_mutation_restores_originals(
    tmp_path: Path, error_kind: str, target: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = PreparedStepStartRoutingCompatibilityError("prepared_step_contract")

    def phase40(*_: object) -> PreparedStepExecutionStart:
        if target in {"state", "both"}:
            state.unlink()
        if target in {"events", "both"}:
            events.write_bytes(b"changed")
        if error_kind == "safe":
            raise safe
        raise RuntimeError("/private/path provider response output failure")

    expected = (
        PreparedStepStartRoutingCompatibilityError
        if error_kind == "safe"
        else PreparedStepStartBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=phase40,
        )
    assert (state.read_bytes(), events.read_bytes()) == before
    if error_kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
        assert "/private" not in str(caught.value)


@pytest.mark.parametrize("fail_targets", [("state",), ("events",), ("state", "events")])
def test_rollback_failure_attempts_both_targets(
    tmp_path: Path, fail_targets: tuple[str, ...]
) -> None:
    state, events = targets(tmp_path)
    before = {state: state.read_bytes(), events: events.read_bytes()}
    restore_attempts: list[Path] = []
    original = Path.write_bytes

    def record(path: Path, data: bytes) -> int:
        if data == before.get(path):
            restore_attempts.append(path)
            name = "state" if path is state else "events"
            if name in fail_targets:
                raise OSError("/private/rollback failure")
        return original(path, data)

    def phase40(*_: object) -> PreparedStepExecutionStart:
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        return started()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
            route_prepared_step_start_bridge_reentry(
                prepared(),
                workflow(),
                employee(),
                state,
                events,
                start_routing_function=phase40,
            )
    assert caught.value.detail.classification == "dependency_rollback"
    assert restore_attempts == [state, events]
    assert "/private" not in str(caught.value)


@pytest.mark.parametrize("error_kind", ["safe", "unexpected"])
def test_unchanged_dependency_errors_do_not_write_or_retry(
    tmp_path: Path, error_kind: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    writes, calls = [], 0
    safe = PreparedStepStartRoutingCompatibilityError("prepared_step_contract")
    original = Path.write_bytes

    def record(path: Path, data: bytes) -> int:
        writes.append(path)
        return original(path, data)

    def phase40(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        if error_kind == "safe":
            raise safe
        raise RuntimeError(
            "/private/path workflow employee request response output failure message"
        )

    expected = (
        PreparedStepStartRoutingCompatibilityError
        if error_kind == "safe"
        else PreparedStepStartBridgeCompatibilityError
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(expected) as caught:
            route_prepared_step_start_bridge_reentry(
                prepared(),
                workflow(),
                employee(),
                state,
                events,
                start_routing_function=phase40,
            )
    assert calls == 1 and writes == []
    assert (state.read_bytes(), events.read_bytes()) == before
    if error_kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
        public = str(caught.value)
        assert all(secret not in public for secret in ("/private", "request", "output"))


@pytest.mark.parametrize("error_kind", ["safe", "unexpected"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
def test_all_dependency_error_mutations_are_compensated(
    tmp_path: Path, error_kind: str, target: str, operation: str
) -> None:
    state, events = targets(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0
    safe = PreparedStepStartRoutingCompatibilityError("prepared_step_contract")

    def mutate(path: Path) -> None:
        if operation == "replace":
            path.write_bytes(b"changed")
        elif operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        else:
            path.write_bytes(path.read_bytes() + b"changed")

    def phase40(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            mutate(state)
        if target in {"events", "both"}:
            mutate(events)
        if error_kind == "safe":
            raise safe
        raise RuntimeError("/private/provider response output failure")

    expected = (
        PreparedStepStartRoutingCompatibilityError
        if error_kind == "safe"
        else PreparedStepStartBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=phase40,
        )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before
    if error_kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
        assert "/private" not in str(caught.value)


@pytest.mark.parametrize("dependency_result", ["return", "safe", "unexpected"])
@pytest.mark.parametrize("fail_targets", [("state",), ("events",), ("state", "events")])
def test_rollback_failure_overrides_all_dependency_results(
    tmp_path: Path, dependency_result: str, fail_targets: tuple[str, ...]
) -> None:
    state, events = targets(tmp_path)
    before = {state: state.read_bytes(), events: events.read_bytes()}
    attempts, calls = [], 0
    original = Path.write_bytes
    safe = PreparedStepStartRoutingCompatibilityError("prepared_step_contract")

    def record(path: Path, data: bytes) -> int:
        if data == before.get(path):
            attempts.append(path)
            if ("state" if path is state else "events") in fail_targets:
                raise OSError("/private/rollback failure")
        return original(path, data)

    def phase40(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        if dependency_result == "safe":
            raise safe
        if dependency_result == "unexpected":
            raise RuntimeError("/private/provider response output failure")
        return started()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
            route_prepared_step_start_bridge_reentry(
                prepared(),
                workflow(),
                employee(),
                state,
                events,
                start_routing_function=phase40,
            )
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1 and attempts == [state, events]
    public = str(caught.value)
    assert all(secret not in public for secret in ("/private", "response", "output"))


def test_attribute_compatible_substitutes_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)

    class Compatible:
        pass

    compatible = Compatible()
    for source in (prepared(), completion(), failure(), employee()):
        compatible.__dict__.update(source.__dict__)
        with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
            route_prepared_step_start_bridge_reentry(
                compatible,
                workflow(),
                employee(),
                state,
                events,
                start_routing_function=lambda *_: pytest.fail("Phase 40 must not run"),
            )
        assert caught.value.detail.classification == "result_type"
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            prepared(),
            workflow(),
            compatible,
            state,
            events,
            start_routing_function=lambda *_: pytest.fail("Phase 40 must not run"),
        )
    assert caught.value.detail.classification == "employee_contract"


@pytest.mark.parametrize(
    "result",
    [
        DecisionSubclass(**completion().__dict__),
        OutcomeSubclass(**failure().__dict__),
    ],
)
def test_non_prepared_result_subclasses_are_rejected_without_writes(
    tmp_path: Path, result: object
) -> None:
    state, events = targets(tmp_path, "succeeded", 2)
    writes, calls = [], 0
    original = Path.write_bytes

    def record(path: Path, data: bytes) -> int:
        writes.append(path)
        return original(path, data)

    def phase40(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
            route_prepared_step_start_bridge_reentry(
                result,
                workflow(),
                employee(),
                state,
                events,
                start_routing_function=phase40,
            )
    assert caught.value.detail.classification == "result_type"
    assert calls == 0 and writes == []


def test_phase40_attribute_compatible_returns_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)

    class Compatible:
        pass

    request = Compatible()
    request.__dict__.update(started().request.__dict__)
    running = Compatible()
    running.__dict__.update(started().running_state.__dict__)
    values = [
        Compatible(),
        PreparedStepExecutionStart(request, started().running_state),
    ]
    values.append(PreparedStepExecutionStart(started().request, running))
    for value in values:
        with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
            route_prepared_step_start_bridge_reentry(
                prepared(),
                workflow(),
                employee(),
                state,
                events,
                start_routing_function=lambda *_: value,
            )
        assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize(
    "changes",
    [
        {"current_step_index": 0},
        {"current_step_index": -1},
        {"current_step_index": 3},
        {"next_step_index": 1},
        {"next_employee_id": "two"},
    ],
)
def test_completion_route_specific_fields_are_rejected(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events = targets(tmp_path, "succeeded", 2)
    result = WorkflowProgressionDecision(**{**completion().__dict__, **changes})
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            result,
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: pytest.fail("Phase 40 must not run"),
        )
    assert caught.value.detail.classification == "completion_contract"


@pytest.mark.parametrize(
    "mode",
    ["category", "response", "output", "missing_message", "invalid_message"],
)
def test_failed_terminal_event_contract_rejects_before_phase40(
    tmp_path: Path, mode: str
) -> None:
    state, events = targets(tmp_path, "failed", 2)
    changes: dict[str, object] = {
        "category": {"failure_category": "transport_error"},
        "response": {"response_id": "response"},
        "output": {"output_text": "output"},
        "missing_message": {"message": None},
        "invalid_message": {"message": 1},
    }[mode]
    events.write_text(
        serialize_runtime_step_event_jsonl(terminal_event("succeeded", 1))
        + serialize_runtime_step_event_jsonl(
            replace(terminal_event("failed", 2), **changes)
        )
    )
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            failure(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: pytest.fail("Phase 40 must not run"),
        )
    assert caught.value.detail.classification == "terminal_contract"


def test_failure_state_category_mismatch_rejects_before_phase40(tmp_path: Path) -> None:
    state, events = targets(tmp_path, "failed", 2)
    mismatched = WorkflowExecutionState(
        "workflow", "failed", "second", 2, "two", ("first",), "transport_error"
    )
    state.write_text(serialize_workflow_execution_state_json(mismatched))
    with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
        route_prepared_step_start_bridge_reentry(
            failure(),
            workflow(),
            employee(),
            state,
            events,
            start_routing_function=lambda *_: pytest.fail("Phase 40 must not run"),
        )
    assert caught.value.detail.classification == "terminal_contract"


def test_target_is_file_oserror_uses_existing_terminal_contract(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    original = Path.is_file

    def broken(path: Path) -> bool:
        if path is state:
            raise OSError("/private/state")
        return original(path)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "is_file", broken)
        with pytest.raises(PreparedStepStartBridgeCompatibilityError) as caught:
            route_prepared_step_start_bridge_reentry(
                prepared(),
                workflow(),
                employee(),
                state,
                events,
                start_routing_function=lambda *_: pytest.fail("Phase 40 must not run"),
            )
    assert caught.value.detail.classification == "terminal_contract"
