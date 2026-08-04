"""Tests for Phase 35 with fake Phase 29 execution."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedRunningExecutionReentryCompatibilityError,
    PersistedStartExecutionCompatibilityError,
    PreparedStepExecutionStart,
    execute_persisted_running_openai_step,
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
from ai_office.storage import serialize_workflow_execution_state_json
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


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "workflow",
        "step",
        2,
        "employee",
        ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None),
    )


def key() -> OpenAIApiKey:
    return OpenAIApiKey(value=SecretStr("synthetic"))


def tools() -> tuple[ToolDefinition, ...]:
    return (ToolDefinition("tool", "Tool", ()),)


def approval(resolved_tools: tuple[ToolDefinition, ...]):
    return approve_model_invocation_execution(
        start().request,
        resolved_tools,
        provider="openai",
        approved_by="test",
        approval_id="id",
    )


def test_delegates_once_returns_exact_result_and_keeps_state(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    before = serialize_workflow_execution_state_json(start().running_state).encode()
    target.write_bytes(before)
    calls = 0
    expected = failure()

    def execute(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    result = execute_persisted_running_openai_step(
        start(),
        target,
        workflow(),
        employee(),
        tools(),
        key(),
        approval(tools()),
        transport=lambda _: None,
        execution_function=execute,
    )  # type: ignore[arg-type]
    assert result is expected
    assert calls == 1
    assert target.read_bytes() == before


def test_mutated_state_is_restored_after_one_call(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    before = serialize_workflow_execution_state_json(start().running_state).encode()
    target.write_bytes(before)

    def execute(*_args: object, **_kwargs: object) -> object:
        target.write_bytes(b"changed")
        return failure()

    with pytest.raises(PersistedRunningExecutionReentryCompatibilityError) as caught:
        execute_persisted_running_openai_step(
            start(),
            target,
            workflow(),
            employee(),
            tools(),
            key(),
            approval(tools()),
            transport=lambda _: None,
            execution_function=execute,
        )  # type: ignore[arg-type]
    assert caught.value.detail.classification == "state_immutability"
    assert target.read_bytes() == before


def _execute(
    target: Path,
    *,
    value: PreparedStepExecutionStart | None = None,
    result: object | None = None,
    resolved_tools: tuple[ToolDefinition, ...] | None = None,
    api_key: object | None = None,
    execution_approval: object | None = None,
    execution_function: object | None = None,
) -> tuple[object, int]:
    value = value or start()
    resolved_tools = tools() if resolved_tools is None else resolved_tools
    target.write_bytes(serialize_workflow_execution_state_json(value.running_state).encode())
    calls = 0

    def execute(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return failure() if result is None else result

    with pytest.raises(PersistedRunningExecutionReentryCompatibilityError) as caught:
        execute_persisted_running_openai_step(
            value,
            target,
            workflow(),
            employee(),
            resolved_tools,
            key() if api_key is None else api_key,
            (
                approval(resolved_tools)
                if execution_approval is None
                else execution_approval
            ),
            transport=lambda _: None,
            execution_function=(
                execute if execution_function is None else execution_function
            ),
        )  # type: ignore[arg-type]
    return caught.value, calls


@pytest.mark.parametrize(
    "completed",
    [
        (),
        ("step",),
        ("first", "step"),
        ("first", "unknown"),
        ("first", "first", "unknown"),
    ],
)
def test_completed_steps_must_be_exact_prior_workflow_prefix(
    tmp_path: Path, completed: tuple[str, ...]
) -> None:
    value = replace(
        start(),
        running_state=replace(start().running_state, completed_step_ids=completed),
    )
    error, calls = _execute(tmp_path / "state.json", value=value)
    assert error.detail.classification == "workflow_identity"
    assert calls == 0


@pytest.mark.parametrize("mode", ["append", "truncate", "replace", "delete"])
def test_all_state_mutations_restore_and_raise_safe_error(
    tmp_path: Path, mode: str
) -> None:
    target = tmp_path / "state.json"
    before = serialize_workflow_execution_state_json(start().running_state).encode()
    target.write_bytes(before)

    def execute(*_args: object, **_kwargs: object) -> object:
        if mode == "append":
            target.write_bytes(before + b" ")
        elif mode == "truncate":
            target.write_bytes(b"")
        elif mode == "replace":
            target.unlink()
            target.write_bytes(b"replacement")
        else:
            target.unlink()
        return failure()

    with pytest.raises(PersistedRunningExecutionReentryCompatibilityError) as caught:
        execute_persisted_running_openai_step(
            start(), target, workflow(), employee(), tools(), key(), approval(tools()),
            transport=lambda _: None, execution_function=execute
        )  # type: ignore[arg-type]
    assert caught.value.detail.classification == "state_immutability"
    assert target.read_bytes() == before


def test_phase_29_error_is_rethrown_as_the_same_object_after_restore(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state.json"
    before = serialize_workflow_execution_state_json(start().running_state).encode()
    target.write_bytes(before)
    expected = RuntimeError("safe phase 29 error")

    def execute(*_args: object, **_kwargs: object) -> object:
        target.write_bytes(b"changed")
        raise expected

    with pytest.raises(RuntimeError) as caught:
        execute_persisted_running_openai_step(
            start(), target, workflow(), employee(), tools(), key(), approval(tools()),
            transport=lambda _: None, execution_function=execute
        )  # type: ignore[arg-type]
    assert caught.value is expected
    assert target.read_bytes() == before


def test_existing_phase_29_safe_error_keeps_its_identity(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    expected = PersistedStartExecutionCompatibilityError("state_data")

    def execute(*_args: object, **_kwargs: object) -> object:
        raise expected

    target.write_bytes(serialize_workflow_execution_state_json(start().running_state).encode())
    with pytest.raises(PersistedStartExecutionCompatibilityError) as caught:
        execute_persisted_running_openai_step(
            start(), target, workflow(), employee(), tools(), key(), approval(tools()),
            transport=lambda _: None, execution_function=execute
        )  # type: ignore[arg-type]
    assert caught.value is expected


def test_restore_failure_raises_state_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(serialize_workflow_execution_state_json(start().running_state).encode())

    def execute(*_args: object, **_kwargs: object) -> object:
        target.unlink()
        return failure()

    monkeypatch.setattr(
        Path, "write_bytes", lambda *_args: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(PersistedRunningExecutionReentryCompatibilityError) as caught:
        execute_persisted_running_openai_step(
            start(), target, workflow(), employee(), tools(), key(), approval(tools()),
            transport=lambda _: None, execution_function=execute
        )  # type: ignore[arg-type]
    assert caught.value.detail.classification == "state_rollback"


@pytest.mark.parametrize("contents", [None, b"not json"])
def test_missing_or_malformed_state_rejects_before_phase_29(
    tmp_path: Path, contents: bytes | None
) -> None:
    target = tmp_path / "state.json"
    if contents is not None:
        target.write_bytes(contents)
    calls = 0

    def execute(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return failure()

    with pytest.raises(PersistedRunningExecutionReentryCompatibilityError):
        execute_persisted_running_openai_step(
            start(), target, workflow(), employee(), tools(), key(), approval(tools()),
            transport=lambda _: None, execution_function=execute
        )  # type: ignore[arg-type]
    assert calls == 0


@pytest.mark.parametrize("state_change", [
    {"status": "ready"}, {"status": "succeeded"}, {"status": "failed"},
    {"last_failure_category": "api_error"}, {"current_step_id": "first"},
    {"current_step_index": 1}, {"current_employee_id": "other"},
])
def test_invalid_persisted_state_rejects_before_phase_29(
    tmp_path: Path, state_change: dict[str, object]
) -> None:
    value = replace(
        start(), running_state=replace(start().running_state, **state_change)
    )
    _error, calls = _execute(tmp_path / "state.json", value=value)
    assert calls == 0


def test_persisted_and_start_state_mismatch_rejects_before_phase_29(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(
        serialize_workflow_execution_state_json(
            replace(start().running_state, completed_step_ids=())
        ).encode()
    )
    calls = 0

    def execute(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return failure()

    with pytest.raises(PersistedRunningExecutionReentryCompatibilityError):
        execute_persisted_running_openai_step(
            start(), target, workflow(), employee(), tools(), key(), approval(tools()),
            transport=lambda _: None, execution_function=execute
        )  # type: ignore[arg-type]
    assert calls == 0


@pytest.mark.parametrize("changed_request", [
    ModelInvocationRequest("other", "system", "task", ("tool",)),
    ModelInvocationRequest("model", "other", "task", ("tool",)),
    ModelInvocationRequest("model", "system", "other", ("tool",)),
    ModelInvocationRequest("model", "system", "task", ()),
])
def test_altered_request_rejects_before_phase_29(
    tmp_path: Path, changed_request: ModelInvocationRequest
) -> None:
    value = replace(start(), request=changed_request)
    _error, calls = _execute(
        tmp_path / "state.json", value=value, resolved_tools=tools()
    )
    assert calls == 0


def test_invalid_tools_key_or_approval_reject_before_phase_29(tmp_path: Path) -> None:
    rejected = replace(approval(tools()), approved=False)
    for index, arguments in enumerate((
        {"resolved_tools": ()}, {"api_key": object()}, {"execution_approval": rejected},
    )):
        _error, calls = _execute(tmp_path / str(index), **arguments)
        assert calls == 0


def test_resolved_tool_order_mismatch_rejects_before_phase_29(tmp_path: Path) -> None:
    value = replace(
        start(),
        request=ModelInvocationRequest("model", "system", "task", ("tool", "other")),
    )
    employee_value = EmployeeDefinition.model_validate(
        {
            "id": "employee",
            "name": "E",
            "role": "R",
            "instructions": "system",
            "model": "model",
            "allowed_tools": ["tool", "other"],
        }
    )
    ordered_tools = (
        ToolDefinition("tool", "Tool", ()),
        ToolDefinition("other", "Other", ()),
    )
    target = tmp_path / "state.json"
    target.write_bytes(serialize_workflow_execution_state_json(value.running_state).encode())
    calls = 0

    def execute(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return failure()

    with pytest.raises(PersistedRunningExecutionReentryCompatibilityError):
        execute_persisted_running_openai_step(
            value,
            target,
            workflow(),
            employee_value,
            tuple(reversed(ordered_tools)),
            key(),
            approval(tuple(reversed(ordered_tools))),
            transport=lambda _: None,
            execution_function=execute,
        )  # type: ignore[arg-type]
    assert calls == 0


class SyntheticFailure(StepRuntimeExecutionFailure):
    pass


@pytest.mark.parametrize("result", [
    SyntheticFailure("workflow", "step", 2, "employee", failure().invocation_result),
    replace(failure(), step_index=True),
    replace(failure(), step_id="other"),
    replace(
        failure(),
        invocation_result=replace(failure().invocation_result, category="other"),
    ),
    replace(
        failure(), invocation_result=replace(failure().invocation_result, message="")
    ),
    replace(
        failure(),
        invocation_result=replace(failure().invocation_result, status_code=True),
    ),
    StepRuntimeExecutionFailure(
        "workflow",
        "step",
        2,
        "employee",
        ModelInvocationFailure("other", "api_error", "safe", None, None, None, None),
    ),
    StepRuntimeExecutionSuccess(
        "workflow",
        "step",
        2,
        "employee",
        ModelInvocationSuccess("openai", "", None, "completed", (), ""),
    ),
    StepRuntimeExecutionSuccess(
        "workflow",
        "step",
        2,
        "employee",
        ModelInvocationSuccess(
            "openai", "response", None, "completed", ("text",), "other"
        ),
    ),
])
def test_invalid_returned_result_is_rejected_after_one_phase_29_call(
    tmp_path: Path, result: object
) -> None:
    error, calls = _execute(tmp_path / "state.json", result=result)
    assert error.detail.classification == "execution_contract"
    assert calls == 1


def test_public_error_does_not_expose_sensitive_inputs(tmp_path: Path) -> None:
    error, _calls = _execute(tmp_path / "secret-api-key-state.json", api_key=object())
    message = str(error)
    assert "secret-api-key" not in message
    assert "approval" not in message
    assert "openai" not in message
