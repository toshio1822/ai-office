"""Provider-independent explicit approval for paid model invocations."""

import json
from dataclasses import dataclass
from hashlib import sha256

from ai_office.execution_target import (
    DIRECT_OPENAI_EXECUTION_TARGET,
    ModelExecutionTarget,
    ModelExecutionTargetError,
    execution_target_for_name,
    validate_execution_target_for_provider,
)
from ai_office.execution_target import (
    execution_target_fingerprint as build_execution_target_fingerprint,
)
from ai_office.invocation.model_invocation_request import (
    ModelInvocationRequest,
    build_model_invocation_task_input,
)
from ai_office.invocation.runtime_facts import (
    EMPTY_RUNTIME_FACTS,
    runtime_facts_snapshot_digest,
    serialize_runtime_facts_snapshot_canonical,
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
    # Defaults preserve construction of the pre-Phase-221 direct-OpenAI
    # approval value.  Active boundaries still validate this field and its
    # fingerprint before persistence or transport.
    execution_target: ModelExecutionTarget = DIRECT_OPENAI_EXECUTION_TARGET
    execution_target_fingerprint: str = build_execution_target_fingerprint(
        DIRECT_OPENAI_EXECUTION_TARGET
    )

    @property
    def target(self) -> ModelExecutionTarget:
        """Compatibility spelling for the approved execution target."""
        return self.execution_target

    @property
    def target_fingerprint(self) -> str:
        """Compatibility spelling for the target-only binding digest."""
        return self.execution_target_fingerprint


class ModelInvocationExecutionApprovalError(ValueError):
    """Raised when an explicit model invocation approval is not valid."""


def build_model_invocation_execution_fingerprint(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    execution_target: ModelExecutionTarget | None = None,
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
    has_runtime_facts = request.runtime_facts != EMPTY_RUNTIME_FACTS
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
    if has_runtime_facts:
        value["task_input"] = build_model_invocation_task_input(request)
        value["runtime_facts"] = json.loads(
            serialize_runtime_facts_snapshot_canonical(request.runtime_facts)
        )
        value["runtime_facts_snapshot_sha256"] = runtime_facts_snapshot_digest(
            request.runtime_facts
        )
    if execution_target is not None:
        execution_target = validate_execution_target_for_provider(execution_target)
        value["execution_target"] = {
            "allow_loopback_http": execution_target.allow_loopback_http,
            "base_url": execution_target.base_url,
            "credential_environment_variable": (
                execution_target.credential_environment_variable
            ),
            "protocol": execution_target.protocol,
            "provider": execution_target.provider,
        }
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
    target: ModelExecutionTarget | None = None,
    execution_target: ModelExecutionTarget | None = None,
) -> ModelInvocationExecutionApproval:
    """Create an explicit approval bound to the supplied invocation inputs."""
    _validate_approval_metadata(provider, approved_by, approval_id)
    if (
        target is not None
        and execution_target is not None
        and target != execution_target
    ):
        raise ModelInvocationExecutionApprovalError(_APPROVAL_ERROR_MESSAGE) from None
    selected_target = execution_target if execution_target is not None else target
    if selected_target is None:
        try:
            selected_target = execution_target_for_name(provider)
        except (ModelExecutionTargetError, TypeError):
            # Retain the old constructor surface for type-only legacy tests.
            # Such an approval cannot pass the authoritative validation below.
            selected_target = DIRECT_OPENAI_EXECUTION_TARGET
    try:
        selected_target = validate_execution_target_for_provider(
            selected_target, provider=provider
        )
    except ModelExecutionTargetError:
        # Unsupported legacy provider labels may still be materialized by
        # old type-contract fixtures; they are rejected by validation and can
        # never reach a provider boundary.
        if provider not in {"openai", "omniroute"}:
            selected_target = DIRECT_OPENAI_EXECUTION_TARGET
        else:
            raise ModelInvocationExecutionApprovalError(
                _APPROVAL_ERROR_MESSAGE
            ) from None
    bind_request_to_target = (
        target is not None
        or execution_target is not None
        or provider == "omniroute"
    )
    return ModelInvocationExecutionApproval(
        approved=True,
        provider=provider,
        request_fingerprint=build_model_invocation_execution_fingerprint(
            request,
            resolved_tools,
            selected_target if bind_request_to_target else None,
        ),
        approved_by=approved_by,
        approval_id=approval_id,
        execution_target=selected_target,
        execution_target_fingerprint=build_execution_target_fingerprint(
            selected_target
        ),
    )


def validate_model_invocation_execution_approval(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    approval: ModelInvocationExecutionApproval,
    *,
    provider: str,
    target: ModelExecutionTarget | None = None,
    execution_target: ModelExecutionTarget | None = None,
) -> None:
    """Validate an explicit approval without exposing mismatch details."""
    try:
        if (
            target is not None
            and execution_target is not None
            and target != execution_target
        ):
            raise ModelExecutionTargetError
        selected_target = execution_target if execution_target is not None else target
        if selected_target is None:
            selected_target = execution_target_for_name(provider)
        selected_target = validate_execution_target_for_provider(
            selected_target, provider=provider
        )
        expected_request_fingerprint = build_model_invocation_execution_fingerprint(
            request, resolved_tools
        )
        target_bound_request_fingerprint = build_model_invocation_execution_fingerprint(
            request, resolved_tools, selected_target
        )
        legacy_direct_request_fingerprint = (
            selected_target == DIRECT_OPENAI_EXECUTION_TARGET
            and approval.request_fingerprint
            == build_model_invocation_execution_fingerprint(request, resolved_tools)
        )
        is_valid = (
            type(approval) is ModelInvocationExecutionApproval
            and approval.approved is True
            and approval.provider == provider
            and (
                approval.request_fingerprint == target_bound_request_fingerprint
                or (
                    approval.request_fingerprint == expected_request_fingerprint
                    and legacy_direct_request_fingerprint
                )
            )
            and approval.execution_target == selected_target
            and approval.execution_target_fingerprint
            == build_execution_target_fingerprint(selected_target)
            and _is_nonempty_string(approval.approved_by)
            and _is_nonempty_string(approval.approval_id)
        )
    except (ModelExecutionTargetError, TypeError, ValueError, AttributeError):
        is_valid = False
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
