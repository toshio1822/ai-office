"""Phase 63 contract tests using injected Phase 56 fakes only."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedRunningExecutionPhaseBridgeError,
    PersistedRunningExecutionRoutingPhaseBridgeCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_routing_phase_bridge_reentry,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition


class ResultSubclass(RunningStatePersistenceResult):
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


class ToolSubclass(ToolDefinition):
    pass


class SuccessSubclass(StepRuntimeExecutionSuccess):
    pass


class StringSubclass(str):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "a"},
                {"id": "two", "name": "Two", "employee": "e", "instructions": "b"},
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "e",
            "name": "E",
            "role": "R",
            "instructions": "system",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def event() -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        "one",
        1,
        "e",
        "running",
        "succeeded",
        "openai",
        None,
        "response",
        "request",
        "output",
        None,
    )


def targets(tmp_path: Path, status: str = "running") -> tuple[Path, Path]:
    state = WorkflowExecutionState(
        "w",
        status,
        "two",
        2,
        "e",
        ("one", "two") if status == "succeeded" else ("one",),
        None if status != "failed" else "api_error",
    )  # type: ignore[arg-type]
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(serialize_runtime_step_event_jsonl(event()))
    if status != "running":
        terminal = replace(
            event(),
            event_type="step_succeeded" if status == "succeeded" else "step_failed",
            step_id="two",
            step_index=2,
            next_status=status,
            response_id="response" if status == "succeeded" else None,
            output_text="output" if status == "succeeded" else None,
            failure_category=None if status == "succeeded" else "api_error",
            message=None if status == "succeeded" else "safe",
        )
        events_path.write_text(
            events_path.read_text() + serialize_runtime_step_event_jsonl(terminal)
        )
    return state_path, events_path


def success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "two",
        2,
        "e",
        ModelInvocationSuccess("openai", "response", None, "completed", ("ok",), "ok"),
    )


def failure() -> StepRuntimeExecutionFailure:
    from ai_office.invocation import ModelInvocationFailure

    return StepRuntimeExecutionFailure(
        "w",
        "two",
        2,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None),
    )


def inputs(tmp_path: Path) -> dict[str, object]:
    state, events = targets(tmp_path)
    request = ModelInvocationRequest("model", "system", "b", ("tool",))
    start = PreparedStepExecutionStart(
        request, WorkflowExecutionState("w", "running", "two", 2, "e", ("one",), None)
    )
    tools = (ToolDefinition("tool", "Tool", ()),)
    approval = approve_model_invocation_execution(
        request, tools, provider="openai", approved_by="test", approval_id="id"
    )
    return {
        "result": RunningStatePersistenceResult(len(state.read_bytes())),
        "start": start,
        "workflow": workflow(),
        "employee": employee(),
        "state_path": state,
        "events_path": events,
        "resolved_tools": tools,
        "api_key": OpenAIApiKey(value=SecretStr("synthetic")),
        "approval": approval,
        "transport": lambda _: None,
    }


def call(values: dict[str, object], dependency: object) -> object:
    return route_persisted_running_execution_routing_phase_bridge_reentry(
        **values,
        phase56_function=dependency,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("expected", [success(), failure()])
def test_execution_routes_delegate_once_with_identity_and_return_identity(
    tmp_path: Path, expected: object
) -> None:
    values, calls = inputs(tmp_path), []
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]

    def phase56(*args: object) -> object:
        calls.append(args)
        assert all(
            actual is values[name]
            for actual, name in zip(
                args,
                (
                    "result",
                    "start",
                    "workflow",
                    "employee",
                    "state_path",
                    "events_path",
                    "resolved_tools",
                    "api_key",
                    "approval",
                    "transport",
                ),
                strict=True,
            )
        )
        return expected

    assert call(values, phase56) is expected
    assert len(calls) == 1
    assert (
        values["state_path"].read_bytes(),
        values["events_path"].read_bytes(),
    ) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_return_same_object_without_phase56(
    tmp_path: Path, kind: str
) -> None:
    state, events = targets(tmp_path, "succeeded" if kind == "complete" else "failed")
    result: object = (
        WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            "two",
            2,
            "e",
            None,
            None,
            None,
            "last_step_succeeded",
        )
        if kind == "complete"
        else PersistedExecutionOutcome(
            "persisted_failure", "w", "two", 2, "e", "api_error"
        )
    )
    values = {
        "result": result,
        "start": None,
        "workflow": workflow(),
        "employee": None,
        "state_path": state,
        "events_path": events,
        "resolved_tools": None,
        "api_key": None,
        "approval": None,
        "transport": None,
    }
    before = state.read_bytes(), events.read_bytes()
    assert call(values, lambda *_: pytest.fail("must not call Phase 56")) is result
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "name", ["start", "employee", "resolved_tools", "api_key", "approval", "transport"]
)
def test_stop_routes_reject_execution_only_inputs(tmp_path: Path, name: str) -> None:
    state, events = targets(tmp_path, "succeeded")
    values = {
        "result": WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            "two",
            2,
            "e",
            None,
            None,
            None,
            "last_step_succeeded",
        ),
        "start": None,
        "workflow": workflow(),
        "employee": None,
        "state_path": state,
        "events_path": events,
        "resolved_tools": None,
        "api_key": None,
        "approval": None,
        "transport": None,
    }
    values[name] = object()
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(values, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "execution_inputs"


@pytest.mark.parametrize(
    "name,value,classification",
    [
        ("result", object(), "result_type"),
        ("result", ResultSubclass(1), "result_type"),
        (
            "workflow",
            WorkflowSubclass.model_validate(workflow().model_dump()),
            "workflow_definition",
        ),
        (
            "employee",
            EmployeeSubclass.model_validate(employee().model_dump()),
            "employee_contract",
        ),
        ("start", object(), "start_contract"),
        ("resolved_tools", (ToolSubclass("tool", "Tool", ()),), "tools_contract"),
        ("api_key", object(), "credential_contract"),
        ("approval", object(), "approval_contract"),
        ("transport", object(), "execution_inputs"),
    ],
)
def test_exact_type_contracts_reject_before_dependency(
    tmp_path: Path, name: str, value: object, classification: str
) -> None:
    values = inputs(tmp_path)
    if name == "start":
        base = values["start"]
        value = StartSubclass(base.request, base.running_state)  # type: ignore[union-attr]
    values[name] = value
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(values, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == classification


@pytest.mark.parametrize(
    "field",
    [
        "workflow_id",
        "status",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "completed_step_ids",
        "last_failure_category",
    ],
)
def test_start_state_fields_are_strict(tmp_path: Path, field: str) -> None:
    values = inputs(tmp_path)
    start_value = values["start"]
    current = start_value.running_state
    replacements = {
        "workflow_id": "wrong",
        "status": StringSubclass("running"),
        "current_step_id": "one",
        "current_step_index": True,
        "current_employee_id": "other",
        "completed_step_ids": ("two",),
        "last_failure_category": "api_error",
    }
    values["start"] = replace(
        start_value, running_state=replace(current, **{field: replacements[field]})
    )
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(values, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "wrong"),
        ("system_instructions", StringSubclass("system")),
        ("task_instructions", None),
        ("allowed_tools", ("other",)),
    ],
)
def test_request_fields_are_strict(tmp_path: Path, field: str, value: object) -> None:
    values = inputs(tmp_path)
    start_value = values["start"]
    values["start"] = replace(
        start_value, request=replace(start_value.request, **{field: value})
    )
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(values, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
def test_dependency_mutations_are_compensated(
    tmp_path: Path, target: str, operation: str
) -> None:
    values, calls = inputs(tmp_path), 0
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"private")
        else:
            path.write_bytes(b"private")

    def phase56(*_: object) -> object:
        nonlocal calls
        calls += 1
        if target in ("state", "both"):
            mutate(state)
        if target in ("events", "both"):
            mutate(events)
        return success()

    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(values, phase56)
    assert (
        caught.value.detail.classification == "execution_result_contract" and calls == 1
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
@pytest.mark.parametrize("mutate", [False, True])
def test_dependency_errors_preserve_safe_identity_or_sanitize(
    tmp_path: Path, kind: str, mutate: bool
) -> None:
    values, calls = inputs(tmp_path), 0
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()
    safe = PersistedRunningExecutionPhaseBridgeError("private")

    def phase56(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutate:
            state.write_bytes(b"private")
            events.write_bytes(b"private")
        if kind == "safe":
            raise safe
        raise RuntimeError("private")

    expected = (
        PersistedRunningExecutionPhaseBridgeError
        if kind == "safe"
        else PersistedRunningExecutionRoutingPhaseBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        call(values, phase56)
    if kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
        assert "private" not in str(caught.value)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    values = inputs(tmp_path)
    state, events = values["state_path"], values["events_path"]
    original = Path.write_bytes
    attempts: list[Path] = []

    def phase56(*_: object) -> object:
        original(state, b"private")
        original(events, b"private")
        raise RuntimeError("private")

    def restore(path: Path, data: bytes) -> int:
        attempts.append(path)
        if (
            failed == "both"
            or (failed == "state" and path == state)
            or (failed == "events" and path == events)
        ):
            raise OSError("rollback")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", restore)
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(values, phase56)
    assert caught.value.detail.classification == "dependency_rollback"
    assert state in attempts and events in attempts
