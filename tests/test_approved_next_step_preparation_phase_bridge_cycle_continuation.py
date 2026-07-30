"""Focused Phase 74 tests using injected Phase 67 fakes only."""

# ruff: noqa: E501

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ApprovedNextStepPreparationPhaseBridgeCycleContinuationCompatibilityError,
    ApprovedNextStepPreparationPhaseBridgeError,
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_approved_next_step_preparation_phase_bridge_cycle_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({
        "id": "workflow", "name": "Workflow", "description": "test",
        "steps": [
            {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
            {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
        ],
    })


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate({
        "id": "two", "name": "Two", "role": "role", "instructions": "employee",
        "model": "model", "allowed_tools": ["tool"],
    })


def decision() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("prepare_next_step", "workflow", "first", 1,
                                       "one", "second", 2, "two", "next_step_available")


def approval() -> NextStepPreparationApproval:
    return NextStepPreparationApproval(True, "workflow", "first", 1, "second", 2, "two")


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep("workflow", "second", 2, "two", "employee", "b", "model", ("tool",))


def targets(tmp_path: Path, status: str = "succeeded", index: int = 1) -> tuple[Path, Path]:
    definition = workflow()
    step = definition.steps[index - 1]
    state = WorkflowExecutionState("workflow", status, step.id, index, step.employee,
                                   tuple(item.id for item in definition.steps[:index])
                                   if status == "succeeded" else (),
                                   None if status == "succeeded" else "api_error")
    events = []
    for event_index in range(1, index):
        previous = definition.steps[event_index - 1]
        events.append(RuntimeStepEvent("step_succeeded", "workflow", previous.id, event_index,
                                       previous.employee, "running", "succeeded", "openai", None,
                                       "response", "request", "output", None))
    events.append(RuntimeStepEvent("step_succeeded" if status == "succeeded" else "step_failed",
                                   "workflow", step.id, index, step.employee, "running", status,
                                   "openai", None if status == "succeeded" else "api_error",
                                   "response" if status == "succeeded" else None, "request",
                                   "output" if status == "succeeded" else None,
                                   None if status == "succeeded" else "safe"))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.json"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in events), encoding="utf-8")
    return state_path, events_path


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "workflow", "second", 2,
                                       "two", None, None, None, "last_step_succeeded")


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "workflow", "first", 1, "one", "api_error")


def test_prepare_delegates_exact_phase67_arguments_once_and_returns_same_object(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    result, definition, approved, person, value = decision(), workflow(), approval(), employee(), prepared()
    calls: list[tuple[object, ...]] = []

    def phase67(*args: object) -> PreparedWorkflowStep:
        calls.append(args)
        assert args == (result, definition, state, events, approved, person)
        return value

    returned = route_approved_next_step_preparation_phase_bridge_cycle_continuation(
        result, definition, approved, person, state, events, phase67_function=phase67
    )
    assert returned is value
    assert len(calls) == 1


def test_stop_routes_are_unchanged_and_zero_call(tmp_path: Path) -> None:
    calls = 0

    def phase67(*args: object):
        nonlocal calls
        calls += 1
        raise AssertionError("must not be called")

    state, events = targets(tmp_path, index=2)
    value = completion()
    returned = route_approved_next_step_preparation_phase_bridge_cycle_continuation(
        value, workflow(), None, None, state, events, phase67_function=phase67
    )
    assert returned is value

    state, events = targets(tmp_path, "failed")
    value = failure()
    returned = route_approved_next_step_preparation_phase_bridge_cycle_continuation(
        value, workflow(), None, None, state, events, phase67_function=phase67
    )
    assert returned is value and calls == 0


class StringSubclass(str):
    pass


class IntegerSubclass(int):
    pass


class ApprovalSubclass(NextStepPreparationApproval):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class PreparedSubclass(PreparedWorkflowStep):
    pass


class PathSubclass(type(Path())):
    pass


def invoke(result, definition, approved, person, state, events, phase67_function=None):
    kwargs = {} if phase67_function is None else {"phase67_function": phase67_function}
    return route_approved_next_step_preparation_phase_bridge_cycle_continuation(
        result, definition, approved, person, state, events, **kwargs
    )


def assert_classification(call, classification: str) -> None:
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCycleContinuationCompatibilityError
    ) as error:
        call()
    assert error.value.detail.classification == classification


@pytest.mark.parametrize(
    "bad_result,bad_workflow,bad_approval,bad_employee,bad_state,bad_events,bad_dependency,classification",
    [
        (object(), workflow(), approval(), employee(), None, None, None, "result_type"),
        (decision(), WorkflowSubclass.model_validate(workflow().model_dump()), approval(), employee(), None, None, None, "workflow_definition"),
        (decision(), workflow(), ApprovalSubclass(True, "workflow", "first", 1, "second", 2, "two"), employee(), None, None, None, "approval_contract"),
        (decision(), workflow(), approval(), EmployeeSubclass.model_validate(employee().model_dump()), None, None, None, "employee_contract"),
        (decision(), workflow(), approval(), employee(), PathSubclass("state"), None, None, "state_target"),
        (decision(), workflow(), approval(), employee(), None, PathSubclass("events"), None, "event_target"),
        (decision(), workflow(), approval(), employee(), None, None, object(), "preparation_contract"),
    ],
)
def test_all_supplied_models_and_dependency_are_exact(
    tmp_path: Path, bad_result, bad_workflow, bad_approval, bad_employee,
    bad_state, bad_events, bad_dependency, classification: str,
) -> None:
    state, events = targets(tmp_path)
    result = bad_result
    definition = bad_workflow
    approved = bad_approval
    person = bad_employee
    state_value = state if bad_state is None else bad_state
    events_value = events if bad_events is None else bad_events
    dependency = (lambda *args: prepared()) if bad_dependency is None else bad_dependency
    assert_classification(
        lambda: invoke(result, definition, approved, person, state_value, events_value, dependency),
        classification,
    )


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("decision", "workflow_complete"),
        ("workflow_id", "wrong"),
        ("current_step_id", StringSubclass("first")),
        ("current_step_index", True),
        ("current_step_index", IntegerSubclass(1)),
        ("current_employee_id", "wrong"),
        ("next_step_id", None),
        ("next_step_index", 1.0),
        ("next_step_index", IntegerSubclass(2)),
        ("next_employee_id", StringSubclass("two")),
        ("reason", "wrong"),
    ],
)
def test_prepare_decision_every_field_is_exact(tmp_path: Path, field: str, bad_value) -> None:
    state, events = targets(tmp_path)
    assert_classification(
        lambda: invoke(replace(decision(), **{field: bad_value}), workflow(), approval(), employee(), state, events),
        "completion_contract" if field == "decision" else "decision_contract",
    )


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("approved", False), ("approved", 1),
        ("workflow_id", "wrong"), ("workflow_id", StringSubclass("workflow")),
        ("current_step_id", "wrong"), ("current_step_id", StringSubclass("first")),
        ("current_step_index", True), ("current_step_index", IntegerSubclass(1)),
        ("next_step_id", "wrong"), ("next_step_id", StringSubclass("second")),
        ("next_step_index", 1), ("next_step_index", IntegerSubclass(2)),
        ("next_employee_id", "wrong"), ("next_employee_id", StringSubclass("two")),
    ],
)
def test_approval_every_field_and_unapproved_value_are_exact(
    tmp_path: Path, field: str, bad_value,
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase67(*args):
        nonlocal calls
        calls += 1
        return prepared()

    assert_classification(
        lambda: invoke(decision(), workflow(), replace(approval(), **{field: bad_value}), employee(), state, events, phase67),
        "approval_contract",
    )
    assert calls == 0


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("id", "wrong"), ("id", 2), ("id", StringSubclass("two")),
        ("name", 2), ("name", StringSubclass("Two")),
        ("role", 2), ("instructions", 2), ("model", 2),
        ("allowed_tools", ("tool",)), ("allowed_tools", [StringSubclass("tool")]),
    ],
)
def test_next_employee_all_fields_and_id_are_exact(tmp_path: Path, field: str, bad_value) -> None:
    state, events = targets(tmp_path)
    bad_employee = employee().model_copy(update={field: bad_value})
    assert_classification(
        lambda: invoke(decision(), workflow(), approval(), bad_employee, state, events),
        "employee_contract",
    )


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("workflow_id", "wrong"), ("current_step_id", "wrong"),
        ("current_step_index", 2), ("current_employee_id", "wrong"),
        ("completed_step_ids", ()), ("completed_step_ids", ("first", "first")),
        ("completed_step_ids", ("second",)), ("status", "failed"),
    ],
)
def test_succeeded_terminal_state_fields_are_strict(
    tmp_path: Path, field: str, bad_value,
) -> None:
    state, events = targets(tmp_path)
    payload = json.loads(state.read_text())
    payload[field] = bad_value
    state.write_text(json.dumps(payload), encoding="utf-8")
    assert_classification(
        lambda: invoke(decision(), workflow(), approval(), employee(), state, events),
        "terminal_contract",
    )


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("event_type", "step_failed"), ("workflow_id", "wrong"),
        ("step_id", "wrong"), ("step_index", 2), ("employee_id", "wrong"),
        ("previous_status", "ready"), ("next_status", "failed"),
        ("provider", 2), ("failure_category", "api_error"),
        ("response_id", 2), ("request_id", 2), ("output_text", 2), ("message", 2),
    ],
)
def test_succeeded_terminal_event_fields_are_strict(
    tmp_path: Path, field: str, bad_value,
) -> None:
    state, events = targets(tmp_path)
    lines = events.read_text().splitlines()
    payload = json.loads(lines[-1])
    payload[field] = bad_value
    events.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert_classification(
        lambda: invoke(decision(), workflow(), approval(), employee(), state, events),
        "terminal_contract",
    )


@pytest.mark.parametrize("malformed", ["", "{}\n", '{"event_type":"step_succeeded","event_type":"step_failed"}\n'])
def test_terminal_history_missing_duplicate_and_order_records_are_rejected(
    tmp_path: Path, malformed: str,
) -> None:
    state, events = targets(tmp_path)
    if malformed == "{}\n":
        events.write_text(malformed, encoding="utf-8")
    else:
        events.write_text(malformed, encoding="utf-8")
    assert_classification(
        lambda: invoke(decision(), workflow(), approval(), employee(), state, events),
        "terminal_contract",
    )


def test_event_order_and_duplicate_records_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path, index=2)
    lines = events.read_text().splitlines(keepends=True)
    events.write_text(lines[1] + lines[0] + lines[1], encoding="utf-8")
    assert_classification(
        lambda: invoke(completion(), workflow(), None, None, state, events),
        "terminal_contract",
    )


@pytest.mark.parametrize("value", [replace(completion(), current_step_id="wrong"), replace(completion(), reason="wrong"),
                                    replace(failure(), current_step_id="wrong"), replace(failure(), failure_category="transport_error")])
def test_stop_route_all_fields_and_terminal_mismatch_are_rejected(tmp_path: Path, value) -> None:
    is_failure = type(value) is PersistedExecutionOutcome
    state, events = targets(tmp_path, "failed" if is_failure else "succeeded", 1 if is_failure else 2)
    assert_classification(
        lambda: invoke(value, workflow(), None, None, state, events),
        "terminal_contract" if is_failure and value.failure_category == "transport_error" else ("failure_contract" if is_failure else "completion_contract"),
    )


@pytest.mark.parametrize("value", [PersistedExecutionOutcome("persisted_success", "workflow", "first", 1, "one", None),
                                    replace(decision(), decision="stopped_failed"),
                                    replace(decision(), decision="not_progressable")])
def test_direct_success_and_unsupported_progression_decisions_are_rejected(tmp_path: Path, value) -> None:
    state, events = targets(tmp_path)
    assert_classification(
        lambda: invoke(value, workflow(), approval(), employee(), state, events),
        "failure_contract" if type(value) is PersistedExecutionOutcome else "completion_contract",
    )


@pytest.mark.parametrize("which", ["missing_state", "missing_events", "directory_state", "directory_events", "conflict"])
def test_target_existence_regular_file_and_conflict_are_strict(tmp_path: Path, which: str) -> None:
    state, events = targets(tmp_path)
    if which == "missing_state":
        state.unlink()
    elif which == "missing_events":
        events.unlink()
    elif which == "directory_state":
        state.unlink()
        state.mkdir()
    elif which == "directory_events":
        events.unlink()
        events.mkdir()
    elif which == "conflict":
        events = state
    assert_classification(
        lambda: invoke(decision(), workflow(), approval(), employee(), state, events),
        "target_conflict" if which == "conflict" else ("state_target" if "state" in which else "event_target"),
    )


@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
@pytest.mark.parametrize("which", ["state", "events"])
def test_each_target_oserror_is_classified_separately(
    tmp_path: Path, monkeypatch, operation: str, which: str,
) -> None:
    state, events = targets(tmp_path)
    target = state if which == "state" else events
    original = getattr(Path, operation)

    def fail(path: Path, *args):
        if path == target:
            raise OSError("target")
        return original(path, *args)

    monkeypatch.setattr(Path, operation, fail)
    assert_classification(
        lambda: invoke(decision(), workflow(), approval(), employee(), state, events),
        "state_target" if which == "state" else "event_target",
    )


@pytest.mark.parametrize("value", [completion(), failure()])
@pytest.mark.parametrize("context", [(approval(), None), (None, employee()), (approval(), employee())])
def test_stop_routes_reject_any_non_none_approval_or_employee(
    tmp_path: Path, value, context,
) -> None:
    approved, person = context
    is_failure = type(value) is PersistedExecutionOutcome
    state, events = targets(tmp_path, "failed" if is_failure else "succeeded", 1 if is_failure else 2)
    assert_classification(
        lambda: invoke(value, workflow(), approved, person, state, events),
        "failure_contract" if is_failure else "completion_contract",
    )


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_dependency_mutation_is_compensated_for_each_target(tmp_path: Path, mutation: str) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def phase67(*args):
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        return object()

    assert_classification(
        lambda: invoke(decision(), workflow(), approval(), employee(), state, events, phase67),
        "preparation_contract",
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["safe_unchanged", "safe_mutated", "unexpected"])
def test_dependency_error_identity_sanitization_and_no_retry(tmp_path: Path, kind: str) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def phase67(*args):
        nonlocal calls
        calls += 1
        if kind == "safe_mutated":
            state.write_bytes(b"changed")
        if kind == "unexpected":
            raise RuntimeError("secret detail")
        if kind.startswith("safe"):
            raise ApprovedNextStepPreparationPhaseBridgeError("safe")

    expected = ApprovedNextStepPreparationPhaseBridgeError if kind.startswith("safe") else ApprovedNextStepPreparationPhaseBridgeCycleContinuationCompatibilityError
    with pytest.raises(expected) as error:
        invoke(decision(), workflow(), approval(), employee(), state, events, phase67)
    if kind == "unexpected":
        assert error.value.detail.classification == "dependency_error"
        assert "secret" not in str(error.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("field", ["workflow_id", "step_id", "step_index", "employee_id",
                                    "employee_instructions", "step_instructions", "model", "allowed_tool_names"])
def test_prepared_return_exact_model_and_all_fields(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    bad_values = {
        "workflow_id": "wrong", "step_id": "wrong", "step_index": 1,
        "employee_id": "wrong", "employee_instructions": 2, "step_instructions": 2,
        "model": 2, "allowed_tool_names": ["tool"],
    }
    bad = replace(prepared(), **{field: bad_values[field]})
    assert_classification(
        lambda: invoke(decision(), workflow(), approval(), employee(), state, events, lambda *args: bad),
        "preparation_contract",
    )


@pytest.mark.parametrize("value", [
    PreparedSubclass("workflow", "second", 2, "two", "employee", "b", "model", ("tool",)),
    SimpleNamespace(**prepared().__dict__),
])
def test_prepared_return_subclass_and_substitute_are_rejected(tmp_path: Path, value) -> None:
    state, events = targets(tmp_path)
    assert_classification(
        lambda: invoke(decision(), workflow(), approval(), employee(), state, events, lambda *args: value),
        "preparation_contract",
    )


@pytest.mark.parametrize("failing", ["state", "events", "both"])
def test_rollback_failure_attempts_both_once_and_classifies_dependency_rollback(
    tmp_path: Path, monkeypatch, failing: str,
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    attempts: list[Path] = []
    original_write = Path.write_bytes

    def phase67(*args):
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        return object()

    def write_bytes(path: Path, contents: bytes) -> int:
        if contents in before and (path == state or path == events):
            attempts.append(path)
            if (path == state and failing in {"state", "both"}) or (path == events and failing in {"events", "both"}):
                raise OSError("rollback")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    assert_classification(
        lambda: invoke(decision(), workflow(), approval(), employee(), state, events, phase67),
        "dependency_rollback",
    )
    assert attempts == [state, events]
