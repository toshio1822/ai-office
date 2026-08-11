"""Focused Phase 141 persisted-running execution outer bridge tests."""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeReentryContinuationError as Phase133Error,
)
from ai_office.invocation import (
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
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition


class WorkflowChild(WorkflowDefinition):
    pass


class StepChild(WorkflowStepDefinition):
    pass


class EmployeeChild(EmployeeDefinition):
    pass


class StartChild(PreparedStepExecutionStart):
    pass


class ResultChild(RunningStatePersistenceResult):
    pass


class RequestChild(ModelInvocationRequest):
    pass


class StateChild(WorkflowExecutionState):
    pass


class ToolChild(ToolDefinition):
    pass


class ParameterChild(ToolParameterDefinition):
    pass


class KeyChild(OpenAIApiKey):
    pass


ApprovalType = type(
    approve_model_invocation_execution(
        ModelInvocationRequest("model", "system", "task", ()),
        (),
        provider="openai",
        approved_by="test",
        approval_id="id",
    )
)


class ApprovalChild(ApprovalType):
    pass


class RuntimeSuccessChild(StepRuntimeExecutionSuccess):
    pass


class RuntimeFailureChild(StepRuntimeExecutionFailure):
    pass


class DecisionChild(WorkflowProgressionDecision):
    pass


class OutcomeChild(PersistedExecutionOutcome):
    pass


class IntChild(int):
    pass


class SecretChild(SecretStr):
    pass


class SecretCompatible:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "a"},
                {"id": "two", "name": "Two", "employee": "e", "instructions": "b"},
                {"id": "three", "name": "Three", "employee": "e", "instructions": "c"},
                {"id": "four", "name": "Four", "employee": "e", "instructions": "d"},
                {"id": "five", "name": "Five", "employee": "e", "instructions": "e"},
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


def _event(
    event_type: str,
    step_id: str,
    step_index: int,
    provider: object = "openai",
    failure_category: object = None,
    response_id: object = "response",
    request_id: object = "request",
    output_text: object = "output",
    message: object = None,
) -> RuntimeStepEvent:
    next_status = "succeeded" if event_type == "step_succeeded" else "failed"
    if event_type == "step_failed":
        response_id, output_text = None, None
        message = "safe failure"
    return RuntimeStepEvent(
        event_type,
        "w",
        step_id,
        step_index,
        "e",
        "running",
        next_status,
        provider,  # type: ignore[arg-type]
        failure_category,  # type: ignore[arg-type]
        response_id,  # type: ignore[arg-type]
        request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
        message,  # type: ignore[arg-type]
    )


def setup(tmp_path: Path) -> dict[str, object]:
    state_value = WorkflowExecutionState(
        "w", "running", "five", 5, "e", ("one", "two", "three", "four"), None
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state_value), encoding="utf-8")
    events_path.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(
                _event("step_succeeded", step_id, index, provider)
            )
            for step_id, index, provider in (
                ("one", 1, "other"),
                ("two", 2, "other"),
                ("three", 3, "other"),
                ("four", 4, "openai"),
            )
        ),
        encoding="utf-8",
    )
    request = ModelInvocationRequest("model", "system", "e", ("tool",))
    tools = (ToolDefinition("tool", "Tool", ()),)
    return {
        "result": RunningStatePersistenceResult(len(state_path.read_bytes())),
        "start": PreparedStepExecutionStart(request, state_value),
        "workflow": workflow(),
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
            approval_id="approval-id",
        ),
        "transport": lambda _: None,
    }


def runtime_success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "five",
        5,
        "e",
        ModelInvocationSuccess("openai", "response", "request", "completed", ("ok",), "ok"),
    )


def runtime_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w",
        "five",
        5,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", "request", None, None, None),
    )


def reject(values: dict[str, object], classification: str, **changes: object) -> None:
    supplied = dict(values)
    supplied.update(changes)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime_success()

    supplied.setdefault("phase133_function", dependency)
    with pytest.raises(
        PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            **supplied  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == classification
    assert calls == 0


def reject_unchanged(
    values: dict[str, object], classification: str, **changes: object
) -> None:
    state = values["state_path"]
    events = values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    reject(values, classification, **changes)
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


def reject_after_call(values: dict[str, object], value: object, classification: str) -> None:
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return value

    with pytest.raises(
        PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            **values, phase133_function=dependency  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == classification and calls == 1


def succeeded_targets(tmp_path: Path, provider: object = "other") -> tuple[Path, Path]:
    state = WorkflowExecutionState(
        "w", "succeeded", "five", 5, "e", ("one", "two", "three", "four", "five"), None
    )
    state_path, events_path = tmp_path / "succeeded-state", tmp_path / "succeeded-events"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(_event("step_succeeded", step, index, event_provider))
            for step, index, event_provider in (
                ("one", 1, "openai"),
                ("two", 2, "other"),
                ("three", 3, "openai"),
                ("four", 4, "other"),
                ("five", 5, provider),
            )
        ),
        encoding="utf-8",
    )
    return state_path, events_path


def failed_targets(tmp_path: Path, provider: object = "other") -> tuple[Path, Path]:
    state = WorkflowExecutionState(
        "w", "failed", "five", 5, "e", ("one", "two", "three", "four"), "api_error"
    )
    state_path, events_path = tmp_path / "failed-state", tmp_path / "failed-events"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(_event("step_succeeded", step, index, event_provider))
            for step, index, event_provider in (
                ("one", 1, "openai"),
                ("two", 2, "other"),
                ("three", 3, "openai"),
                ("four", 4, "other"),
            )
        )
        + serialize_runtime_step_event_jsonl(
            _event("step_failed", "five", 5, provider, "api_error")
        ),
        encoding="utf-8",
    )
    return state_path, events_path


def stop_values(tmp_path: Path, result_kind: str) -> tuple[dict[str, object], object]:
    values = setup(tmp_path)
    if result_kind == "complete":
        state, events = succeeded_targets(tmp_path)
        result: object = WorkflowProgressionDecision(
            "workflow_complete", "w", "five", 5, "e", None, None, None, "last_step_succeeded"
        )
    else:
        state, events = failed_targets(tmp_path)
        result = PersistedExecutionOutcome("persisted_failure", "w", "five", 5, "e", "api_error")
    values.update(
        result=result,
        state_path=state,
        events_path=events,
        start=None,
        employee=None,
        resolved_tools=None,
        api_key=None,
        approval=None,
        transport=None,
    )
    return values, result


def test_source_audit_uses_only_public_phase133() -> None:
    source = Path(
        "src/ai_office/engine/persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert "route_persisted_running_execution_cycle_handoff_chain_bridge_reentry_continuation_boundary" in source
    assert "phase126" not in source.lower()
    assert "route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary" not in source
    assert "route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


def test_public_signature_default_and_canonical_ten_argument_identity(tmp_path: Path) -> None:
    function = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    params = list(inspect.signature(function).parameters.values())
    assert [param.name for param in params[:10]] == [
        "result", "start", "workflow", "employee", "state_path", "events_path",
        "resolved_tools", "api_key", "approval", "transport",
    ]
    assert all(param.annotation is object for param in params[:10])
    assert params[10].kind is inspect.Parameter.KEYWORD_ONLY
    values = setup(tmp_path)
    seen: list[tuple[object, ...]] = []
    expected = runtime_success()

    def dependency(*args: object) -> object:
        seen.append(args)
        return expected

    actual = function(**values, phase133_function=dependency)  # type: ignore[arg-type]
    assert actual is expected and len(seen) == 1
    assert all(
        left is right
        for left, right in zip(
            seen[0],
            tuple(values[name] for name in (
                "result", "start", "workflow", "employee", "state_path", "events_path",
                "resolved_tools", "api_key", "approval", "transport",
            )),
            strict=True,
        )
    )


def test_default_dependency_identity_and_non_callable_rejection(tmp_path: Path) -> None:
    parameter = inspect.signature(
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    ).parameters["phase133_function"]
    from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
        route_persisted_running_execution_cycle_handoff_chain_bridge_reentry_continuation_boundary,
    )
    assert parameter.default is route_persisted_running_execution_cycle_handoff_chain_bridge_reentry_continuation_boundary
    reject(setup(tmp_path), "execution_inputs", phase133_function=object())


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_continuation_indices_one_through_four_are_rejected_before_phase133(
    tmp_path: Path, index: int
) -> None:
    values = setup(tmp_path)
    step = values["workflow"].steps[index - 1]  # type: ignore[union-attr]
    state = WorkflowExecutionState(
        "w", "running", step.id, index, "e", tuple(item.id for item in values["workflow"].steps[: index - 1]), None  # type: ignore[union-attr]
    )
    values["start"] = PreparedStepExecutionStart(
        ModelInvocationRequest("model", "system", step.instructions, ("tool",)), state
    )
    values["state_path"].write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")  # type: ignore[union-attr]
    values["result"] = RunningStatePersistenceResult(len(values["state_path"].read_bytes()))  # type: ignore[union-attr]
    reject(values, "start_contract")


def test_valid_execution_route_delegates_once_with_identity(tmp_path: Path) -> None:
    values = setup(tmp_path)
    expected = runtime_success()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    actual = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values, phase133_function=dependency  # type: ignore[arg-type]
    )
    assert actual is expected and calls == 1


def test_execution_route_leaves_targets_byte_for_byte_unchanged(tmp_path: Path) -> None:
    values = setup(tmp_path)
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    actual = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values, phase133_function=lambda *_: runtime_success()  # type: ignore[arg-type]
    )
    assert isinstance(actual, StepRuntimeExecutionSuccess)
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("output_text", ["", "output"])
def test_immediate_predecessor_empty_and_non_empty_output_text_are_accepted(
    tmp_path: Path, output_text: str
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        _event("step_succeeded", "four", 4, "openai", output_text=output_text)
    )
    events.write_text("".join(lines[:3]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    actual = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values, phase133_function=lambda *_: runtime_success()  # type: ignore[arg-type]
    )
    assert isinstance(actual, StepRuntimeExecutionSuccess)


def test_earlier_empty_output_text_survives_later_succeeded_predecessor(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    content = "".join(
        serialize_runtime_step_event_jsonl(
            _event("step_succeeded", step_id, index, provider, output_text=output_text)
        )
        for step_id, index, provider, output_text in (
            ("one", 1, "other", ""),
            ("two", 2, "other", "output"),
            ("three", 3, "other", ""),
            ("four", 4, "openai", "output"),
        )
    )
    events.write_text(content, encoding="utf-8")  # type: ignore[union-attr]
    actual = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values, phase133_function=lambda *_: runtime_success()  # type: ignore[arg-type]
    )
    assert isinstance(actual, StepRuntimeExecutionSuccess)


@pytest.mark.parametrize("output_text", [4, ["x"], None])
def test_predecessor_output_text_non_string_is_rejected(tmp_path: Path, output_text: object) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        _event("step_succeeded", "four", 4, "openai", output_text=output_text)
    )
    events.write_text("".join(lines[:3]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "persistence_result_contract")


@pytest.mark.parametrize("response_id", ["", 4, ["x"]])
def test_predecessor_response_id_empty_and_non_string_is_rejected(
    tmp_path: Path, response_id: object
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        _event("step_succeeded", "four", 4, "openai", response_id=response_id)
    )
    events.write_text("".join(lines[:3]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "persistence_result_contract")


@pytest.mark.parametrize("request_id", ["", 4, ["x"]])
def test_predecessor_request_id_empty_and_non_string_is_rejected(
    tmp_path: Path, request_id: object
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        _event("step_succeeded", "four", 4, "openai", request_id=request_id)
    )
    events.write_text("".join(lines[:3]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "persistence_result_contract")


def test_immediate_predecessor_none_request_id_delegates_once_in_canonical_order(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        _event("step_succeeded", "four", 4, "openai", request_id=None)
    )
    events.write_text("".join(lines[:3]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    state, events_path = values["state_path"], values["events_path"]
    before = state.read_bytes(), events_path.read_bytes()  # type: ignore[union-attr]
    seen: list[tuple[object, ...]] = []
    expected = runtime_success()

    def dependency(*args: object) -> object:
        seen.append(args)
        return expected

    actual = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values, phase133_function=dependency  # type: ignore[arg-type]
    )
    assert actual is expected and len(seen) == 1
    assert all(
        left is right
        for left, right in zip(
            seen[0],
            tuple(values[name] for name in (
                "result", "start", "workflow", "employee", "state_path", "events_path",
                "resolved_tools", "api_key", "approval", "transport",
            )),
            strict=True,
        )
    )
    assert (state.read_bytes(), events_path.read_bytes()) == before  # type: ignore[union-attr]


def test_immediate_predecessor_none_request_id_returns_exact_runtime_result_unchanged(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        _event("step_succeeded", "four", 4, "openai", request_id=None)
    )
    events.write_text("".join(lines[:3]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime_failure()

    actual = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values, phase133_function=dependency  # type: ignore[arg-type]
    )
    assert isinstance(actual, StepRuntimeExecutionFailure)
    assert calls == 1


def test_earlier_predecessor_none_request_id_is_rejected_before_phase133(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        _event("step_succeeded", "two", 2, "other", request_id=None)
    )
    events.write_text(lines[0] + replacement + lines[2] + lines[3], encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "persistence_result_contract")


def test_earlier_predecessor_exact_non_empty_request_ids_remain_accepted(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    replacements = {
        "one": _event("step_succeeded", "one", 1, "other", request_id="req-1"),
        "two": _event("step_succeeded", "two", 2, "other", request_id="req-2"),
        "three": _event("step_succeeded", "three", 3, "other", request_id="req-3"),
        "four": _event("step_succeeded", "four", 4, "openai", request_id="req-4"),
    }
    events.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(replacements[step_id])
            for step_id in ("one", "two", "three", "four")
        ),
        encoding="utf-8",
    )  # type: ignore[union-attr]
    actual = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values, phase133_function=lambda *_: runtime_success()  # type: ignore[arg-type]
    )
    assert isinstance(actual, StepRuntimeExecutionSuccess)


@pytest.mark.parametrize("provider", ["other", "", 4])
def test_immediate_predecessor_provider_must_be_exact_openai(
    tmp_path: Path, provider: object
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        _event("step_succeeded", "four", 4, provider)
    )
    events.write_text("".join(lines[:3]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "persistence_result_contract")


def test_earlier_predecessor_provider_is_not_unnecessarily_restricted(tmp_path: Path) -> None:
    values = setup(tmp_path)
    actual = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values, phase133_function=lambda *_: runtime_success()  # type: ignore[arg-type]
    )
    assert isinstance(actual, StepRuntimeExecutionSuccess)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"])
def test_predecessor_history_matrix_is_rejected_before_phase133(
    tmp_path: Path, mutation: str
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    extra = serialize_runtime_step_event_jsonl(_event("step_succeeded", "five", 5, "openai"))
    unrelated = serialize_runtime_step_event_jsonl(_event("step_succeeded", "unrelated", 99, "openai"))
    if mutation == "duplicate":
        content = "".join(lines + [lines[-1]])
    elif mutation == "missing":
        content = "".join(lines[:-1])
    elif mutation == "reordered":
        content = lines[1] + lines[0] + lines[2] + lines[3]
    elif mutation == "unrelated":
        content = unrelated + lines[1] + lines[2] + lines[3]
    elif mutation == "malformed":
        content = "{malformed}\n"
    else:
        content = "".join(lines) + extra
    events.write_text(content, encoding="utf-8")  # type: ignore[union-attr]
    before = (values["state_path"].read_bytes(), events.read_bytes())  # type: ignore[union-attr]
    reject(values, "persistence_result_contract")
    assert (values["state_path"].read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "field, value, classification",
    [
        ("result", ResultChild(1), "result_type"),
        ("workflow", WorkflowChild.model_validate(workflow().model_dump()), "workflow_definition"),
        ("employee", EmployeeChild.model_validate(employee().model_dump()), "employee_contract"),
        ("resolved_tools", (ToolChild("tool", "Tool", ()),), "tools_contract"),
        ("api_key", KeyChild(value=SecretStr("synthetic")), "credential_contract"),
    ],
)
def test_top_level_subclasses_are_rejected_before_phase133(
    tmp_path: Path, field: str, value: object, classification: str
) -> None:
    values = setup(tmp_path)
    values[field] = value
    reject(values, classification)


@pytest.mark.parametrize(
    "field, value, classification",
    [
        ("result", SimpleNamespace(state_bytes_written=1), "result_type"),
        ("workflow", SimpleNamespace(id="w", name="W", description="D", steps=workflow().steps), "workflow_definition"),
        ("employee", SimpleNamespace(id="e", name="E", role="R", instructions="system", model="model", allowed_tools=["tool"]), "employee_contract"),
        ("resolved_tools", [ToolDefinition("tool", "Tool", ())], "tools_contract"),
        ("api_key", SimpleNamespace(value=SecretStr("synthetic")), "credential_contract"),
        ("approval", SimpleNamespace(approved=True), "approval_contract"),
    ],
)
def test_attribute_compatible_input_substitutes_are_rejected(
    tmp_path: Path, field: str, value: object, classification: str
) -> None:
    values = setup(tmp_path)
    values[field] = value
    reject(values, classification)


def test_start_request_and_running_state_subclasses_are_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path)
    request = values["start"].request  # type: ignore[union-attr]
    running = values["start"].running_state  # type: ignore[union-attr]
    cases = [
        (StartChild(request, running), "start_contract"),
        (PreparedStepExecutionStart(RequestChild(request.model, request.system_instructions, request.task_instructions, request.allowed_tools), running), "start_contract"),
        (PreparedStepExecutionStart(request, StateChild(running.workflow_id, running.status, running.current_step_id, running.current_step_index, running.current_employee_id, running.completed_step_ids, running.last_failure_category)), "start_contract"),
    ]
    for start, classification in cases:
        reject(values, classification, start=start)


def test_nested_request_fully_attribute_compatible_substitute_is_rejected(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    request = values["start"].request  # type: ignore[union-attr]
    substitute = SimpleNamespace(
        model=request.model,
        system_instructions=request.system_instructions,
        task_instructions=request.task_instructions,
        allowed_tools=request.allowed_tools,
    )
    values["start"] = PreparedStepExecutionStart(
        substitute, values["start"].running_state  # type: ignore[union-attr]
    )
    reject_unchanged(values, "start_contract")


def test_nested_running_state_fully_attribute_compatible_substitute_is_rejected(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    running = values["start"].running_state  # type: ignore[union-attr]
    substitute = SimpleNamespace(
        workflow_id=running.workflow_id,
        status=running.status,
        current_step_id=running.current_step_id,
        current_step_index=running.current_step_index,
        current_employee_id=running.current_employee_id,
        completed_step_ids=running.completed_step_ids,
        last_failure_category=running.last_failure_category,
    )
    values["start"] = PreparedStepExecutionStart(
        values["start"].request, substitute  # type: ignore[union-attr]
    )
    reject_unchanged(values, "start_contract")


def test_workflow_step_subclass_is_rejected_inside_exact_workflow(tmp_path: Path) -> None:
    values = setup(tmp_path)
    exact_step = values["workflow"].steps[4]  # type: ignore[union-attr]
    child_step = StepChild.model_validate(exact_step.model_dump())
    values["workflow"] = WorkflowDefinition.model_construct(
        id=values["workflow"].id,  # type: ignore[union-attr]
        name=values["workflow"].name,  # type: ignore[union-attr]
        description=values["workflow"].description,  # type: ignore[union-attr]
        steps=[*values["workflow"].steps[:4], child_step],  # type: ignore[union-attr]
    )
    reject(values, "workflow_definition")


def test_workflow_step_fully_attribute_compatible_substitute_is_rejected(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    exact_workflow = values["workflow"]
    exact_step = exact_workflow.steps[4]  # type: ignore[union-attr]
    substitute = SimpleNamespace(
        id=exact_step.id,
        name=exact_step.name,
        employee=exact_step.employee,
        instructions=exact_step.instructions,
    )
    values["workflow"] = WorkflowDefinition.model_construct(
        id=exact_workflow.id,  # type: ignore[union-attr]
        name=exact_workflow.name,  # type: ignore[union-attr]
        description=exact_workflow.description,  # type: ignore[union-attr]
        steps=[*exact_workflow.steps[:4], substitute],  # type: ignore[union-attr]
    )
    reject_unchanged(values, "workflow_definition")


def test_approval_subclass_is_rejected_before_phase133(tmp_path: Path) -> None:
    values = setup(tmp_path)
    approval = values["approval"]
    values["approval"] = ApprovalChild(
        approval.approved,
        approval.provider,
        approval.request_fingerprint,
        approval.approved_by,
        approval.approval_id,
    )
    reject(values, "approval_contract")


def test_approval_fully_attribute_compatible_substitute_is_rejected(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    approval = values["approval"]
    values["approval"] = SimpleNamespace(
        approved=approval.approved,
        provider=approval.provider,
        request_fingerprint=approval.request_fingerprint,
        approved_by=approval.approved_by,
        approval_id=approval.approval_id,
    )
    reject_unchanged(values, "approval_contract")


def test_start_fully_attribute_compatible_substitute_is_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path)
    exact = values["start"]
    values["start"] = SimpleNamespace(request=exact.request, running_state=exact.running_state)
    reject(values, "start_contract")


@pytest.mark.parametrize("field", ["model", "system_instructions", "task_instructions", "allowed_tools"])
def test_request_semantic_linkage_is_strict(tmp_path: Path, field: str) -> None:
    values = setup(tmp_path)
    request = values["start"].request
    replacement = {"model": "wrong-model", "system_instructions": "wrong", "task_instructions": "wrong", "allowed_tools": ("tool", "wrong-tool")} [field]  # type: ignore[index]
    values["start"] = PreparedStepExecutionStart(replace(request, **{field: replacement}), values["start"].running_state)
    reject(values, "start_contract")


@pytest.mark.parametrize("field", ["id", "instructions", "model", "allowed_tools"])
def test_employee_semantic_linkage_is_strict(tmp_path: Path, field: str) -> None:
    values = setup(tmp_path)
    replacement = {"id": "other", "instructions": "wrong", "model": "wrong", "allowed_tools": ["tool", "wrong-tool"]}[field]
    values["employee"] = values["employee"].model_copy(update={field: replacement})  # type: ignore[union-attr]
    reject(values, "start_contract" if field != "id" else "start_contract")


@pytest.mark.parametrize("kind", ["request", "state"])
def test_nested_exact_type_and_field_contract_is_rechecked(tmp_path: Path, kind: str) -> None:
    values = setup(tmp_path)
    if kind == "request":
        request = values["start"].request
        values["start"] = PreparedStepExecutionStart(
            replace(request, task_instructions=object()), values["start"].running_state  # type: ignore[arg-type,union-attr]
        )
    else:
        running = values["start"].running_state
        values["start"] = PreparedStepExecutionStart(
            values["start"].request, replace(running, current_step_index=True)  # type: ignore[arg-type,union-attr]
        )
    reject(values, "start_contract")


def test_tool_parameters_are_exact_models(tmp_path: Path) -> None:
    values = setup(tmp_path)
    values["resolved_tools"] = (ToolDefinition("tool", "Tool", (ParameterChild("p", "", "string", True),)),)
    reject(values, "tools_contract")


def test_tool_fully_attribute_compatible_substitute_is_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path)
    exact_tool = values["resolved_tools"][0]
    substitute = SimpleNamespace(
        name=exact_tool.name,
        description=exact_tool.description,
        parameters=exact_tool.parameters,
    )
    values["resolved_tools"] = (substitute,)
    reject_unchanged(values, "tools_contract")


def test_tool_parameter_fully_attribute_compatible_substitute_is_rejected(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    request = values["start"].request  # type: ignore[union-attr]
    parameter = ToolParameterDefinition("parameter", "description", "string", True)
    exact_tool = ToolDefinition("tool", "Tool", (parameter,))
    values["approval"] = approve_model_invocation_execution(
        request,
        (exact_tool,),
        provider="openai",
        approved_by="test",
        approval_id="approval-id",
    )
    substitute = SimpleNamespace(
        name=parameter.name,
        description=parameter.description,
        type=parameter.type,
        required=parameter.required,
    )
    values["resolved_tools"] = (
        ToolDefinition("tool", "Tool", (substitute,)),
    )
    reject_unchanged(values, "tools_contract")


@pytest.mark.parametrize("tool_names", [("tool", "tool"), (), ("tool", "extra")])
def test_resolved_tool_duplicate_missing_and_extra_linkage_is_strict(
    tmp_path: Path, tool_names: tuple[str, ...]
) -> None:
    values = setup(tmp_path)
    values["resolved_tools"] = tuple(
        ToolDefinition(name, "Tool", ()) for name in tool_names
    )
    reject(values, "start_contract")


def test_resolved_tool_order_is_strict(tmp_path: Path) -> None:
    values = setup(tmp_path)
    employee_value = values["employee"].model_copy(  # type: ignore[union-attr]
        update={"allowed_tools": ["one", "two"]}
    )
    request = replace(
        values["start"].request,  # type: ignore[union-attr]
        allowed_tools=("one", "two"),
    )
    tools = (ToolDefinition("two", "Tool", ()), ToolDefinition("one", "Tool", ()))
    values.update(
        employee=employee_value,
        start=PreparedStepExecutionStart(request, values["start"].running_state),  # type: ignore[union-attr]
        resolved_tools=tools,
    )
    reject(values, "start_contract")


@pytest.mark.parametrize(
    "field", ["approved", "provider", "request_fingerprint", "approved_by", "approval_id"]
)
def test_approval_semantic_contract_is_strict(tmp_path: Path, field: str) -> None:
    values = setup(tmp_path)
    replacement = {
        "approved": False,
        "provider": "other",
        "request_fingerprint": "wrong",
        "approved_by": 4,
        "approval_id": 4,
    }[field]
    values["approval"] = replace(values["approval"], **{field: replacement})  # type: ignore[arg-type]
    reject(values, "approval_contract")


@pytest.mark.parametrize("count", [0, -1, True, "wrong", 1])
def test_persistence_byte_count_requires_positive_exact_actual_length(
    tmp_path: Path, count: object
) -> None:
    values = setup(tmp_path)
    if type(count) is int and count == 1:
        count = len(values["state_path"].read_bytes()) + 1  # type: ignore[union-attr]
    values["result"] = RunningStatePersistenceResult(count)  # type: ignore[arg-type]
    reject(values, "persistence_result_contract")


def test_persistence_byte_count_int_subclass_is_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path)
    actual_length = len(values["state_path"].read_bytes())  # type: ignore[union-attr]
    values["result"] = RunningStatePersistenceResult(IntChild(actual_length))
    reject_unchanged(values, "persistence_result_contract")


@pytest.mark.parametrize("nested", ["subclass", "substitute"])
def test_nested_secretstr_exact_type_is_rejected(
    tmp_path: Path, nested: str
) -> None:
    values = setup(tmp_path)
    nested_value = (
        SecretChild("synthetic")
        if nested == "subclass"
        else SecretCompatible("synthetic")
    )
    values["api_key"] = OpenAIApiKey.model_construct(value=nested_value)
    reject_unchanged(values, "credential_contract")


def test_non_callable_transport_is_rejected_before_phase133(tmp_path: Path) -> None:
    reject_unchanged(setup(tmp_path), "execution_inputs", transport=object())


def test_mismatched_persisted_running_state_is_rejected_before_phase133(tmp_path: Path) -> None:
    values = setup(tmp_path)
    mismatched = WorkflowExecutionState("w", "running", "five", 5, "e", ("one", "two"), None)
    values["state_path"].write_text(serialize_workflow_execution_state_json(mismatched), encoding="utf-8")  # type: ignore[union-attr]
    values["result"] = RunningStatePersistenceResult(len(values["state_path"].read_bytes()))  # type: ignore[union-attr]
    reject(values, "persistence_result_contract")


@pytest.mark.parametrize(
    "value",
    [
        RuntimeSuccessChild("w", "five", 5, "e", runtime_success().invocation_result),
        RuntimeFailureChild("w", "five", 5, "e", runtime_failure().invocation_result),
        SimpleNamespace(workflow_id="w", step_id="five", step_index=5, employee_id="e"),
    ],
)
def test_runtime_top_level_subclasses_and_substitutes_are_rejected_after_one_call(
    tmp_path: Path, value: object
) -> None:
    values = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return value

    with pytest.raises(
        PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            **values, phase133_function=dependency  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "runtime_contract"
    assert calls == 1


@pytest.mark.parametrize("nested", ["subclass", "substitute"])
def test_runtime_nested_invocation_substitutes_are_rejected_after_one_call(
    tmp_path: Path, nested: str
) -> None:
    values = setup(tmp_path)
    result = runtime_success()
    invocation = result.invocation_result
    nested_value = (
        type("InvocationChild", (type(invocation),), {})(*invocation.__dict__.values())
        if nested == "subclass"
        else SimpleNamespace(**invocation.__dict__)
    )
    malformed = replace(result, invocation_result=nested_value)  # type: ignore[arg-type]
    reject_after_call(values, malformed, "runtime_contract")


@pytest.mark.parametrize("field", ["text_parts", "text"])
def test_runtime_nested_success_value_contract_is_strict(tmp_path: Path, field: str) -> None:
    values = setup(tmp_path)
    invocation = runtime_success().invocation_result
    replacement = {"text_parts": ["ok"], "text": "wrong"}[field]
    bad = replace(
        runtime_success(),
        invocation_result=replace(invocation, **{field: replacement}),  # type: ignore[arg-type]
    )
    reject_after_call(values, bad, "runtime_contract")


def test_runtime_nested_failure_type_and_value_contract_is_strict(tmp_path: Path) -> None:
    values = setup(tmp_path)
    invocation = runtime_failure().invocation_result
    child_type = type("FailureChild", (type(invocation),), {})
    subclass = child_type(*invocation.__dict__.values())
    malformed = replace(invocation, category=[])  # type: ignore[arg-type]
    for nested in (subclass, SimpleNamespace(**invocation.__dict__), malformed):
        reject_after_call(
            values,
            replace(runtime_failure(), invocation_result=nested),  # type: ignore[arg-type]
            "runtime_contract",
        )


@pytest.mark.parametrize("field", ["workflow_id", "step_id", "step_index", "employee_id"])
def test_runtime_identity_and_provider_linkage_are_strict(tmp_path: Path, field: str) -> None:
    values = setup(tmp_path)
    replacement = {"workflow_id": "other", "step_id": "other", "step_index": 4, "employee_id": "other"}[field]
    bad = replace(runtime_success(), **{field: replacement})
    reject_after_call(values, bad, "runtime_contract")


@pytest.mark.parametrize("provider", ["other", 4])
def test_runtime_provider_must_be_exact_openai(tmp_path: Path, provider: object) -> None:
    values = setup(tmp_path)
    bad = replace(runtime_success(), invocation_result=replace(runtime_success().invocation_result, provider=provider))  # type: ignore[arg-type]
    reject_after_call(values, bad, "runtime_contract")


@pytest.mark.parametrize("value", [runtime_success(), runtime_failure()])
def test_exact_runtime_success_and_failure_are_returned_by_identity(tmp_path: Path, value: object) -> None:
    values = setup(tmp_path)
    actual = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values, phase133_function=lambda *_: value  # type: ignore[arg-type]
    )
    assert actual is value


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_runtime_return_mutation_is_compensated_without_retry(tmp_path: Path, mutation: str) -> None:
    values = setup(tmp_path)
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"mutated-state")  # type: ignore[union-attr]
        if mutation in ("events", "both"):
            events.write_bytes(b"mutated-events")  # type: ignore[union-attr]
        return runtime_success()

    with pytest.raises(
        PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            **values, phase133_function=dependency  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "runtime_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_malformed_return_mutation_is_compensated_without_retry(tmp_path: Path, mutation: str) -> None:
    values = setup(tmp_path)
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"mutated-state")  # type: ignore[union-attr]
        if mutation in ("events", "both"):
            events.write_bytes(b"mutated-events")  # type: ignore[union-attr]
        return object()

    with pytest.raises(
        PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            **values, phase133_function=dependency  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "runtime_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_safe_phase133_error_identity_is_preserved_and_compensated(
    tmp_path: Path, mutation: str | None
) -> None:
    values = setup(tmp_path)
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    supplied_error = Phase133Error("safe")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"mutated-state")  # type: ignore[union-attr]
        if mutation in ("events", "both"):
            events.write_bytes(b"mutated-events")  # type: ignore[union-attr]
        raise supplied_error

    with pytest.raises(Phase133Error) as caught:
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            **values, phase133_function=dependency  # type: ignore[arg-type]
        )
    assert caught.value is supplied_error and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_unexpected_phase133_error_is_sanitized_and_compensated(
    tmp_path: Path, mutation: str | None
) -> None:
    values = setup(tmp_path)
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"mutated-state")  # type: ignore[union-attr]
        if mutation in ("events", "both"):
            events.write_bytes(b"mutated-events")  # type: ignore[union-attr]
        raise RuntimeError("secret detail")

    with pytest.raises(
        PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            **values, phase133_function=dependency  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value) and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once(
    tmp_path: Path, failed_target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = setup(tmp_path)
    state, events = values["state_path"], values["events_path"]
    restore_calls = {"state": 0, "events": 0}
    dependency_calls = 0
    original_write = Path.write_bytes

    def failing_write(path: Path, data: bytes) -> int:
        if failed_target in ("state", "both") and path == state:
            restore_calls["state"] += 1
            raise OSError("state rollback")
        if failed_target in ("events", "both") and path == events:
            restore_calls["events"] += 1
            raise OSError("event rollback")
        restore_calls["state" if path == state else "events"] += 1
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", failing_write)

    def dependency(*_: object) -> object:
        nonlocal dependency_calls
        dependency_calls += 1
        original_write(state, b"mutated-state")
        original_write(events, b"mutated-events")
        raise RuntimeError("unexpected")

    with pytest.raises(
        PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            **values, phase133_function=dependency  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert restore_calls["state"] == 1 and restore_calls["events"] == 1
    assert dependency_calls == 1


@pytest.mark.parametrize("result_kind", ["complete", "failure"])
def test_stop_routes_return_supplied_object_identity_with_provider_other_and_zero_call(
    tmp_path: Path, result_kind: str
) -> None:
    values, result = stop_values(tmp_path, result_kind)
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime_success()

    actual = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values, phase133_function=dependency  # type: ignore[arg-type]
    )
    assert actual is result and calls == 0
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("result_kind", ["complete", "failure"])
def test_stop_subclasses_and_attribute_compatible_substitutes_are_zero_call_rejected(
    tmp_path: Path, result_kind: str
) -> None:
    values, result = stop_values(tmp_path, result_kind)
    if result_kind == "complete":
        child = type("DecisionChild", (type(result),), {})(*result.__dict__.values())
        substitute = SimpleNamespace(**result.__dict__)
        classification = "result_type"
    else:
        child = type("OutcomeChild", (type(result),), {})(*result.__dict__.values())
        substitute = SimpleNamespace(**result.__dict__)
        classification = "result_type"
    for replacement in (child, substitute):
        reject(values, classification, result=replacement)


@pytest.mark.parametrize("result_kind", ["complete", "failure"])
def test_explicit_stop_result_subclasses_are_zero_call_rejected(
    tmp_path: Path, result_kind: str
) -> None:
    values, result = stop_values(tmp_path, result_kind)
    replacement = (
        DecisionChild(*result.__dict__.values())
        if result_kind == "complete"
        else OutcomeChild(*result.__dict__.values())
    )
    reject(values, "result_type", result=replacement)


@pytest.mark.parametrize("result_kind", ["complete", "failure"])
def test_stop_current_step_index_bool_and_int_subclass_are_zero_call_rejected(
    tmp_path: Path, result_kind: str
) -> None:
    values, result = stop_values(tmp_path, result_kind)
    classification = "completion_contract" if result_kind == "complete" else "failure_contract"
    for replacement in (True, IntChild(5)):
        reject(
            values,
            classification,
            result=replace(result, current_step_index=replacement),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("result_kind", ["complete", "failure"])
@pytest.mark.parametrize("field", ["start", "employee", "resolved_tools", "api_key", "approval", "transport"])
def test_each_execution_input_is_rejected_on_each_stop_route(
    tmp_path: Path, result_kind: str, field: str
) -> None:
    values, _ = stop_values(tmp_path, result_kind)
    replacement = (lambda _: None) if field == "transport" else setup(tmp_path)[field]
    reject(values, "execution_inputs", **{field: replacement})


@pytest.mark.parametrize("decision", ["persisted_success", "stopped_failed", "not_progressable"])
def test_unsupported_progression_is_zero_call_rejected(tmp_path: Path, decision: str) -> None:
    values = setup(tmp_path)
    result = WorkflowProgressionDecision(decision, "w", "five", 5, "e", None, None, None, "reason")  # type: ignore[arg-type]
    reject(values, "completion_contract", result=result)


@pytest.mark.parametrize("value", [runtime_success(), runtime_failure(), WorkflowExecutionPersistenceResult(Path("s"), Path("e"), 1, 1)])
def test_direct_non_phase139_results_are_zero_call_rejected(tmp_path: Path, value: object) -> None:
    reject(setup(tmp_path), "result_type", result=value)


@pytest.mark.parametrize("field", ["decision", "current_step_index", "reason"])
def test_malformed_completion_is_zero_call_rejected(tmp_path: Path, field: str) -> None:
    values, result = stop_values(tmp_path, "complete")
    replacement = {"decision": "wrong", "current_step_index": True, "reason": 4}[field]
    bad = replace(result, **{field: replacement})
    reject(values, "completion_contract", result=bad)


@pytest.mark.parametrize("field", ["current_step_index", "failure_category"])
def test_malformed_failure_is_zero_call_rejected(tmp_path: Path, field: str) -> None:
    values, result = stop_values(tmp_path, "failure")
    replacement = {"current_step_index": True, "failure_category": object()}[field]
    bad = replace(result, **{field: replacement})
    reject(values, "failure_contract", result=bad)


def test_workflow_complete_stop_with_empty_terminal_output_is_zero_call_rejected(
    tmp_path: Path,
) -> None:
    values, _ = stop_values(tmp_path, "complete")
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        _event("step_succeeded", "five", 5, "other", output_text="")
    )
    events.write_text("".join(lines[:4]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    reject(values, "terminal_contract")


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_missing_and_directory_targets_are_rejected(tmp_path: Path, target: str) -> None:
    values = setup(tmp_path)
    path = values[target]
    path.unlink()  # type: ignore[union-attr]
    reject(values, "state_target" if target == "state_path" else "event_target")
    path.mkdir()
    reject(values, "state_target" if target == "state_path" else "event_target")


def test_target_conflict_is_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path)
    reject(values, "target_conflict", events_path=values["state_path"])


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_is_file_and_read_bytes_oserror_are_target_safe(
    tmp_path: Path, target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = setup(tmp_path)
    selected = values[target]
    original_is_file = Path.is_file

    def raising_is_file(path: Path) -> bool:
        if path == selected:
            raise OSError("unreadable")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", raising_is_file)
    reject(values, "state_target" if target == "state_path" else "event_target")


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_read_bytes_oserror_is_target_safe(
    tmp_path: Path, target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = setup(tmp_path)
    selected = values[target]
    original_read_bytes = Path.read_bytes

    def raising_read_bytes(path: Path) -> bytes:
        if path == selected:
            raise OSError("unreadable")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", raising_read_bytes)
    reject(values, "state_target" if target == "state_path" else "event_target")
