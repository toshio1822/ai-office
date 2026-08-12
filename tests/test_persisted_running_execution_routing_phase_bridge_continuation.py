"""Phase 70 continuation boundary contract tests."""

# ruff: noqa: E501

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError,
    PersistedRunningExecutionRoutingPhaseBridgeError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_routing_phase_bridge_continuation,
)
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationFailure,
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


class WorkflowSubclass(WorkflowDefinition):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


class RequestSubclass(ModelInvocationRequest):
    pass


class StateSubclass(WorkflowExecutionState):
    pass


class StartSubclass(PreparedStepExecutionStart):
    pass


class ToolSubclass(ToolDefinition):
    pass


class ApiKeySubclass(OpenAIApiKey):
    pass


class ApprovalSubclass(ModelInvocationExecutionApproval):
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
            "allowed_tools": ["tool", "other"],
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


def values(tmp_path: Path) -> dict[str, object]:
    state = WorkflowExecutionState("w", "running", "two", 2, "e", ("one",), None)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(serialize_runtime_step_event_jsonl(event()))
    request = ModelInvocationRequest("model", "system", "b", ("tool", "other"))
    start = PreparedStepExecutionStart(request, state)
    tools = (ToolDefinition("tool", "Tool", ()), ToolDefinition("other", "Other", ()))
    return {
        "result": RunningStatePersistenceResult(len(state_path.read_bytes())),
        "start": start,
        "workflow": workflow(),
        "employee": employee(),
        "state_path": state_path,
        "events_path": events_path,
        "resolved_tools": tools,
        "api_key": OpenAIApiKey(value=SecretStr("synthetic")),
        "approval": approve_model_invocation_execution(
            request, tools, provider="openai", approved_by="test", approval_id="id"
        ),
        "transport": lambda _: None,
    }


def runtime_result() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "two",
        2,
        "e",
        ModelInvocationSuccess("openai", "response", None, "completed", ("ok",), "ok"),
    )


def runtime_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w",
        "two",
        2,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None),
    )


def call(values_: dict[str, object], dependency: object) -> object:
    return route_persisted_running_execution_routing_phase_bridge_continuation(
        **values_, phase63_function=dependency
    )  # type: ignore[arg-type]


def test_execution_delegates_once_with_identity_and_returns_exact_result(
    tmp_path: Path,
) -> None:
    supplied, calls = values(tmp_path), []
    before = supplied["state_path"].read_bytes(), supplied["events_path"].read_bytes()  # type: ignore[union-attr]

    def phase63(*args: object) -> object:
        calls.append(args)
        assert all(
            actual is supplied[name]
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
        return runtime_result()

    returned = call(supplied, phase63)
    assert returned is not None and len(calls) == 1
    assert (
        supplied["state_path"].read_bytes(),
        supplied["events_path"].read_bytes(),
    ) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_terminal_routes_stop_without_phase63_and_preserve_identity(
    tmp_path: Path, kind: str
) -> None:
    state = WorkflowExecutionState(
        "w",
        "succeeded" if kind == "complete" else "failed",
        "two",
        2,
        "e",
        ("one", "two") if kind == "complete" else ("one",),
        None if kind == "complete" else "api_error",
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    terminal = replace(
        event(),
        event_type="step_succeeded" if kind == "complete" else "step_failed",
        step_id="two",
        step_index=2,
        next_status="succeeded" if kind == "complete" else "failed",
        response_id="response" if kind == "complete" else None,
        output_text="output" if kind == "complete" else None,
        failure_category=None if kind == "complete" else "api_error",
        message=None if kind == "complete" else "safe",
    )
    events_path.write_text(
        serialize_runtime_step_event_jsonl(event())
        + serialize_runtime_step_event_jsonl(terminal)
    )
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
    supplied = {
        "result": result,
        "start": None,
        "workflow": workflow(),
        "employee": None,
        "state_path": state_path,
        "events_path": events_path,
        "resolved_tools": None,
        "api_key": None,
        "approval": None,
        "transport": None,
    }
    before = state_path.read_bytes(), events_path.read_bytes()
    assert (
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called")) is result
    )
    assert (state_path.read_bytes(), events_path.read_bytes()) == before


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_target_oserror_is_classified_before_phase63(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, operation: str
) -> None:
    supplied = values(tmp_path)
    original = getattr(Path, operation)
    target_path = supplied[f"{target}_path"]

    def fail(path: Path, *args: object, **kwargs: object) -> object:
        if path == target_path:
            raise OSError("synthetic")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == f"{target}_target".replace(
        "events", "event"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "other"),
        ("current_step_index", True),
        ("current_step_id", "one"),
        ("last_failure_category", "api_error"),
    ],
)
def test_persisted_running_state_fields_are_revalidated(
    tmp_path: Path, field: str, value: object
) -> None:
    supplied = values(tmp_path)
    start = supplied["start"]
    supplied["start"] = replace(
        start, running_state=replace(start.running_state, **{field: value})
    )  # type: ignore[union-attr]
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize("dependency_result", [runtime_result(), runtime_failure()])
def test_success_and_failure_routes_return_exact_dependency_object(
    tmp_path: Path, dependency_result: object
) -> None:
    supplied = values(tmp_path)
    returned = call(supplied, lambda *_: dependency_result)
    assert returned is dependency_result


@pytest.mark.parametrize(
    "value,classification",
    [
        (RunningStatePersistenceResult(0), "persistence_result_contract"),
        (RunningStatePersistenceResult(1), "persistence_result_contract"),
        (type("ResultSubclass", (RunningStatePersistenceResult,), {})(1), "result_type"),
    ],
)
def test_persistence_result_type_and_byte_count_are_exact(
    tmp_path: Path, value: object, classification: str
) -> None:
    supplied = values(tmp_path)
    supplied["result"] = value
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == classification


@pytest.mark.parametrize(
    "name, replacement, classification",
    [
        ("workflow", WorkflowSubclass.model_validate(workflow().model_dump()), "workflow_definition"),
        ("employee", EmployeeSubclass.model_validate(employee().model_dump()), "employee_contract"),
        ("start", "start", "start_contract"),
        ("api_key", ApiKeySubclass(value=SecretStr("synthetic")), "credential_contract"),
        ("approval", "approval", "approval_contract"),
    ],
)
def test_exact_model_types_are_rejected_before_dependency(
    tmp_path: Path, name: str, replacement: object, classification: str
) -> None:
    supplied = values(tmp_path)
    if name == "start":
        base = supplied["start"]
        replacement = StartSubclass(base.request, base.running_state)  # type: ignore[union-attr]
    elif name == "approval":
        base = supplied["approval"]
        replacement = ApprovalSubclass(*base.__dict__.values())  # type: ignore[union-attr]
    supplied[name] = replacement
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == classification


def test_exact_request_state_and_tool_item_models_are_rejected_before_dependency(
    tmp_path: Path,
) -> None:
    supplied = values(tmp_path)
    base = supplied["start"]
    supplied["start"] = StartSubclass(
        RequestSubclass(*base.request.__dict__.values()), base.running_state  # type: ignore[union-attr]
    )
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == "start_contract"

    supplied = values(tmp_path)
    base = supplied["start"]
    state = StateSubclass(*base.running_state.__dict__.values())  # type: ignore[union-attr]
    supplied["start"] = PreparedStepExecutionStart(base.request, state)  # type: ignore[union-attr]
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == "start_contract"

    supplied = values(tmp_path)
    tools = supplied["resolved_tools"]
    supplied["resolved_tools"] = (ToolSubclass(*tools[0].__dict__.values()), tools[1])  # type: ignore[union-attr]
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == "tools_contract"


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "wrong"),
        ("model", 1),
        ("system_instructions", "wrong"),
        ("system_instructions", True),
        ("task_instructions", "wrong"),
        ("task_instructions", None),
        ("allowed_tools", ("tool",)),
        ("allowed_tools", ["tool", "other"]),
    ],
)
def test_every_request_field_is_revalidated(
    tmp_path: Path, field: str, value: object
) -> None:
    supplied = values(tmp_path)
    start = supplied["start"]
    supplied["start"] = replace(
        start, request=replace(start.request, **{field: value})
    )  # type: ignore[union-attr]
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "wrong"),
        ("workflow_id", True),
        ("status", "succeeded"),
        ("status", True),
        ("current_step_id", "one"),
        ("current_step_id", 2),
        ("current_step_index", 1),
        ("current_step_index", True),
        ("current_employee_id", "wrong"),
        ("current_employee_id", None),
        ("completed_step_ids", ("two",)),
        ("completed_step_ids", ["one"]),
        ("last_failure_category", "api_error"),
        ("last_failure_category", False),
    ],
)
def test_every_running_state_field_is_revalidated(
    tmp_path: Path, field: str, value: object
) -> None:
    supplied = values(tmp_path)
    start = supplied["start"]
    supplied["start"] = replace(
        start, running_state=replace(start.running_state, **{field: value})
    )  # type: ignore[union-attr]
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == "start_contract"


def test_employee_identity_and_derived_request_fields_are_revalidated(
    tmp_path: Path,
) -> None:
    supplied = values(tmp_path)
    supplied["employee"] = employee().model_copy(update={"id": "other"})
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize(
    "tools,classification",
    [
        ((), "start_contract"),
        ((ToolDefinition("other", "Other", ()), ToolDefinition("tool", "Tool", ())), "start_contract"),
        ((ToolDefinition("tool", "Tool", ()), ToolDefinition("tool", "Tool", ())), "start_contract"),
        ((ToolDefinition("tool", "Tool", ()),), "start_contract"),
        (("tool", "other"), "tools_contract"),
    ],
)
def test_resolved_tools_tuple_order_duplicates_missing_extra_and_item_type(
    tmp_path: Path, tools: object, classification: str
) -> None:
    supplied = values(tmp_path)
    supplied["resolved_tools"] = tools
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == classification


def test_api_key_and_approval_contracts_are_revalidated(tmp_path: Path) -> None:
    supplied = values(tmp_path)
    supplied["api_key"] = object()
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == "credential_contract"

    supplied = values(tmp_path)
    supplied["approval"] = replace(supplied["approval"], approved=False)
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == "approval_contract"


@pytest.mark.parametrize(
    "name",
    ["start", "employee", "resolved_tools", "api_key", "approval", "transport"],
)
def test_execution_route_rejects_each_missing_required_input(
    tmp_path: Path, name: str
) -> None:
    supplied = values(tmp_path)
    supplied[name] = None
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification in {
        "execution_inputs",
        "start_contract",
        "employee_contract",
        "tools_contract",
        "credential_contract",
        "approval_contract",
    }


def test_direct_persisted_success_is_rejected(tmp_path: Path) -> None:
    supplied = values(tmp_path)
    supplied["result"] = PersistedExecutionOutcome(
        "persisted_success", "w", "two", 2, "e", "api_error"
    )
    supplied.update(
        start=None,
        employee=None,
        resolved_tools=None,
        api_key=None,
        approval=None,
        transport=None,
    )
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == "failure_contract"


@pytest.mark.parametrize("kind", ["complete", "failure"])
@pytest.mark.parametrize(
    "name", ["start", "employee", "resolved_tools", "api_key", "approval", "transport"]
)
def test_both_stop_routes_reject_every_execution_only_input(
    tmp_path: Path, kind: str, name: str
) -> None:
    state = WorkflowExecutionState(
        "w",
        "succeeded" if kind == "complete" else "failed",
        "two",
        2,
        "e",
        ("one", "two") if kind == "complete" else ("one",),
        None if kind == "complete" else "api_error",
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    terminal = replace(
        event(),
        event_type="step_succeeded" if kind == "complete" else "step_failed",
        step_id="two",
        step_index=2,
        next_status="succeeded" if kind == "complete" else "failed",
        response_id="response" if kind == "complete" else None,
        output_text="output" if kind == "complete" else None,
        failure_category=None if kind == "complete" else "api_error",
        message=None if kind == "complete" else "safe",
    )
    events_path.write_text(
        serialize_runtime_step_event_jsonl(event())
        + serialize_runtime_step_event_jsonl(terminal)
    )
    result: object = (
        WorkflowProgressionDecision(
            "workflow_complete", "w", "two", 2, "e", None, None, None, "last_step_succeeded"
        )
        if kind == "complete"
        else PersistedExecutionOutcome("persisted_failure", "w", "two", 2, "e", "api_error")
    )
    supplied = {
        "result": result,
        "start": None,
        "workflow": workflow(),
        "employee": None,
        "state_path": state_path,
        "events_path": events_path,
        "resolved_tools": None,
        "api_key": None,
        "approval": None,
        "transport": None,
    }
    supplied[name] = object()
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == "execution_inputs"


@pytest.mark.parametrize(
    "dependency_result",
    [
        object(),
        StepRuntimeExecutionSuccess("w", "wrong", 2, "e", runtime_result().invocation_result),
        StepRuntimeExecutionSuccess("w", "two", True, "e", runtime_result().invocation_result),
        StepRuntimeExecutionSuccess("w", "two", 2, "wrong", runtime_result().invocation_result),
        StepRuntimeExecutionFailure("w", "two", True, "e", runtime_failure().invocation_result),
    ],
)
def test_malformed_dependency_return_and_runtime_result_fields_are_rejected(
    tmp_path: Path, dependency_result: object
) -> None:
    supplied = values(tmp_path)
    before = supplied["state_path"].read_bytes(), supplied["events_path"].read_bytes()  # type: ignore[union-attr]
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: dependency_result)
    assert caught.value.detail.classification == "execution_result_contract"
    assert (
        supplied["state_path"].read_bytes(),
        supplied["events_path"].read_bytes(),
    ) == before  # type: ignore[union-attr]


def test_safe_phase63_error_identity_is_preserved_and_not_retried(tmp_path: Path) -> None:
    supplied, calls = values(tmp_path), 0
    error = PersistedRunningExecutionRoutingPhaseBridgeError("private")

    def phase63(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(PersistedRunningExecutionRoutingPhaseBridgeError) as caught:
        call(supplied, phase63)
    assert caught.value is error and calls == 1


def test_unexpected_phase63_error_is_sanitized_and_not_retried(tmp_path: Path) -> None:
    supplied, calls = values(tmp_path), 0

    def phase63(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("private provider detail")

    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, phase63)
    assert caught.value.detail.classification == "dependency_error"
    assert "private provider detail" not in str(caught.value)
    assert calls == 1


@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_state_events_and_both_mutations_are_compensated(
    tmp_path: Path, target: str
) -> None:
    supplied, calls = values(tmp_path), 0
    state, events = supplied["state_path"], supplied["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]

    def phase63(*_: object) -> object:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            state.write_bytes(b"mutated")  # type: ignore[union-attr]
        if target in {"events", "both"}:
            events.write_bytes(b"mutated")  # type: ignore[union-attr]
        return runtime_result()

    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, phase63)
    assert caught.value.detail.classification == "execution_result_contract"
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_each_rollback_failure_attempts_both_targets_and_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    supplied, calls = values(tmp_path), 0
    state, events = supplied["state_path"], supplied["events_path"]
    original_write = Path.write_bytes
    attempts: list[Path] = []

    def phase63(*_: object) -> object:
        nonlocal calls
        calls += 1
        original_write(state, b"mutated")
        original_write(events, b"mutated")
        return runtime_result()

    def restore(path: Path, data: bytes) -> int:
        attempts.append(path)
        if failed == "both" or (failed == "state" and path == state) or (failed == "events" and path == events):
            raise OSError("rollback failure")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", restore)
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, phase63)
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempts.count(state) == 1 and attempts.count(events) == 1
    assert calls == 1


@pytest.mark.parametrize("name", ["state_path", "events_path"])
def test_missing_and_non_regular_targets_are_rejected_before_dependency(
    tmp_path: Path, name: str
) -> None:
    supplied = values(tmp_path)
    path = supplied[name]
    path.unlink()  # type: ignore[union-attr]
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == (
        "state_target" if name == "state_path" else "event_target"
    )

    supplied = values(tmp_path)
    path = supplied[name]
    path.unlink()  # type: ignore[union-attr]
    path.mkdir()  # type: ignore[union-attr]
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        call(supplied, lambda *_: pytest.fail("Phase 63 must not be called"))
    assert caught.value.detail.classification == (
        "state_target" if name == "state_path" else "event_target"
    )
@pytest.mark.parametrize("output", ["", None, 123, 1.5])
def test_succeeded_predecessor_empty_output_is_accepted_and_non_string_rejected(
    tmp_path: Path, output: object
) -> None:
    supplied = values(tmp_path)
    events = supplied["events_path"]
    events.write_text(
        serialize_runtime_step_event_jsonl(
            RuntimeStepEvent(
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
                output,  # type: ignore[arg-type]
                None,
            )
        )
    )
    calls = 0
    expected = runtime_result()

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    if output == "":
        returned = call(supplied, dependency)
        assert returned is expected and calls == 1
    else:
        with pytest.raises(
            PersistedRunningExecutionRoutingPhaseBridgeContinuationCompatibilityError
        ) as caught:
            call(supplied, dependency)
        assert caught.value.detail.classification == "persistence_result_contract"
        assert calls == 0


def continuation(tmp_path: Path) -> dict[str, object]:
    """Running at step three with succeeded steps one and two (two predecessors)."""
    wf = WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "a"},
                {"id": "two", "name": "Two", "employee": "e", "instructions": "b"},
                {"id": "three", "name": "Three", "employee": "e", "instructions": "c"},
            ],
        }
    )
    state = WorkflowExecutionState(
        "w", "running", "three", 3, "e", ("one", "two"), None
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(
        serialize_runtime_step_event_jsonl(
            RuntimeStepEvent(
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
                "o",
                None,
            )
        )
        + serialize_runtime_step_event_jsonl(
            RuntimeStepEvent(
                "step_succeeded",
                "w",
                "two",
                2,
                "e",
                "running",
                "succeeded",
                "openai",
                None,
                "response2",
                "request2",
                "o2",
                None,
            )
        )
    )
    request = ModelInvocationRequest("model", "system", "c", ("tool", "other"))
    tools = (
        ToolDefinition("tool", "Tool", ()),
        ToolDefinition("other", "Other", ()),
    )
    return {
        "result": RunningStatePersistenceResult(len(state_path.read_bytes())),
        "start": PreparedStepExecutionStart(request, state),
        "workflow": wf,
        "employee": employee(),
        "state_path": state_path,
        "events_path": events_path,
        "resolved_tools": tools,
        "api_key": OpenAIApiKey(value=SecretStr("synthetic")),
        "approval": approve_model_invocation_execution(
            request,
            tools,
            provider="openai",
            approved_by="test",
            approval_id="id",
        ),
        "transport": lambda _: None,
    }


def runtime_three() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "three",
        3,
        "e",
        ModelInvocationSuccess("openai", "response", None, "completed", ("ok",), "ok"),
    )


def test_earlier_succeeded_predecessor_empty_output_delegates_exactly_once_in_canonical_identity_order(
    tmp_path: Path,
) -> None:
    supplied = continuation(tmp_path)
    events = supplied["events_path"]
    first = serialize_runtime_step_event_jsonl(
        RuntimeStepEvent(
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
            "",
            None,
        )
    )
    second = serialize_runtime_step_event_jsonl(
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            "two",
            2,
            "e",
            "running",
            "succeeded",
            "openai",
            None,
            "response2",
            "request2",
            "o2",
            None,
        )
    )
    events.write_text(first + second)
    state_before = supplied["state_path"].read_bytes()
    events_before = events.read_bytes()
    transport_calls = 0

    def transport(_: object) -> object:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("transport must not be called")

    supplied["transport"] = transport
    calls: list[tuple[object, ...]] = []
    expected = runtime_three()

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    returned = call(supplied, dependency)
    assert returned is expected and len(calls) == 1
    assert all(
        left is right
        for left, right in zip(
            calls[0],
            (
                supplied["result"],
                supplied["start"],
                supplied["workflow"],
                supplied["employee"],
                supplied["state_path"],
                supplied["events_path"],
                supplied["resolved_tools"],
                supplied["api_key"],
                supplied["approval"],
                supplied["transport"],
            ),
            strict=True,
        )
    )
    assert supplied["state_path"].read_bytes() == state_before
    assert events.read_bytes() == events_before
    assert transport_calls == 0


def test_immediate_succeeded_predecessor_empty_output_delegates_exactly_once_in_canonical_identity_order(
    tmp_path: Path,
) -> None:
    supplied = continuation(tmp_path)
    events = supplied["events_path"]
    first = serialize_runtime_step_event_jsonl(
        RuntimeStepEvent(
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
            "o",
            None,
        )
    )
    second = serialize_runtime_step_event_jsonl(
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            "two",
            2,
            "e",
            "running",
            "succeeded",
            "openai",
            None,
            "response2",
            "request2",
            "",
            None,
        )
    )
    events.write_text(first + second)
    state_before = supplied["state_path"].read_bytes()
    events_before = events.read_bytes()
    transport_calls = 0

    def transport(_: object) -> object:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("transport must not be called")

    supplied["transport"] = transport
    calls: list[tuple[object, ...]] = []
    expected = runtime_three()

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    returned = call(supplied, dependency)
    assert returned is expected and len(calls) == 1
    assert all(
        left is right
        for left, right in zip(
            calls[0],
            (
                supplied["result"],
                supplied["start"],
                supplied["workflow"],
                supplied["employee"],
                supplied["state_path"],
                supplied["events_path"],
                supplied["resolved_tools"],
                supplied["api_key"],
                supplied["approval"],
                supplied["transport"],
            ),
            strict=True,
        )
    )
    assert supplied["state_path"].read_bytes() == state_before
    assert events.read_bytes() == events_before
    assert transport_calls == 0
