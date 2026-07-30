"""Focused Phase 81 strict-boundary tests."""

# ruff: noqa: E501

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError,
    ApprovedNextStepPreparationPhaseBridgeError,
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_approved_next_step_preparation_phase_bridge_cycle_reentry_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class ApprovalSubclass(NextStepPreparationApproval):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


class PreparedSubclass(PreparedWorkflowStep):
    pass


def definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({
        "id": "workflow", "name": "Workflow", "description": "test",
        "steps": [
            {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
            {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
        ],
    })


def person() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate({"id": "two", "name": "Two", "role": "role", "instructions": "employee", "model": "model", "allowed_tools": ["tool"]})


def progress() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("prepare_next_step", "workflow", "first", 1, "one", "second", 2, "two", "next_step_available")


def approval() -> NextStepPreparationApproval:
    return NextStepPreparationApproval(True, "workflow", "first", 1, "second", 2, "two")


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep("workflow", "second", 2, "two", "employee", "b", "model", ("tool",))


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "workflow", "first", 1, "one", "api_error")


def complete() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded")


def targets(tmp_path: Path, status: str = "succeeded", index: int = 1) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    workflow = definition()
    step = workflow.steps[index - 1]
    state_model = WorkflowExecutionState("workflow", status, step.id, index, step.employee, tuple(item.id for item in workflow.steps[:index]) if status == "succeeded" else (), None if status == "succeeded" else "api_error")
    events = []
    for event_index in range(1, index):
        previous = workflow.steps[event_index - 1]
        events.append(RuntimeStepEvent("step_succeeded", "workflow", previous.id, event_index, previous.employee, "running", "succeeded", "openai", None, "response", "request", "output", None))
    events.append(RuntimeStepEvent("step_succeeded" if status == "succeeded" else "step_failed", "workflow", step.id, index, step.employee, "running", status, "openai", None if status == "succeeded" else "api_error", "response" if status == "succeeded" else None, "request", "output" if status == "succeeded" else None, None if status == "succeeded" else "safe"))
    state = tmp_path / "state.json"
    event_path = tmp_path / "events.jsonl"
    state.write_bytes(serialize_workflow_execution_state_json(state_model).encode())
    event_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in events))
    return state, event_path


def invoke(result: object, workflow: object, approved: object, employee: object, state: Path, events: Path, function=None):
    kwargs = {} if function is None else {"phase74_function": function}
    return route_approved_next_step_preparation_phase_bridge_cycle_reentry_continuation(result, workflow, approved, employee, state, events, **kwargs)


def rewrite_json(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n")


def test_prepare_delegates_all_six_dependency_arguments_by_identity_and_returns_same_object(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    result, workflow, approved, employee = progress(), definition(), approval(), person()
    expected = prepared()
    received: list[object] = []

    def phase74(result_arg: object, workflow_arg: object, state_arg: object, events_arg: object, approval_arg: object, employee_arg: object) -> object:
        received.extend((result_arg, workflow_arg, state_arg, events_arg, approval_arg, employee_arg))
        return expected

    returned = invoke(result, workflow, approved, employee, state, events, phase74)
    assert returned is expected
    assert received == [result, workflow, state, events, approved, employee]
    assert all(a is b for a, b in zip(received, [result, workflow, state, events, approved, employee], strict=True))


def test_stop_routes_require_absent_context_and_preserve_identity(tmp_path: Path) -> None:
    state, events = targets(tmp_path, index=2)
    calls = 0

    def phase74(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    value = complete()
    assert invoke(value, definition(), None, None, state, events, phase74) is value
    state, events = targets(tmp_path / "failure", "failed")
    value = failure()
    assert invoke(value, definition(), None, None, state, events, phase74) is value
    assert calls == 0


@pytest.mark.parametrize("value", [PreparedSubclass("workflow", "second", 2, "two", "employee", "b", "model", ("tool",)), SimpleNamespace(workflow_id="workflow")])
def test_prepared_return_subclass_and_substitute_are_rejected(tmp_path: Path, value: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(progress(), definition(), approval(), person(), state, events, lambda *_: value)
    assert caught.value.detail.classification == "preparation_contract"


@pytest.mark.parametrize("field", ["workflow_id", "step_id", "step_index", "employee_id", "employee_instructions", "step_instructions", "model", "allowed_tool_names"])
def test_prepared_return_each_field_is_strictly_validated(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    values = {"workflow_id": "wrong", "step_id": "wrong", "step_index": True, "employee_id": "wrong", "employee_instructions": 2, "step_instructions": 2, "model": 2, "allowed_tool_names": ["tool"]}
    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(progress(), definition(), approval(), person(), state, events, lambda *_: replace(prepared(), **{field: values[field]}))
    assert caught.value.detail.classification == "preparation_contract"


@pytest.mark.parametrize("field", ["decision", "workflow_id", "current_step_id", "current_step_index", "current_employee_id", "next_step_id", "next_step_index", "next_employee_id", "reason"])
def test_prepare_decision_each_field_is_strictly_validated(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    values = {"decision": "workflow_complete", "workflow_id": "wrong", "current_step_id": "wrong", "current_step_index": True, "current_employee_id": "wrong", "next_step_id": "wrong", "next_step_index": 1, "next_employee_id": "wrong", "reason": "wrong"}
    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(replace(progress(), **{field: values[field]}), definition(), approval(), person(), state, events)
    assert caught.value.detail.classification in {"decision_contract", "completion_contract", "approval_contract", "employee_contract"}


@pytest.mark.parametrize("bad", [object(), DecisionSubclass("prepare_next_step", "workflow", "first", 1, "one", "second", 2, "two", "next_step_available"), PersistedExecutionOutcome("persisted_success", "workflow", "first", 1, "one", None)])
def test_unsupported_result_types_and_direct_success_are_rejected(tmp_path: Path, bad: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(bad, definition(), approval(), person(), state, events)


@pytest.mark.parametrize("bad", [ApprovalSubclass(True, "workflow", "first", 1, "second", 2, "two"), SimpleNamespace(approved=True)])
def test_approval_subclass_and_substitute_are_rejected(tmp_path: Path, bad: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(progress(), definition(), bad, person(), state, events)
    assert caught.value.detail.classification == "approval_contract"


@pytest.mark.parametrize("bad", [EmployeeSubclass.model_validate(person().model_dump()), SimpleNamespace(id="two")])
def test_employee_subclass_and_substitute_are_rejected(tmp_path: Path, bad: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(progress(), definition(), approval(), bad, state, events)
    assert caught.value.detail.classification == "employee_contract"


@pytest.mark.parametrize("context", [(approval(), None), (None, person()), (approval(), person())])
@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_reject_extra_approval_or_employee(tmp_path: Path, context: tuple[object, object], kind: str) -> None:
    approved, employee = context
    status, index = ("succeeded", 2) if kind == "complete" else ("failed", 1)
    state, events = targets(tmp_path, status, index)
    value = complete() if kind == "complete" else failure()
    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(value, definition(), approved, employee, state, events)


@pytest.mark.parametrize("which", ["missing_state", "missing_events", "directory_state", "directory_events", "conflict"])
def test_targets_are_regular_distinct_files_and_oserrors_are_classified(tmp_path: Path, which: str) -> None:
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
    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(progress(), definition(), approval(), person(), state, events)


@pytest.mark.parametrize("which", ["state", "events"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_target_oserrors_are_independently_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, operation: str) -> None:
    state, events = targets(tmp_path)
    target = state if which == "state" else events
    original = getattr(Path, operation)

    def fail(path: Path, *args: object):
        if path == target:
            raise OSError("target")
        return original(path, *args)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(progress(), definition(), approval(), person(), state, events)
    assert caught.value.detail.classification == ("state_target" if which == "state" else "event_target")


def test_terminal_mismatch_is_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    rewrite_json(state, "workflow_id", "other")
    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(progress(), definition(), approval(), person(), state, events)
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("mutation", ["none", "state", "events", "both"])
def test_safe_error_identity_and_compensation_for_each_mutation(tmp_path: Path, mutation: str) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = ApprovedNextStepPreparationPhaseBridgeError("safe")

    def phase74(*_: object) -> object:
        if mutation in ("state", "both"):
            state.write_bytes(b"changed-state")
        if mutation in ("events", "both"):
            events.write_bytes(b"changed-events")
        raise safe

    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeError) as caught:
        invoke(progress(), definition(), approval(), person(), state, events, phase74)
    assert caught.value is safe and (state.read_bytes(), events.read_bytes()) == before


def test_unexpected_error_is_sanitized_and_no_retry(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase74(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret")

    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(progress(), definition(), approval(), person(), state, events, phase74)
    assert calls == 1 and caught.value.detail.classification == "dependency_error" and "secret" not in str(caught.value)


@pytest.mark.parametrize("failure_target", ["state", "events", "both"])
def test_rollback_failures_try_both_targets_once_and_classify_dependency_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_target: str) -> None:
    state, events = targets(tmp_path)
    original_write = Path.write_bytes
    attempts: list[Path] = []

    def write(path: Path, contents: bytes) -> int:
        if contents.startswith(b"{"):
            attempts.append(path)
            if (path == state and failure_target in ("state", "both")) or (path == events and failure_target in ("events", "both")):
                raise OSError("rollback")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", write)
    def phase74(*_: object) -> object:
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return object()

    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(progress(), definition(), approval(), person(), state, events, phase74)
    assert caught.value.detail.classification == "dependency_rollback" and attempts == [state, events]


def test_malformed_return_is_compensated_once_and_not_retried(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def phase74(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed")
        return object()

    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(progress(), definition(), approval(), person(), state, events, phase74)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before
