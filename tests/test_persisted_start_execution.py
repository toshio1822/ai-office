"""Tests for the persisted-start Phase 29 execution boundary."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.engine import (
    PersistedStartExecutionCompatibilityError,
    PreparedStepExecutionStart,
    execute_persisted_start_openai_step,
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
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionInput,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import serialize_workflow_execution_state_json
from ai_office.tools import ToolDefinition


def request(tools: tuple[str, ...] = ("search",)) -> ModelInvocationRequest:
    return ModelInvocationRequest("model", "system", "task", tools)


def state(**changes: object) -> WorkflowExecutionState:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "status": "running",
        "current_step_id": "step",
        "current_step_index": 2,
        "current_employee_id": "employee",
        "completed_step_ids": ("first",),
        "last_failure_category": None,
    }
    values.update(changes)
    return WorkflowExecutionState(**values)  # type: ignore[arg-type]


def start(**changes: object) -> PreparedStepExecutionStart:
    values: dict[str, object] = {"request": request(), "running_state": state()}
    values.update(changes)
    return PreparedStepExecutionStart(**values)  # type: ignore[arg-type]


def employee(**changes: object) -> EmployeeDefinition:
    values: dict[str, object] = {
        "id": "employee",
        "name": "Employee",
        "role": "Role",
        "instructions": "system",
        "model": "model",
        "allowed_tools": ["search"],
    }
    values.update(changes)
    return EmployeeDefinition(**values)


def key() -> OpenAIApiKey:
    return OpenAIApiKey(value=SecretStr("synthetic-key"))


def write_state(path: Path, value: WorkflowExecutionState) -> bytes:
    contents = serialize_workflow_execution_state_json(value).encode()
    path.write_bytes(contents)
    return contents


def approval(value: ModelInvocationRequest) -> ModelInvocationExecutionApproval:
    return approve_model_invocation_execution(
        value, (), provider="openai", approved_by="reviewer", approval_id="approval"
    )


def test_exact_persisted_state_delegates_once_with_exact_phase_21_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    value = start()
    original = write_state(target, value.running_state)
    captured: dict[str, object] = {}
    result = StepRuntimeExecutionSuccess(
        "workflow",
        "step",
        2,
        "employee",
        ModelInvocationSuccess("openai", "response", None, "completed", (), ""),
    )

    def execute(
        input_value: StepRuntimeExecutionInput,
        api: OpenAIApiKey,
        *,
        transport: object,
    ) -> object:
        captured.update(input=input_value, api=api, transport=transport)
        return result

    monkeypatch.setattr(
        "ai_office.runtime.persisted_start_execution.execute_openai_runtime_step",
        execute,
    )
    tools: tuple[ToolDefinition, ...] = ()

    def transport(_: object) -> None:
        return None

    actual = execute_persisted_start_openai_step(
        value,
        target,
        employee(),
        tools,
        key(),
        approval(value.request),
        transport=transport,
    )

    assert actual is result
    assert isinstance(captured["input"], StepRuntimeExecutionInput)
    invocation = captured["input"]
    assert invocation.invocation_request is value.request
    assert invocation.resolved_tools is tools
    assert invocation.step_request.model == "model"
    assert invocation.step_request.workflow_id == "workflow"
    assert invocation.step_request.step_id == "step"
    assert invocation.step_request.step_index == 2
    assert invocation.step_request.employee_id == "employee"
    assert invocation.step_request.employee_name == "Employee"
    assert invocation.step_request.employee_role == "Role"
    assert invocation.step_request.workflow_name is None
    assert invocation.step_request.step_name is None
    assert target.read_bytes() == original
    assert not (tmp_path / "events.jsonl").exists()


def test_phase_21_failure_is_returned_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    value = start()
    write_state(target, value.running_state)
    result = StepRuntimeExecutionFailure(
        "workflow", "step", 2, "employee",
        ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None),
    )
    monkeypatch.setattr(
        "ai_office.runtime.persisted_start_execution.execute_openai_runtime_step",
        lambda *_args, **_kwargs: result,
    )
    assert execute_persisted_start_openai_step(
        value, target, employee(), (), key(), approval(value.request)
    ) is result


@pytest.mark.parametrize(
    ("persisted", "classification"),
    [
        (state(status="ready"), "state_status"),
        (state(status="succeeded"), "state_status"),
        (state(status="failed", last_failure_category="api_error"), "state_status"),
        (state(workflow_id="stale"), "state_identity"),
        (state(current_step_id="stale"), "state_identity"),
        (state(current_step_index=3), "state_identity"),
        (state(current_employee_id="stale"), "state_identity"),
        (state(completed_step_ids=()), "state_identity"),
        (state(last_failure_category="api_error"), "state_identity"),
    ],
)
def test_invalid_persisted_states_reject_before_delegation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    persisted: WorkflowExecutionState,
    classification: str,
) -> None:
    target = tmp_path / "state.json"
    value = start()
    write_state(target, persisted)
    calls = 0

    def execute(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    monkeypatch.setattr(
        "ai_office.runtime.persisted_start_execution.execute_openai_runtime_step",
        execute,
    )
    with pytest.raises(PersistedStartExecutionCompatibilityError) as error:
        execute_persisted_start_openai_step(
            value, target, employee(), (), key(), approval(value.request)
        )
    assert error.value.detail.classification == classification
    assert calls == 0


@pytest.mark.parametrize(
    ("employee_value", "classification"),
    [
        (employee(id="other"), "employee_identity"),
        (employee(instructions="other"), "employee_contract"),
        (employee(model="other"), "employee_contract"),
        (employee(allowed_tools=[]), "employee_contract"),
    ],
)
def test_employee_mismatch_rejects_before_delegation(
    tmp_path: Path, employee_value: EmployeeDefinition, classification: str
) -> None:
    target = tmp_path / "state.json"
    value = start()
    write_state(target, value.running_state)
    with pytest.raises(PersistedStartExecutionCompatibilityError) as error:
        execute_persisted_start_openai_step(
            value, target, employee_value, (), key(), approval(value.request)
        )
    assert error.value.detail.classification == classification


@pytest.mark.parametrize(
    ("value", "classification"),
    [
        (object(), "start_type"),
        (start(request=object()), "request_data"),
        (start(running_state=object()), "state_identity"),
    ],
)
def test_synthetic_start_values_are_safe(
    tmp_path: Path, value: object, classification: str
) -> None:
    target = tmp_path / "state.json"
    with pytest.raises(PersistedStartExecutionCompatibilityError) as error:
        execute_persisted_start_openai_step(
            value, target, employee(), (), key(), approval(request())
        )
    assert error.value.detail.classification == classification
    assert str(target) not in str(error.value)


def test_missing_and_malformed_state_are_rejected(tmp_path: Path) -> None:
    value = start()
    for target, contents, expected in (
        (tmp_path / "missing.json", None, "state_target"),
        (tmp_path / "invalid.json", b"not json", "state_data"),
        (tmp_path / "utf8.json", b"\xff", "state_data"),
    ):
        if contents is not None:
            target.write_bytes(contents)
        with pytest.raises(PersistedStartExecutionCompatibilityError) as error:
            execute_persisted_start_openai_step(
                value, target, employee(), (), key(), approval(value.request)
            )
        assert error.value.detail.classification == expected
