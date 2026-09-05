"""Provider-independent explicit approval for paid model invocations."""

import json
from dataclasses import dataclass
from hashlib import sha256

from ai_office.invocation.model_invocation_request import (
    ModelInvocationRequest,
    build_model_invocation_task_input,
)
from ai_office.tools import ToolDefinition

_APPROVAL_ERROR_MESSAGE = "model invocation execution is not approved"


@dataclass(frozen=True)
class ModelInvocationExecutionApproval:
    """Immutable caller-provided approval bound to one invocation and tool tuple."""

    approved: bool
    provider: str
    request_fingerprint: str
    approved_by: str
    approval_id: str


class ModelInvocationExecutionApprovalError(ValueError):
    """Raised when an explicit model invocation approval is not valid."""


def build_model_invocation_execution_fingerprint(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
) -> str:
    """Return a deterministic fingerprint of the invocation and resolved tools."""
    value = {
        "model": request.model,
        "system_instructions": request.system_instructions,
        "task_instructions": request.task_instructions,
        "allowed_tools": list(request.allowed_tools),
        "resolved_tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": [
                    {
                        "name": parameter.name,
                        "description": parameter.description,
                        "type": parameter.type,
                        "required": parameter.required,
                    }
                    for parameter in tool.parameters
                ],
            }
            for tool in resolved_tools
        ],
    }
    if request.upstream_inputs != ():
        value["task_input"] = build_model_invocation_task_input(request)
        value["upstream_inputs"] = [
            {
                "employee_id": upstream.employee_id,
                "output_text": upstream.output_text,
                "step_id": upstream.step_id,
                "step_index": upstream.step_index,
                "workflow_id": upstream.workflow_id,
            }
            for upstream in request.upstream_inputs
        ]
    canonical_value = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical_value.encode("utf-8")).hexdigest()


def approve_model_invocation_execution(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    *,
    provider: str,
    approved_by: str,
    approval_id: str,
) -> ModelInvocationExecutionApproval:
    """Create an explicit approval bound to the supplied invocation inputs."""
    _validate_approval_metadata(provider, approved_by, approval_id)
    return ModelInvocationExecutionApproval(
        approved=True,
        provider=provider,
        request_fingerprint=build_model_invocation_execution_fingerprint(
            request,
            resolved_tools,
        ),
        approved_by=approved_by,
        approval_id=approval_id,
    )


def validate_model_invocation_execution_approval(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    approval: ModelInvocationExecutionApproval,
    *,
    provider: str,
) -> None:
    """Validate an explicit approval without exposing mismatch details."""
    is_valid = (
        approval.approved is True
        and approval.provider == provider
        and approval.request_fingerprint
        == build_model_invocation_execution_fingerprint(request, resolved_tools)
        and _is_nonempty_string(approval.approved_by)
        and _is_nonempty_string(approval.approval_id)
    )
    if not is_valid:
        raise ModelInvocationExecutionApprovalError(_APPROVAL_ERROR_MESSAGE) from None


def _validate_approval_metadata(
    provider: str,
    approved_by: str,
    approval_id: str,
) -> None:
    metadata = (provider, approved_by, approval_id)
    if not all(_is_nonempty_string(value) for value in metadata):
        raise ModelInvocationExecutionApprovalError(_APPROVAL_ERROR_MESSAGE) from None


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and value != ""
