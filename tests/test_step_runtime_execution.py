"""Tests for the one-step runtime execution result boundary."""

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import get_args

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.execution_target import DIRECT_OPENAI_EXECUTION_TARGET
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
)
from ai_office.planning import StepExecutionRequest
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
    OpenAIResponsesTransportError,
)
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionInput,
    StepRuntimeExecutionResult,
    StepRuntimeExecutionSuccess,
    execute_openai_runtime_step,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition
from tests._execution_evidence_test_support import create_test_execution_evidence


def step_request(
    *,
    model: str = "model",
    employee_instructions: str = "employee instructions",
    step_instructions: str = "step instructions",
    allowed_tools: tuple[str, ...] = (),
) -> StepExecutionRequest:
    return StepExecutionRequest(
        workflow_id="workflow",
        workflow_name="Workflow",
        step_index=1,
        step_id="step",
        step_name="Step",
        employee_id="employee",
        employee_name="Employee",
        employee_role="Role",
        model=model,
        allowed_tools=allowed_tools,
        employee_instructions=employee_instructions,
        step_instructions=step_instructions,
    )


def invocation_request(
    step: StepExecutionRequest,
) -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model=step.model,
        system_instructions=step.employee_instructions,
        task_instructions=step.step_instructions,
        allowed_tools=step.allowed_tools,
    )


def tool(name: str = "web_search") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        parameters=(
            ToolParameterDefinition("query", "query description", "string", True),
        ),
    )


def execution_input(
    *,
    root: Path,
    step: StepExecutionRequest | None = None,
    invocation: ModelInvocationRequest | None = None,
    tools: tuple[ToolDefinition, ...] = (),
) -> StepRuntimeExecutionInput:
    value_step = step or step_request(allowed_tools=tuple(item.name for item in tools))
    tool_names = tuple(item.name for item in tools)
    workflow = WorkflowDefinition.model_validate(
        {
            "id": value_step.workflow_id,
            "name": value_step.workflow_name,
            "description": "runtime execution evidence fixture",
            "steps": [
                {
                    "id": value_step.step_id,
                    "name": value_step.step_name,
                    "employee": value_step.employee_id,
                    "instructions": value_step.step_instructions,
                    "business_approval_required": False,
                }
            ],
        }
    )
    employee = EmployeeDefinition(
        id=value_step.employee_id,
        name=value_step.employee_name,
        role=value_step.employee_role,
        instructions=value_step.employee_instructions,
        model=value_step.model,
        allowed_tools=list(dict.fromkeys(tool_names)),
    )
    source_request = ModelInvocationRequest(
        model=value_step.model,
        system_instructions=value_step.employee_instructions,
        task_instructions=value_step.step_instructions,
        allowed_tools=tool_names,
    )
    evidence = create_test_execution_evidence(
        root,
        run_id=f"runtime-execution-{root.name}",
        workflow=workflow,
        employees=(employee,),
        request=source_request,
        resolved_tools=tools,
        step_id=value_step.step_id,
        target=DIRECT_OPENAI_EXECUTION_TARGET,
        approval_id=f"approval-{root.name}",
    )
    bound_step = value_step.model_copy(
        update={
            "run_id": evidence.request.run_id,
            "manifest_digest": evidence.request.manifest_digest,
        }
    )
    value_invocation = invocation or ModelInvocationRequest(
        model=value_step.model,
        system_instructions=value_step.employee_instructions,
        task_instructions=value_step.step_instructions,
        allowed_tools=value_step.allowed_tools,
        run_id=evidence.request.run_id,
        manifest_digest=evidence.request.manifest_digest,
        run_input=evidence.request.run_input,
    )
    return StepRuntimeExecutionInput(
        step_request=bound_step,
        invocation_request=value_invocation,
        resolved_tools=tools,
        approval=evidence.approval,
        execution_evidence=evidence.context,
    )


def api_key() -> OpenAIApiKey:
    return OpenAIApiKey(value=SecretStr("synthetic-key"))


def raw_response(status_code: int, payload: object) -> OpenAIResponsesRawHttpResponse:
    return OpenAIResponsesRawHttpResponse(
        status_code=status_code,
        reason="synthetic",
        headers=(("x-request-id", "request-123"),),
        body=json.dumps(payload).encode(),
    )


def success_payload(content: object) -> dict[str, object]:
    return {
        "id": "response-123",
        "object": "response",
        "status": "completed",
        "output": [{"type": "message", "content": content}],
    }


def test_runtime_models_are_immutable_and_preserve_exact_identity_and_result(
    tmp_path: Path,
) -> None:
    result = ModelInvocationSuccess(
        provider="openai",
        response_id="response",
        request_id=None,
        status="completed",
        text_parts=("text",),
        text="text",
    )
    success = StepRuntimeExecutionSuccess("workflow", "step", 1, "employee", result)
    failure = StepRuntimeExecutionFailure(
        "workflow",
        "step",
        1,
        "employee",
        ModelInvocationFailure(
            "openai", "invalid_request", "safe", None, None, None, None
        ),
    )

    assert success.invocation_result is result
    assert success.step_index == 1
    assert set(get_args(StepRuntimeExecutionResult)) == {
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
    }
    with pytest.raises(FrozenInstanceError):
        success.step_id = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        failure.employee_id = "other"  # type: ignore[misc]

    value = execution_input(root=tmp_path)
    assert "api_key" not in value.__dataclass_fields__
    with pytest.raises(FrozenInstanceError):
        value.approval = value.approval  # type: ignore[misc]


def test_success_and_empty_text_are_wrapped_without_mutating_inputs(
    tmp_path: Path,
) -> None:
    value = execution_input(root=tmp_path)
    original_invocation = value.invocation_request
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        return raw_response(200, success_payload([]))

    result = execute_openai_runtime_step(value, api_key(), transport=transport)

    assert isinstance(result, StepRuntimeExecutionSuccess)
    assert result.workflow_id == "workflow"
    assert result.step_id == "step"
    assert result.step_index == 1
    assert result.employee_id == "employee"
    assert result.invocation_result.text == ""
    assert calls == 1
    assert value.invocation_request == original_invocation


def test_duplicate_resolved_tools_preserve_order_through_one_transport_call(
    tmp_path: Path,
) -> None:
    value = execution_input(
        root=tmp_path, tools=(tool("web_search"), tool("web_search"))
    )
    requests: list[OpenAIResponsesAuthenticatedHttpRequest] = []

    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        requests.append(request_value)
        return raw_response(200, success_payload([]))

    result = execute_openai_runtime_step(value, api_key(), transport=transport)

    assert isinstance(result, StepRuntimeExecutionSuccess)
    assert len(requests) == 1
    assert requests[0].body.count('"name":"web_search"') == 2
    assert value.resolved_tools == (tool("web_search"), tool("web_search"))


@pytest.mark.parametrize(
    ("transport", "category"),
    [
        (
            lambda _: raw_response(
                429,
                {
                    "error": {
                        "message": "safe",
                        "type": None,
                        "param": None,
                        "code": None,
                    }
                },
            ),
            "api_error",
        ),
        (
            lambda _: (_ for _ in ()).throw(OpenAIResponsesTransportError("safe")),
            "transport_error",
        ),
        (
            lambda _: OpenAIResponsesRawHttpResponse(200, "synthetic", (), b"invalid"),
            "invalid_response",
        ),
        (
            lambda _: raw_response(200, success_payload([{"type": "output_text"}])),
            "invalid_output",
        ),
    ],
)
def test_provider_failures_are_wrapped_without_reinterpretation(
    tmp_path: Path,
    transport: object,
    category: str,
) -> None:
    result = execute_openai_runtime_step(
        execution_input(root=tmp_path),
        api_key(),
        transport=transport,  # type: ignore[arg-type]
    )

    assert isinstance(result, StepRuntimeExecutionFailure)
    assert result.invocation_result.category == category
    assert result.workflow_id == "workflow"
    assert result.step_index == 1


def test_rejected_approval_and_resolved_tool_mismatch_preserve_provider_guards(
    tmp_path: Path,
) -> None:
    valid = execution_input(root=tmp_path)
    approval_rejected = replace(
        valid, approval=replace(valid.approval, approved=False)
    )
    mismatch = execution_input(
        root=tmp_path / "mismatch",
        step=step_request(allowed_tools=("search",)),
        tools=(),
    )
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    approval_result = execute_openai_runtime_step(
        approval_rejected,
        api_key(),
        transport=transport,
    )
    mismatch_result = execute_openai_runtime_step(
        mismatch, api_key(), transport=transport
    )

    assert approval_result.invocation_result.category == "approval_required"  # type: ignore[union-attr]
    assert mismatch_result.invocation_result.category == "invalid_request"  # type: ignore[union-attr]
    assert calls == 0


@pytest.mark.parametrize(
    "invocation",
    [
        ModelInvocationRequest(
            "other", "employee instructions", "step instructions", ()
        ),
        ModelInvocationRequest("model", "other", "step instructions", ()),
        ModelInvocationRequest("model", "employee instructions", "other", ()),
        ModelInvocationRequest(
            "model", "employee instructions", "step instructions", ("search",)
        ),
    ],
)
def test_cross_model_mismatch_is_safe_invalid_request_before_transport(
    tmp_path: Path,
    invocation: ModelInvocationRequest,
) -> None:
    value = execution_input(root=tmp_path, invocation=invocation)
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    result = execute_openai_runtime_step(value, api_key(), transport=transport)

    assert isinstance(result, StepRuntimeExecutionFailure)
    assert result.invocation_result.category == "invalid_request"
    assert (
        result.invocation_result.message
        == "runtime step execution inputs are inconsistent"
    )
    assert result.invocation_result.request_id is None
    assert calls == 0


def test_transport_uncertainty_is_recorded_and_not_retried(tmp_path: Path) -> None:
    value = execution_input(root=tmp_path)
    calls = 0

    def transport(_: OpenAIResponsesAuthenticatedHttpRequest):
        nonlocal calls
        calls += 1
        raise RuntimeError("unexpected")

    result = execute_openai_runtime_step(value, api_key(), transport=transport)
    assert isinstance(result, StepRuntimeExecutionFailure)
    assert result.invocation_result.category == "transport_error"
    assert calls == 1

    from ai_office.execution_evidence import ExecutionAttemptAlreadyClaimedError

    with pytest.raises(ExecutionAttemptAlreadyClaimedError):
        execute_openai_runtime_step(value, api_key(), transport=transport)
    assert calls == 1
