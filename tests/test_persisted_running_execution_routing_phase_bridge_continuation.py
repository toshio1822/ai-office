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
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_routing_phase_bridge_continuation,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition


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


def values(tmp_path: Path) -> dict[str, object]:
    state = WorkflowExecutionState("w", "running", "two", 2, "e", ("one",), None)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(serialize_runtime_step_event_jsonl(event()))
    request = ModelInvocationRequest("model", "system", "b", ("tool",))
    start = PreparedStepExecutionStart(request, state)
    tools = (ToolDefinition("tool", "Tool", ()),)
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
