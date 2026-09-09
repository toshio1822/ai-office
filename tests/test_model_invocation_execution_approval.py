"""Tests for explicit provider-independent paid-execution approval."""

from dataclasses import FrozenInstanceError, replace

import pytest

from ai_office.execution_target import (
    DIRECT_OPENAI_EXECUTION_TARGET,
    LOCAL_OMNIROUTE_EXECUTION_TARGET,
)
from ai_office.invocation import (
    EMPTY_RUNTIME_FACTS,
    ModelInvocationExecutionApproval,
    ModelInvocationExecutionApprovalError,
    ModelInvocationRequest,
    RuntimeFact,
    RuntimeFactProvenance,
    RuntimeFactsSnapshot,
    approve_model_invocation_execution,
    build_model_invocation_execution_fingerprint,
    validate_model_invocation_execution_approval,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition


def request(
    *,
    model: str = "model",
    system_instructions: str = "system",
    task_instructions: str = "task",
    allowed_tools: tuple[str, ...] = ("search",),
    runtime_facts: RuntimeFactsSnapshot = EMPTY_RUNTIME_FACTS,
) -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model=model,
        system_instructions=system_instructions,
        task_instructions=task_instructions,
        allowed_tools=allowed_tools,
        runtime_facts=runtime_facts,
    )


def runtime_facts(
    *,
    value: str = "workflow_complete",
    value_kind: str = "enum",
    source_sha256: str = "a" * 64,
    observed_at: str | None = None,
) -> RuntimeFactsSnapshot:
    return RuntimeFactsSnapshot(
        facts=(
            RuntimeFact(
                key="workflow.status",
                value_kind=value_kind,  # type: ignore[arg-type]
                value=value,
                provenance=RuntimeFactProvenance(
                    origin="persisted_event",
                    workflow_id="article-workflow",
                    source_ref="event:4",
                    source_sha256=source_sha256,
                    observed_at=observed_at,
                ),
            ),
        )
    )


def tool(
    *,
    name: str = "search",
    description: str = "search description",
    parameter_name: str = "query",
    parameter_description: str = "query description",
    parameter_type: str = "string",
    required: bool = True,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        parameters=(
            ToolParameterDefinition(
                parameter_name,
                parameter_description,
                parameter_type,
                required,
            ),
        ),
    )


def approval(
    invocation: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
) -> ModelInvocationExecutionApproval:
    return approve_model_invocation_execution(
        invocation,
        resolved_tools,
        provider="openai",
        approved_by="reviewer",
        approval_id="approval-123",
    )


def test_approval_is_immutable_and_helper_binds_nonempty_metadata() -> None:
    invocation = request()
    value = approval(invocation, (tool(),))

    assert value.approved is True
    assert value.provider == "openai"
    assert value.approved_by == "reviewer"
    assert value.approval_id == "approval-123"
    assert value.request_fingerprint == build_model_invocation_execution_fingerprint(
        invocation,
        (tool(),),
    )
    with pytest.raises(FrozenInstanceError):
        value.approved_by = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["provider", "approved_by", "approval_id"])
def test_helper_rejects_empty_required_metadata(field: str) -> None:
    values = {"provider": "openai", "approved_by": "reviewer", "approval_id": "id"}
    values[field] = ""

    with pytest.raises(
        ModelInvocationExecutionApprovalError,
        match="^model invocation execution is not approved$",
    ):
        approve_model_invocation_execution(request(), (tool(),), **values)


def test_fingerprint_is_deterministic_and_order_sensitive_without_exposing_inputs() -> (
    None
):
    invocation = request(
        system_instructions="system secret 日本語",
        task_instructions="task secret 😀",
        allowed_tools=("search", "read", "search"),
    )
    tools = (tool(name="search"), tool(name="read"), tool(name="search"))
    fingerprint = build_model_invocation_execution_fingerprint(invocation, tools)

    assert fingerprint == build_model_invocation_execution_fingerprint(
        invocation, tools
    )
    assert len(fingerprint) == 64
    assert fingerprint != build_model_invocation_execution_fingerprint(
        replace(invocation, model="other"), tools
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        replace(invocation, system_instructions="other"), tools
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        replace(invocation, task_instructions="other"), tools
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        replace(invocation, allowed_tools=("read", "search", "search")), tools
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        invocation, (tools[1], tools[0], tools[2])
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        invocation, (replace(tools[0], description="other"), tools[1], tools[2])
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        invocation,
        (
            ToolDefinition(
                name="search",
                description="search description",
                parameters=(
                    ToolParameterDefinition("query", "other", "integer", False),
                ),
            ),
            tools[1],
            tools[2],
        ),
    )
    assert "system secret" not in fingerprint
    assert "task secret" not in fingerprint
    assert "synthetic-key" not in fingerprint


def test_nonempty_runtime_facts_fingerprint_binds_exact_snapshot_and_task_input(
) -> None:
    snapshot = runtime_facts()
    invocation = request(
        task_instructions="task\n",
        runtime_facts=snapshot,
    )
    fingerprint = build_model_invocation_execution_fingerprint(
        invocation, (tool(),)
    )

    assert fingerprint == (
        "46df6c0c5f998dfb096af8095f4212303b1aae73d6b6303619440c5b08e66fdd"
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        request(runtime_facts=runtime_facts(value="persisted_failure")), (tool(),)
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        request(
            runtime_facts=runtime_facts(
                value="workflow-complete",
                value_kind="identifier",
            )
        ),
        (tool(),),
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        request(
            runtime_facts=runtime_facts(
                source_sha256="b" * 64,
            )
        ),
        (tool(),),
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        request(
            runtime_facts=runtime_facts(
                observed_at="2026-09-09T09:00:00Z",
            )
        ),
        (tool(),),
    )
    assert "workflow_complete" not in fingerprint


def test_nonempty_runtime_facts_preserve_target_binding() -> None:
    invocation = request(runtime_facts=runtime_facts())

    direct = build_model_invocation_execution_fingerprint(
        invocation, (tool(),), DIRECT_OPENAI_EXECUTION_TARGET
    )
    local = build_model_invocation_execution_fingerprint(
        invocation, (tool(),), LOCAL_OMNIROUTE_EXECUTION_TARGET
    )

    assert direct != local


def test_nonempty_runtime_facts_caller_order_does_not_change_fingerprint() -> None:
    first = RuntimeFact(
        key="workflow.status",
        value_kind="enum",
        value="succeeded",
        provenance=RuntimeFactProvenance(
            "persisted_state", "article-workflow", "state", "a" * 64
        ),
    )
    second = RuntimeFact(
        key="step.completed_count",
        value_kind="integer",
        value=4,
        provenance=RuntimeFactProvenance(
            "persisted_state", "article-workflow", "state", "a" * 64
        ),
    )
    left = request(runtime_facts=RuntimeFactsSnapshot(facts=(first, second)))
    right = request(runtime_facts=RuntimeFactsSnapshot(facts=(second, first)))

    assert build_model_invocation_execution_fingerprint(left, (tool(),)) == (
        build_model_invocation_execution_fingerprint(right, (tool(),))
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda value: replace(value, approved=False),
        lambda value: replace(value, approved=1),
        lambda value: replace(value, provider="OpenAI"),
        lambda value: replace(value, request_fingerprint="stale"),
        lambda value: replace(value, approved_by=""),
        lambda value: replace(value, approval_id=""),
    ],
)
def test_validation_rejects_every_invalid_approval_without_details(
    change: object,
) -> None:
    invocation = request()
    tools = (tool(),)
    value = approval(invocation, tools)

    with pytest.raises(
        ModelInvocationExecutionApprovalError,
        match="^model invocation execution is not approved$",
    ) as error:
        validate_model_invocation_execution_approval(
            invocation,
            tools,
            change(value),  # type: ignore[operator]
            provider="openai",
        )

    assert error.value.__cause__ is None


def test_validation_accepts_current_approval_without_mutating_inputs() -> None:
    invocation = request()
    tools = (tool(),)
    value = approval(invocation, tools)

    validate_model_invocation_execution_approval(
        invocation,
        tools,
        value,
        provider="openai",
    )

    assert invocation == request()
    assert tools == (tool(),)
    assert value == approval(invocation, tools)


def test_approval_binds_nonempty_runtime_facts_and_rejects_stale_changes() -> None:
    original = request(runtime_facts=runtime_facts())
    tools = (tool(),)
    value = approval(original, tools)

    validate_model_invocation_execution_approval(
        original, tools, value, provider="openai"
    )
    for changed in (
        request(runtime_facts=runtime_facts(value="persisted_failure")),
        request(runtime_facts=runtime_facts(source_sha256="b" * 64)),
    ):
        with pytest.raises(
            ModelInvocationExecutionApprovalError,
            match="^model invocation execution is not approved$",
        ) as error:
            validate_model_invocation_execution_approval(
                changed, tools, value, provider="openai"
            )
        assert "workflow_complete" not in str(error.value)
        assert "persisted_failure" not in str(error.value)


def test_empty_runtime_facts_approval_cannot_validate_nonempty_request() -> None:
    empty_approval = approval(request(), (tool(),))

    with pytest.raises(ModelInvocationExecutionApprovalError) as error:
        validate_model_invocation_execution_approval(
            request(runtime_facts=runtime_facts()),
            (tool(),),
            empty_approval,
            provider="openai",
        )

    assert str(error.value) == "model invocation execution is not approved"
    assert "workflow_complete" not in str(error.value)


def test_non_json_tool_definition_value_propagates_standard_type_error() -> None:
    invalid_tool = ToolDefinition(
        name="search",
        description="description",
        parameters=(
            ToolParameterDefinition("query", "description", "string", object()),
        ),
    )

    with pytest.raises(TypeError):
        build_model_invocation_execution_fingerprint(request(), (invalid_tool,))
