"""Tests for guarded composition of the OpenAI execution boundaries."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.execution_evidence import (
    ExecutionEvidenceError,
    list_run_execution_evidence,
)
from ai_office.execution_target import DIRECT_OPENAI_EXECUTION_TARGET
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
    OpenAIResponsesTransportError,
    execute_openai_model_invocation,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition
from tests._execution_evidence_test_support import create_test_execution_evidence

type FakeTransport = Callable[
    [OpenAIResponsesAuthenticatedHttpRequest], OpenAIResponsesRawHttpResponse
]


def tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        parameters=(
            ToolParameterDefinition("query", "query description", "string", True),
        ),
    )


def request(allowed_tools: tuple[str, ...] = ()) -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model="model",
        system_instructions="system instructions",
        task_instructions="task instructions",
        allowed_tools=allowed_tools,
    )


def api_key() -> OpenAIApiKey:
    return OpenAIApiKey(value=SecretStr("synthetic-key"))


def approval(
    invocation: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
):
    return approve_model_invocation_execution(
        invocation,
        resolved_tools,
        provider="openai",
        approved_by="test-user",
        approval_id="test-approval",
    )


def raw_response(status_code: int, payload: object) -> OpenAIResponsesRawHttpResponse:
    return OpenAIResponsesRawHttpResponse(
        status_code=status_code,
        reason="synthetic",
        headers=(("x-request-id", "request_123"),),
        body=json.dumps(payload, ensure_ascii=False).encode(),
    )


def success_payload(content: object) -> dict[str, object]:
    return {
        "id": "resp_123",
        "object": "response",
        "status": "completed",
        "output": [{"type": "message", "content": content}],
    }


def evidence_fixture(
    tmp_path: Path,
    invocation: ModelInvocationRequest | None = None,
    resolved_tools: tuple[ToolDefinition, ...] = (),
):
    source = request() if invocation is None else invocation
    workflow = WorkflowDefinition.model_validate(
        {
            "id": "provider-test-workflow",
            "name": "Provider Test Workflow",
            "description": "Run-bound provider test.",
            "steps": [
                {
                    "id": "provider-step",
                    "name": "Provider Step",
                    "employee": "test-employee",
                    "instructions": "Execute the provider test.",
                    "business_approval_required": False,
                }
            ],
        }
    )
    employee = EmployeeDefinition(
        id="test-employee",
        name="Test Employee",
        role="Provider test",
        instructions="Execute the provider test.",
        model=source.model,
        allowed_tools=list(dict.fromkeys(source.allowed_tools)),
    )
    return create_test_execution_evidence(
        tmp_path,
        run_id="provider-test-run",
        workflow=workflow,
        employees=(employee,),
        request=source,
        resolved_tools=resolved_tools,
        step_id="provider-step",
        target=DIRECT_OPENAI_EXECUTION_TARGET,
    )


def test_success_composes_boundaries_once_and_preserves_exact_output(
    tmp_path: Path,
) -> None:
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []

    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        calls.append(request_value)
        return raw_response(
            200,
            success_payload(
                [
                    {"type": "output_text", "text": " first\n"},
                    {"type": "output_text", "text": "最後 😀"},
                ]
            ),
        )

    unbound_invocation = request(("web_search", "web_search"))
    resolved_tools = (tool("web_search"), tool("web_search"))
    fixture = evidence_fixture(tmp_path, unbound_invocation, resolved_tools)
    invocation = fixture.request
    result = execute_openai_model_invocation(
        invocation,
        resolved_tools,
        api_key(),
        fixture.approval,
        transport=transport,
        execution_evidence=fixture.context,
    )

    assert isinstance(result, ModelInvocationSuccess)
    assert result.provider == "openai"
    assert result.response_id == "resp_123"
    assert result.request_id == "request_123"
    assert result.status == "completed"
    assert result.text_parts == (" first\n", "最後 😀")
    assert result.text == " first\n最後 😀"
    assert len(calls) == 1
    assert calls[0].headers[-1] == ("Authorization", "Bearer synthetic-key")
    assert calls[0].body.count('"name":"web_search"') == 2
    assert invocation.allowed_tools == ("web_search", "web_search")
    assert resolved_tools == (tool("web_search"), tool("web_search"))


def test_empty_supported_output_text_remains_success(tmp_path: Path) -> None:
    fixture = evidence_fixture(tmp_path)
    result = execute_openai_model_invocation(
        fixture.request,
        (),
        api_key(),
        fixture.approval,
        transport=lambda _: raw_response(200, success_payload([])),
        execution_evidence=fixture.context,
    )

    assert isinstance(result, ModelInvocationSuccess)
    assert result.text_parts == ()
    assert result.text == ""


def test_api_error_is_normalized_as_data(tmp_path: Path) -> None:
    fixture = evidence_fixture(tmp_path)
    result = execute_openai_model_invocation(
        fixture.request,
        (),
        api_key(),
        fixture.approval,
        transport=lambda _: raw_response(
            429,
            {
                "error": {
                    "message": "synthetic API error",
                    "type": "synthetic_type",
                    "param": "not copied",
                    "code": "synthetic_code",
                }
            },
        ),
        execution_evidence=fixture.context,
    )

    assert result.category == "api_error"  # type: ignore[union-attr]
    assert result.request_id == "request_123"  # type: ignore[union-attr]
    assert result.status_code == 429  # type: ignore[union-attr]
    assert result.provider_error_type == "synthetic_type"  # type: ignore[union-attr]
    assert result.provider_error_code == "synthetic_code"  # type: ignore[union-attr]
    assert "not copied" not in repr(result)


@pytest.mark.parametrize(
    ("transport", "expected_category", "expected_message"),
    [
        (
            lambda _: (_ for _ in ()).throw(
                OpenAIResponsesTransportError("safe transport error")
            ),
            "transport_error",
            "safe transport error",
        ),
        (
            lambda _: OpenAIResponsesRawHttpResponse(200, "synthetic", (), b"not JSON"),
            "invalid_response",
            "invalid JSON response body",
        ),
        (
            lambda _: raw_response(200, success_payload([{"type": "output_text"}])),
            "invalid_output",
            "invalid OpenAI output structure",
        ),
    ],
)
def test_safe_errors_are_normalized(
    tmp_path: Path,
    transport: FakeTransport,
    expected_category: str,
    expected_message: str,
) -> None:
    fixture = evidence_fixture(tmp_path)
    result = execute_openai_model_invocation(
        fixture.request,
        (),
        api_key(),
        fixture.approval,
        transport=transport,
        execution_evidence=fixture.context,
    )

    assert result.category == expected_category  # type: ignore[union-attr]
    assert result.message == expected_message  # type: ignore[union-attr]
    assert result.request_id is None  # type: ignore[union-attr]
    assert result.status_code is None  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("allowed_tools", "resolved_tools"),
    [
        (("search",), ()),
        ((), (tool("search"),)),
        (("search", "read"), (tool("read"), tool("search"))),
        (("search",), (tool("read"),)),
    ],
)
def test_tool_mismatch_fails_before_transport(
    tmp_path: Path,
    allowed_tools: tuple[str, ...],
    resolved_tools: tuple[ToolDefinition, ...],
) -> None:
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    invocation = request(allowed_tools)
    evidence = evidence_fixture(tmp_path)
    result = execute_openai_model_invocation(
        invocation,
        resolved_tools,
        api_key(),
        approval(invocation, resolved_tools),
        transport=transport,
        execution_evidence=evidence.context,
    )

    assert result.category == "invalid_request"  # type: ignore[union-attr]
    assert result.message == "resolved tools do not match invocation request"  # type: ignore[union-attr]
    assert calls == 0


def test_execution_evidence_must_match_exact_approval_before_transport(
    tmp_path: Path,
) -> None:
    fixture = evidence_fixture(tmp_path)
    alternate_approval = approve_model_invocation_execution(
        fixture.request,
        (),
        provider="openai",
        approved_by="different-reviewer",
        approval_id="different-approval",
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    with pytest.raises(ExecutionEvidenceError):
        execute_openai_model_invocation(
            fixture.request,
            (),
            api_key(),
            alternate_approval,
            transport=transport,
            execution_evidence=fixture.context,
        )
    assert calls == 0
    assert list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    ) == ()


def test_rejected_approval_fails_before_transport(tmp_path: Path) -> None:
    invocation = request()
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    result = execute_openai_model_invocation(
        invocation,
        (),
        api_key(),
        ModelInvocationExecutionApproval(False, "openai", "stale", "reviewer", "id"),
        transport=transport,
        execution_evidence=evidence_fixture(tmp_path).context,
    )

    assert result.category == "approval_required"  # type: ignore[union-attr]
    assert result.message == "model invocation execution is not approved"  # type: ignore[union-attr]
    assert result.request_id is None  # type: ignore[union-attr]
    assert result.status_code is None  # type: ignore[union-attr]
    assert calls == 0


def test_tool_mismatch_precedes_rejected_approval(tmp_path: Path) -> None:
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    result = execute_openai_model_invocation(
        request(("search",)),
        (),
        api_key(),
        ModelInvocationExecutionApproval(False, "openai", "stale", "reviewer", "id"),
        transport=transport,
        execution_evidence=evidence_fixture(tmp_path).context,
    )

    assert result.category == "invalid_request"  # type: ignore[union-attr]
    assert calls == 0


def test_arbitrary_transport_exception_is_conservatively_normalized(
    tmp_path: Path,
) -> None:
    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        raise RuntimeError("unexpected")

    fixture = evidence_fixture(tmp_path)
    result = execute_openai_model_invocation(
        fixture.request,
        (),
        api_key(),
        fixture.approval,
        transport=transport,
        execution_evidence=fixture.context,
    )
    assert result.category == "transport_error"  # type: ignore[union-attr]
