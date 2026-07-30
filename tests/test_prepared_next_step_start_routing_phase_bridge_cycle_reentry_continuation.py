"""Focused Phase 82 strict-boundary tests."""

# ruff: noqa: E501

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedNextStepStartRoutingPhaseBridgeCycleContinuationError,
    PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_prepared_next_step_start_routing_phase_bridge_cycle_reentry_continuation,
)
from ai_office.engine.prepared_next_step_start_routing_phase_bridge_cycle_continuation import (
    route_prepared_next_step_start_routing_phase_bridge_cycle_continuation,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class PreparedSubclass(PreparedWorkflowStep):
    pass


class StartedSubclass(PreparedStepExecutionStart):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


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


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep("workflow", "second", 2, "two", "employee", "b", "model", ("tool",))


def started() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "employee", "b", ("tool",)),
        WorkflowExecutionState("workflow", "running", "second", 2, "two", ("first",), None),
    )


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded")


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "workflow", "first", 1, "one", "api_error")


def persisted_success() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_success", "workflow", "first", 1, "one", None)


def targets(tmp_path: Path, status: str = "succeeded", index: int = 1) -> tuple[Path, Path]:
    definition = workflow()
    step = definition.steps[index - 1]
    state = WorkflowExecutionState(
        "workflow", status, step.id, index, step.employee,
        tuple(item.id for item in definition.steps[:index]) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error",
    )
    events = []
    if index == 2:
        events.append(RuntimeStepEvent("step_succeeded", "workflow", "first", 1, "one", "running", "succeeded", "openai", None, "response", "request", "output", None))
    events.append(RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed", "workflow", step.id, index,
        step.employee, "running", status, "openai", None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None, "request", "output" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    ))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in events), encoding="utf-8")
    return state_path, events_path


def invoke(result: object, person: object | None, state: Path, events: Path, function=None):
    kwargs = {} if function is None else {"phase75_function": function}
    return route_prepared_next_step_start_routing_phase_bridge_cycle_reentry_continuation(result, workflow(), person, state, events, **kwargs)


def test_prepared_route_uses_canonical_five_argument_identity_and_returns_exact_object(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    supplied, definition, person, expected = prepared(), workflow(), employee(), started()
    received: list[object] = []

    def phase75(result_arg: object, workflow_arg: object, employee_arg: object, state_arg: object, events_arg: object) -> object:
        received.extend((result_arg, workflow_arg, employee_arg, state_arg, events_arg))
        return expected

    returned = route_prepared_next_step_start_routing_phase_bridge_cycle_reentry_continuation(supplied, definition, person, state, events, phase75_function=phase75)
    expected_args = [supplied, definition, person, state, events]
    assert returned is expected and received == expected_args
    assert all(actual is expected_arg for actual, expected_arg in zip(received, expected_args, strict=True))


def test_phase75_public_signature_is_canonical() -> None:
    parameters = tuple(inspect.signature(route_prepared_next_step_start_routing_phase_bridge_cycle_continuation).parameters)
    assert parameters[:5] == ("result", "workflow", "employee", "state_path", "events_path")


@pytest.mark.parametrize("result, status, index", [(completion(), "succeeded", 2), (failure(), "failed", 1)])
def test_stop_routes_return_same_object_and_do_not_call_phase75(tmp_path: Path, result: object, status: str, index: int) -> None:
    state, events = targets(tmp_path, status, index)
    calls = 0

    def phase75(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert invoke(result, None, state, events, phase75) is result and calls == 0


@pytest.mark.parametrize("result", [completion(), failure()])
def test_stop_routes_reject_employee(tmp_path: Path, result: object) -> None:
    state, events = targets(tmp_path, "failed" if type(result) is PersistedExecutionOutcome else "succeeded", 1 if type(result) is PersistedExecutionOutcome else 2)
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(result, employee(), state, events)
    assert error.value.detail.classification in {"completion_contract", "failure_contract"}


@pytest.mark.parametrize("value", [object(), SimpleNamespace(step_id="second"), DecisionSubclass("workflow_complete", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded"), OutcomeSubclass("persisted_failure", "workflow", "first", 1, "one", "api_error")])
def test_result_type_is_exact(tmp_path: Path, value: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(value, None, state, events)
    assert error.value.detail.classification == "result_type"


@pytest.mark.parametrize("field, value", [
    ("workflow_id", "wrong"), ("step_id", "wrong"), ("step_index", True), ("employee_id", "wrong"),
    ("employee_instructions", "wrong"), ("step_instructions", "wrong"), ("model", "wrong"), ("allowed_tool_names", ["tool"]),
])
def test_every_prepared_field_is_rejected_before_dependency(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase75(*_: object) -> object:
        nonlocal calls
        calls += 1
        return started()

    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(replace(prepared(), **{field: value}), employee(), state, events, phase75)
    expected = "employee_contract" if field == "employee_id" else "prepared_step_contract"
    assert error.value.detail.classification == expected and calls == 0


@pytest.mark.parametrize("bad_employee", [None, EmployeeSubclass.model_validate(employee().model_dump()), employee().model_copy(update={"id": "wrong"}), employee().model_copy(update={"id": 2})])
def test_prepared_route_requires_exact_matching_employee(tmp_path: Path, bad_employee: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), bad_employee, state, events)
    assert error.value.detail.classification == "employee_contract"


@pytest.mark.parametrize("field, value", [("name", 1), ("role", 1), ("instructions", 1), ("model", 1), ("allowed_tools", ("tool",))])
def test_employee_derived_fields_require_exact_types(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    bad_employee = employee().model_copy(update={field: value})
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), bad_employee, state, events)
    assert error.value.detail.classification == "employee_contract"


@pytest.mark.parametrize("field, value", [("model", "wrong"), ("system_instructions", "wrong"), ("task_instructions", "wrong"), ("allowed_tools", ("wrong",))])
def test_every_returned_request_field_is_rejected(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    bad = replace(started(), request=replace(started().request, **{field: value}))
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events, lambda *_: bad)
    assert error.value.detail.classification == "start_contract"


@pytest.mark.parametrize("field, value", [("workflow_id", "wrong"), ("status", "succeeded"), ("current_step_id", "wrong"), ("current_step_index", True), ("current_employee_id", "wrong"), ("completed_step_ids", ["first"]), ("last_failure_category", "api_error")])
def test_every_returned_running_state_field_is_rejected(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    bad = replace(started(), running_state=replace(started().running_state, **{field: value}))
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events, lambda *_: bad)
    assert error.value.detail.classification == "start_contract"


@pytest.mark.parametrize("bad", [
    PreparedSubclass("workflow", "second", 2, "two", "employee", "b", "model", ("tool",)),
    StartedSubclass(started().request, started().running_state),
    SimpleNamespace(request=started().request, running_state=started().running_state),
    object(),
])
def test_dependency_return_model_and_substitute_are_rejected(tmp_path: Path, bad: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events, lambda *_: bad)
    assert error.value.detail.classification == "start_contract"


@pytest.mark.parametrize("value", [object(), SimpleNamespace(decision="prepare_next_step")])
def test_unsupported_progression_is_rejected(tmp_path: Path, value: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(value, None, state, events)
    assert error.value.detail.classification == "result_type"


def test_exact_persisted_success_is_rejected_without_phase75_call(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase75(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase 75 must not run")

    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(persisted_success(), None, state, events, phase75)
    assert error.value.detail.classification == "failure_contract" and calls == 0


def test_terminal_predecessor_mismatch_is_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path, "succeeded", 2)
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events)
    assert error.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("which", ["state", "events"])
def test_target_is_file_and_read_bytes_oserror_are_classified_separately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str) -> None:
    state, events = targets(tmp_path)
    target = state if which == "state" else events
    original = Path.read_bytes
    monkeypatch.setattr(Path, "is_file", lambda self: (_ for _ in ()).throw(OSError()) if self == target else True)
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events)
    assert error.value.detail.classification == ("state_target" if which == "state" else "event_target")
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError()) if self == target else original(self))
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events)
    assert error.value.detail.classification == ("state_target" if which == "state" else "event_target")


def test_missing_and_non_regular_targets_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    events.unlink()
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events)
    assert error.value.detail.classification == "event_target"
    events.mkdir()
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events)
    assert error.value.detail.classification == "event_target"


@pytest.mark.parametrize("mutation", ["none", "state", "events", "both"])
def test_safe_error_identity_after_each_mutation_pattern(tmp_path: Path, mutation: str) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = PreparedNextStepStartRoutingPhaseBridgeCycleContinuationError("safe")

    def phase75(*_: object) -> object:
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        raise safe

    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleContinuationError) as error:
        invoke(prepared(), employee(), state, events, phase75)
    assert error.value is safe and (state.read_bytes(), events.read_bytes()) == before


def test_unexpected_error_is_sanitized_and_not_retried(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase75(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret")

    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events, phase75)
    assert calls == 1 and error.value.detail.classification == "dependency_error" and "secret" not in str(error.value)


def test_malformed_return_mutation_is_restored_without_retry(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def phase75(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        return object()

    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events, phase75)
    assert error.value.detail.classification == "start_contract" and calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failing", ["state", "events", "both"])
def test_rollback_failures_attempt_both_and_do_not_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    original_write = Path.write_bytes
    attempts: list[Path] = []

    def phase75(*_: object) -> object:
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return object()

    def write_bytes(path: Path, data: bytes) -> int:
        if data in before:
            attempts.append(path)
            if (path == state and failing in {"state", "both"}) or (path == events and failing in {"events", "both"}):
                raise OSError("rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events, phase75)
    assert error.value.detail.classification == "dependency_rollback" and attempts == [state, events]


def test_no_second_restore_after_dependency_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state, events = targets(tmp_path)
    original_write = Path.write_bytes
    attempts = 0

    def write_bytes(path: Path, data: bytes) -> int:
        nonlocal attempts
        if data.startswith(b"{"):
            attempts += 1
            raise OSError("rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    def phase75(*_: object) -> object:
        original_write(state, b"changed")
        return object()

    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        invoke(prepared(), employee(), state, events, phase75)
    assert error.value.detail.classification == "dependency_rollback" and attempts == 2


def test_target_conflict_and_noncallable_dependency_are_rejected(tmp_path: Path) -> None:
    state, _ = targets(tmp_path)
    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        route_prepared_next_step_start_routing_phase_bridge_cycle_reentry_continuation(prepared(), workflow(), employee(), state, state)
    assert error.value.detail.classification == "target_conflict"
