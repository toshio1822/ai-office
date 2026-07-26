"""Tests for Phase 42 with an injected Phase 35 fake."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedRunningExecutionReentryCompatibilityError,
    PersistedRunningExecutionRoutingCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_reentry,
)
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "first", "name": "F", "employee": "one", "instructions": "a"},
                {
                    "id": "step",
                    "name": "S",
                    "employee": "employee",
                    "instructions": "task",
                },
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "employee",
            "name": "E",
            "role": "R",
            "instructions": "system",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def start() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "system", "task", ("tool",)),
        WorkflowExecutionState(
            "workflow", "running", "step", 2, "employee", ("first",), None
        ),
    )


def resolved_tools() -> tuple[ToolDefinition, ...]:
    return (ToolDefinition("tool", "Tool", ()),)


def key() -> OpenAIApiKey:
    return OpenAIApiKey(value=SecretStr("synthetic"))


def approval():
    return approve_model_invocation_execution(
        start().request,
        resolved_tools(),
        provider="openai",
        approved_by="test",
        approval_id="id",
    )


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "workflow",
        "step",
        2,
        "employee",
        ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None),
    )


def success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "workflow",
        "step",
        2,
        "employee",
        ModelInvocationSuccess("openai", "response", None, "completed", ("ok",), "ok"),
    )


class RuntimeSuccessSubclass(StepRuntimeExecutionSuccess):
    pass


class RuntimeFailureSubclass(StepRuntimeExecutionFailure):
    pass


class PersistenceSubclass(RunningStatePersistenceResult):
    pass


class StartSubclass(PreparedStepExecutionStart):
    pass


class RequestSubclass(ModelInvocationRequest):
    pass


class StateSubclass(WorkflowExecutionState):
    pass


class ToolSubclass(ToolDefinition):
    pass


class ApiKeySubclass(OpenAIApiKey):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


def setup(tmp_path: Path) -> tuple[Path, Path, bytes]:
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    before = serialize_workflow_execution_state_json(start().running_state).encode()
    state.write_bytes(before)
    events.write_bytes(b"")
    return state, events, before


def call(state: Path, events: Path, phase35: object, *, result: object | None = None):
    return route_persisted_running_execution_reentry(
        RunningStatePersistenceResult(len(state.read_bytes()))
        if result is None
        else result,
        start(),
        workflow(),
        employee(),
        state,
        events,
        resolved_tools(),
        key(),
        approval(),
        lambda _: None,
        execution_reentry_function=phase35,  # type: ignore[arg-type]
    )


def test_routes_once_returns_same_result_and_keeps_targets(tmp_path: Path) -> None:
    state, events, before = setup(tmp_path)
    calls = 0
    expected = failure()

    def phase35(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        assert args == (
            start(),
            state,
            workflow(),
            employee(),
            resolved_tools(),
            key(),
            approval(),
        )
        assert set(kwargs) == {"transport"} and callable(kwargs["transport"])
        return expected

    assert call(state, events, phase35) is expected
    assert calls == 1
    assert state.read_bytes() == before and events.read_bytes() == b""


@pytest.mark.parametrize("expected", [success(), failure()])
def test_exact_runtime_results_are_returned_without_reconstruction(
    tmp_path: Path, expected: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
) -> None:
    state, events, _ = setup(tmp_path)
    calls = 0

    def phase36(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    assert call(state, events, phase36) is expected
    assert calls == 1


@pytest.mark.parametrize(
    "returned",
    [
        RuntimeSuccessSubclass(*success().__dict__.values()),
        RuntimeFailureSubclass(*failure().__dict__.values()),
        object(),
        type("Compatible", (), failure().__dict__)(),
    ],
)
def test_phase36_return_must_be_an_exact_valid_runtime_result(
    tmp_path: Path, returned: object
) -> None:
    state, events, _ = setup(tmp_path)
    calls = 0

    def phase36(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError) as caught:
        call(state, events, phase36)
    assert caught.value.detail.classification == "execution_contract"
    assert calls == 1


def test_invalid_runtime_identity_is_rejected_after_one_call(tmp_path: Path) -> None:
    state, events, _ = setup(tmp_path)
    invalid = StepRuntimeExecutionFailure(
        "other", "step", 2, "employee", failure().invocation_result
    )

    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError) as caught:
        call(state, events, lambda *_args, **_kwargs: invalid)
    assert caught.value.detail.classification == "execution_contract"


def test_completion_stops_without_execution_and_returns_same_decision(
    tmp_path: Path,
) -> None:
    state, events, before = setup(tmp_path)
    decision = WorkflowProgressionDecision(
        "workflow_complete",
        "workflow",
        "step",
        2,
        "employee",
        None,
        None,
        None,
        "last_step_succeeded",
    )
    calls = 0

    def phase35(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        return failure()

    value = route_persisted_running_execution_reentry(
        decision,
        None,
        workflow(),
        None,
        state,
        events,
        None,
        None,
        None,
        None,
        execution_reentry_function=phase35,
    )
    assert value is decision and calls == 0
    assert state.read_bytes() == before and events.read_bytes() == b""


@pytest.mark.parametrize(
    "result",
    [
        object(),
        PersistenceSubclass(1),
        RunningStatePersistenceResult(True),
        RunningStatePersistenceResult(0),
        RunningStatePersistenceResult(-1),
        RunningStatePersistenceResult("x"),
        RunningStatePersistenceResult(999),
    ],
)
def test_invalid_persistence_contract_does_not_execute(
    tmp_path: Path, result: object
) -> None:
    state, events, _ = setup(tmp_path)
    calls = 0

    def phase35(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        return failure()

    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError) as caught:
        call(state, events, phase35, result=result)
    assert caught.value.detail.classification in {"result_type", "persistence_contract"}
    assert calls == 0


def test_result_type_wins_before_missing_targets_or_route_arguments(
    tmp_path: Path,
) -> None:
    state, events, _ = setup(tmp_path)
    state.unlink()
    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError) as caught:
        route_persisted_running_execution_reentry(
            object(),
            object(),
            object(),
            object(),
            state,
            events,
            object(),
            object(),
            object(),
            object(),
            execution_reentry_function=object(),  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "result_type"


@pytest.mark.parametrize(
    "field,value",
    [
        ("start", object()),
        ("employee", object()),
        ("tools", object()),
        ("key", object()),
        ("approval", object()),
        ("transport", None),
    ],
)
def test_execution_argument_prevalidation_precedes_missing_targets(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, _ = setup(tmp_path)
    state.unlink()
    args: dict[str, object] = {
        "start": start(),
        "employee": employee(),
        "tools": resolved_tools(),
        "key": key(),
        "approval": approval(),
        "transport": lambda _: None,
    }
    args[field] = value
    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError) as caught:
        route_persisted_running_execution_reentry(
            RunningStatePersistenceResult(1),
            args["start"],
            workflow(),
            args["employee"],
            state,
            events,
            args["tools"],
            args["key"],
            args["approval"],
            args["transport"],
            execution_reentry_function=lambda *_args, **_kwargs: failure(),  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification in {
        "start_contract",
        "employee_contract",
        "execution_contract",
    }


@pytest.mark.parametrize("mode", ["state", "event", "delete"])
def test_changed_targets_are_restored_and_rejected(tmp_path: Path, mode: str) -> None:
    state, events, before = setup(tmp_path)

    def phase35(*_: object, **__: object) -> object:
        if mode == "state":
            state.write_bytes(b"changed")
        elif mode == "event":
            events.write_bytes(b"changed")
        else:
            state.unlink()
            events.unlink()
        return failure()

    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError) as caught:
        call(state, events, phase35)
    assert caught.value.detail.classification == "execution_contract"
    assert state.read_bytes() == before and events.read_bytes() == b""


def test_safe_phase35_error_is_reraised_after_compensation(tmp_path: Path) -> None:
    state, events, before = setup(tmp_path)
    error = PersistedRunningExecutionReentryCompatibilityError("state_data")

    def phase35(*_: object, **__: object) -> object:
        state.write_bytes(b"changed")
        raise error

    with pytest.raises(PersistedRunningExecutionReentryCompatibilityError) as caught:
        call(state, events, phase35)
    assert caught.value is error and state.read_bytes() == before


def test_unexpected_error_is_safe_and_does_not_disclose_secret(tmp_path: Path) -> None:
    state, events, _ = setup(tmp_path)

    def phase35(*_: object, **__: object) -> object:
        raise RuntimeError("credential=secret")

    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError) as caught:
        call(state, events, phase35)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "start_value,employee_value,tools_value,key_value,transport_value",
    [
        (None, employee(), resolved_tools(), key(), lambda _: None),
        (
            StartSubclass(start().request, start().running_state),
            employee(),
            resolved_tools(),
            key(),
            lambda _: None,
        ),
        (
            replace(
                start(), request=RequestSubclass(*start().request.__dict__.values())
            ),
            employee(),
            resolved_tools(),
            key(),
            lambda _: None,
        ),
        (
            replace(
                start(),
                running_state=StateSubclass(*start().running_state.__dict__.values()),
            ),
            employee(),
            resolved_tools(),
            key(),
            lambda _: None,
        ),
        (start(), None, resolved_tools(), key(), lambda _: None),
        (
            start(),
            employee(),
            (ToolSubclass(*resolved_tools()[0].__dict__.values()),),
            key(),
            lambda _: None,
        ),
        (
            start(),
            employee(),
            resolved_tools(),
            ApiKeySubclass(value=SecretStr("synthetic")),
            lambda _: None,
        ),
        (start(), employee(), resolved_tools(), key(), None),
    ],
)
def test_exact_execution_input_types_reject_without_dependency_call(
    tmp_path: Path,
    start_value: object,
    employee_value: object,
    tools_value: object,
    key_value: object,
    transport_value: object,
) -> None:
    state, events, _ = setup(tmp_path)
    calls = 0

    def phase36(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        return failure()

    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError):
        route_persisted_running_execution_reentry(
            RunningStatePersistenceResult(len(state.read_bytes())),
            start_value,
            workflow(),
            employee_value,
            state,
            events,
            tools_value,
            key_value,
            approval(),
            transport_value,
            execution_reentry_function=phase36,  # type: ignore[arg-type]
        )
    assert calls == 0


@pytest.mark.parametrize(
    "running",
    [
        replace(start().running_state, status="ready"),
        replace(start().running_state, last_failure_category="api_error"),
        replace(start().running_state, workflow_id="other"),
        replace(start().running_state, current_step_id="first"),
        replace(start().running_state, current_step_index=True),
        replace(start().running_state, current_step_index=0),
        replace(start().running_state, current_step_index=3),
        replace(start().running_state, current_employee_id="other"),
        replace(start().running_state, completed_step_ids=()),
        replace(start().running_state, completed_step_ids=("unknown",)),
        replace(start().running_state, completed_step_ids=("step",)),
    ],
)
def test_invalid_running_contract_does_not_call_dependency(
    tmp_path: Path, running: WorkflowExecutionState
) -> None:
    state, events, _ = setup(tmp_path)
    supplied = replace(start(), running_state=running)
    state.write_bytes(serialize_workflow_execution_state_json(running).encode())
    calls = 0

    def phase36(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        return failure()

    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError):
        route_persisted_running_execution_reentry(
            RunningStatePersistenceResult(len(state.read_bytes())),
            supplied,
            workflow(),
            employee(),
            state,
            events,
            resolved_tools(),
            key(),
            approval(),
            lambda _: None,
            execution_reentry_function=phase36,
        )
    assert calls == 0


@pytest.mark.parametrize(
    "state_mode,event_mode",
    [
        ("replace", "same"),
        ("delete", "same"),
        ("truncate", "same"),
        ("append", "same"),
        ("same", "replace"),
        ("same", "delete"),
        ("same", "truncate"),
        ("same", "append"),
        ("delete", "append"),
    ],
)
def test_all_target_mutations_are_compensated_once(
    tmp_path: Path, state_mode: str, event_mode: str
) -> None:
    state, events, before = setup(tmp_path)
    events.write_bytes(b"event")
    event_before = events.read_bytes()
    calls = 0

    def mutate(path: Path, mode: str) -> None:
        if mode == "replace":
            path.write_bytes(b"replace")
        elif mode == "delete":
            path.unlink()
        elif mode == "truncate":
            path.write_bytes(b"")
        elif mode == "append":
            path.write_bytes(path.read_bytes() + b"+")

    def phase36(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        mutate(state, state_mode)
        mutate(events, event_mode)
        return failure()

    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError):
        call(state, events, phase36)
    assert (
        calls == 1
        and state.read_bytes() == before
        and events.read_bytes() == event_before
    )
