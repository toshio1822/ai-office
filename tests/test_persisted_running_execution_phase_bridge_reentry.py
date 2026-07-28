"""Phase 56 contract matrix using injected Phase 49 fakes only."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedRunningExecutionPhaseBridgeCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_phase_bridge_reentry,
)
from ai_office.engine.persisted_running_execution_routing_reentry import (
    PersistedRunningExecutionRoutingCompatibilityError,
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


class FailureSubclass(StepRuntimeExecutionFailure):
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


def event(index: int, status: str = "succeeded") -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "w",
        "one" if index == 1 else "two",
        index,
        "e",
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "output" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]


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
    events_path.write_text(serialize_runtime_step_event_jsonl(event(1)))
    if status != "running":
        events_path.write_text(
            events_path.read_text()
            + serialize_runtime_step_event_jsonl(event(2, status))
        )
    return state_path, events_path


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


def call(values: dict[str, object], dependency: object) -> object:
    return route_persisted_running_execution_phase_bridge_reentry(
        **values, phase49_function=dependency
    )  # type: ignore[arg-type]


@pytest.mark.parametrize("expected", [success(), failure()])
def test_execution_route_delegates_once_with_exact_objects_and_returns_identity(
    tmp_path: Path, expected: object
) -> None:
    values, calls = inputs(tmp_path), []
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]

    def phase49(*args: object) -> object:
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

    assert call(values, phase49) is expected
    assert (
        len(calls) == 1
        and (values["state_path"].read_bytes(), values["events_path"].read_bytes())
        == before
    )  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_terminal_routes_stop_without_phase49(tmp_path: Path, kind: str) -> None:
    state, events = targets(tmp_path, "succeeded" if kind == "complete" else "failed")
    result = (
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
    assert call(values, lambda *_: pytest.fail("Phase 49 must not run")) is result
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "input_name",
    ["start", "employee", "resolved_tools", "api_key", "approval", "transport"],
)
def test_terminal_routes_reject_irrelevant_execution_inputs(
    tmp_path: Path, input_name: str
) -> None:
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
    values[input_name] = object()
    with pytest.raises(
        PersistedRunningExecutionPhaseBridgeCompatibilityError
    ) as caught:
        call(values, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "execution_inputs"


@pytest.mark.parametrize(
    "value", [object(), ResultSubclass(1), SimpleNamespace(state_bytes_written=1)]
)
def test_result_type_rejects_subclasses_and_substitutes(
    tmp_path: Path, value: object
) -> None:
    values = inputs(tmp_path)
    values["result"] = value
    with pytest.raises(
        PersistedRunningExecutionPhaseBridgeCompatibilityError
    ) as caught:
        call(values, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "result_type"


@pytest.mark.parametrize(
    "name, value, classification",
    [
        (
            "workflow",
            WorkflowSubclass.model_validate(workflow().model_dump()),
            "workflow_definition",
        ),
        ("workflow", SimpleNamespace(id="w", steps=()), "workflow_definition"),
        (
            "employee",
            EmployeeSubclass.model_validate(employee().model_dump()),
            "employee_contract",
        ),
        ("employee", SimpleNamespace(id="e"), "employee_contract"),
        ("start", SimpleNamespace(request=None, running_state=None), "start_contract"),
    ],
)
def test_exact_execution_input_models_reject_substitutes(
    tmp_path: Path, name: str, value: object, classification: str
) -> None:
    values = inputs(tmp_path)
    values[name] = value
    with pytest.raises(
        PersistedRunningExecutionPhaseBridgeCompatibilityError
    ) as caught:
        call(values, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == classification


@pytest.mark.parametrize(
    "part, field, value",
    [
        ("request", "model", "wrong"),
        ("request", "system_instructions", StringSubclass("system")),
        ("request", "task_instructions", None),
        ("request", "allowed_tools", ["tool"]),
        ("request", "allowed_tools", ("other",)),
        ("state", "workflow_id", "wrong"),
        ("state", "status", "succeeded"),
        ("state", "current_step_id", "one"),
        ("state", "current_step_index", True),
        ("state", "current_step_index", 0),
        ("state", "current_step_index", -1),
        ("state", "current_step_index", 3),
        ("state", "current_step_index", 2.0),
        ("state", "current_step_index", "2"),
        ("state", "current_step_index", None),
        ("state", "current_step_index", object()),
        ("state", "current_employee_id", "other"),
        ("state", "completed_step_ids", ()),
        ("state", "completed_step_ids", (1,)),
        ("state", "completed_step_ids", ("two",)),
        ("state", "last_failure_category", "api_error"),
    ],
)
def test_nested_start_fields_reject_before_phase49(
    tmp_path: Path, part: str, field: str, value: object
) -> None:
    values = inputs(tmp_path)
    nested = (
        values["start"].request if part == "request" else values["start"].running_state
    )  # type: ignore[union-attr]
    changed = replace(nested, **{field: value})
    values["start"] = replace(
        values["start"],
        **({"request": changed} if part == "request" else {"running_state": changed}),
    )  # type: ignore[arg-type]
    with pytest.raises(
        PersistedRunningExecutionPhaseBridgeCompatibilityError
    ) as caught:
        call(values, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize(
    "name, value, classification",
    [
        ("resolved_tools", [], "tools_contract"),
        ("resolved_tools", (ToolSubclass("tool", "Tool", ()),), "tools_contract"),
        ("resolved_tools", (SimpleNamespace(name="tool"),), "tools_contract"),
        ("api_key", None, "credential_contract"),
        ("api_key", object(), "credential_contract"),
        ("approval", None, "approval_contract"),
        ("approval", object(), "approval_contract"),
        ("transport", object(), "execution_inputs"),
    ],
)
def test_execution_inputs_reject_bad_tools_credential_approval_and_transport(
    tmp_path: Path, name: str, value: object, classification: str
) -> None:
    values = inputs(tmp_path)
    values[name] = value
    with pytest.raises(
        PersistedRunningExecutionPhaseBridgeCompatibilityError
    ) as caught:
        call(values, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == classification


@pytest.mark.parametrize("value", [True, 0, -1, 1.0, "1", None, object(), 999])
def test_persistence_result_byte_count_is_exact(tmp_path: Path, value: object) -> None:
    values = inputs(tmp_path)
    values["result"] = RunningStatePersistenceResult(value)  # type: ignore[arg-type]
    with pytest.raises(
        PersistedRunningExecutionPhaseBridgeCompatibilityError
    ) as caught:
        call(values, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "persistence_result_contract"


@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
def test_phase49_target_mutations_are_compensated(
    tmp_path: Path, target: str, operation: str
) -> None:
    values, calls = inputs(tmp_path), 0
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"private")
        else:
            path.write_bytes(b"private")

    def phase49(*_: object) -> object:
        nonlocal calls
        calls += 1
        if target in ("state", "both"):
            mutate(state)  # type: ignore[arg-type]
        if target in ("events", "both"):
            mutate(events)  # type: ignore[arg-type]
        return success()

    with pytest.raises(
        PersistedRunningExecutionPhaseBridgeCompatibilityError
    ) as caught:
        call(values, phase49)
    assert (
        caught.value.detail.classification == "execution_result_contract" and calls == 1
    )
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
@pytest.mark.parametrize("mutate", [False, True])
def test_dependency_errors_preserve_identity_or_sanitize_and_restore(
    tmp_path: Path, kind: str, mutate: bool
) -> None:
    values, calls = inputs(tmp_path), 0
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    safe = PersistedRunningExecutionRoutingCompatibilityError("private detail")

    def phase49(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutate:
            state.write_bytes(b"private")
            events.write_bytes(b"private")  # type: ignore[union-attr]
        if kind == "safe":
            raise safe
        raise RuntimeError("private response")

    expected = (
        PersistedRunningExecutionRoutingCompatibilityError
        if kind == "safe"
        else PersistedRunningExecutionPhaseBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        call(values, phase49)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]
    if kind == "safe":
        assert caught.value is safe
    else:
        assert (
            caught.value.detail.classification == "dependency_error"
            and "private" not in str(caught.value)
        )


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failure_attempts_both_and_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    values = inputs(tmp_path)
    state, events = values["state_path"], values["events_path"]
    original, attempts = Path.write_bytes, []

    def phase49(*_: object) -> object:
        original(state, b"private")
        original(events, b"private")
        raise RuntimeError("private")  # type: ignore[arg-type]

    def restore(path: Path, data: bytes) -> int:
        attempts.append(path)
        if (
            failed == "both"
            or (failed == "state" and path is state)
            or (failed == "events" and path is events)
        ):
            raise OSError("private")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", restore)
    with pytest.raises(
        PersistedRunningExecutionPhaseBridgeCompatibilityError
    ) as caught:
        call(values, phase49)
    assert (
        caught.value.detail.classification == "dependency_rollback"
        and state in attempts
        and events in attempts
    )
