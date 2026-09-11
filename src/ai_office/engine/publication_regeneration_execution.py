"""One-shot provider execution for an approved publication regeneration.

This module is the first boundary that connects the provider-free Phase 263--265
contracts to the existing Responses provider executor.  It deliberately keeps
the Phase 265 durable claim inside a transport wrapper instead of copying the
provider executor: request, payload, and authentication construction therefore
remain owned by the existing provider implementation, while the claim is the
last durable write-ahead gate before the supplied transport is called.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from ai_office.engine.post_terminal_facts import (
    PublicationReadinessAuditError,
    load_publication_readiness_audit,
)
from ai_office.engine.publication_regeneration import (
    PublicationRegenerationApproval,
    PublicationRegenerationAttemptClaim,
    PublicationRegenerationAttemptClaimError,
    PublicationRegenerationPlan,
    claim_publication_regeneration_attempt,
    validate_publication_regeneration_approval,
    validate_publication_regeneration_plan,
)
from ai_office.execution_target import (
    ModelExecutionTarget,
    ModelExecutionTargetError,
    validate_execution_target_for_provider,
)
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationExecutionApprovalError,
    ModelInvocationRequest,
    ModelInvocationResult,
    validate_model_invocation_execution_approval,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesTransport,
    execute_openai_model_invocation,
    load_api_key_for_execution_target,
    send_openai_responses_http_request,
)
from ai_office.tools import ToolDefinition

_EXECUTION_ERROR_MESSAGE = "publication regeneration execution is blocked"


class PublicationRegenerationExecutionError(ValueError):
    """Raised when control-plane validation prevents provider execution."""

    def __init__(self, classification: str = "execution") -> None:
        super().__init__(_EXECUTION_ERROR_MESSAGE)
        self.detail = PublicationRegenerationExecutionFailureDetail(classification)


@dataclass(frozen=True)
class PublicationRegenerationExecutionFailureDetail:
    """Safe classification for a blocked regeneration execution."""

    classification: str


def execute_approved_publication_regeneration(
    *,
    audit_path: Path,
    ledger_directory: Path,
    plan: PublicationRegenerationPlan,
    outer_approval: PublicationRegenerationApproval,
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    inner_approval: ModelInvocationExecutionApproval,
    execution_target: ModelExecutionTarget,
    environment: Mapping[str, str] | None = None,
    transport: OpenAIResponsesTransport = send_openai_responses_http_request,
) -> ModelInvocationResult:
    """Execute one exact, durably claimed publication regeneration attempt.

    The explicit argument order is the control contract: fresh audit reload,
    plan revalidation, both approvals, target/credential preflight, and only
    then the existing provider executor.  The wrapper passed to that executor
    exclusively owns the final Phase 265 claim and delegates the supplied
    transport at most once.
    """
    audit = _load_fresh_audit(audit_path)
    _validate_plan(plan, audit, request, resolved_tools, execution_target)
    _validate_outer_approval(plan, outer_approval)
    _validate_inner_approval(
        request,
        resolved_tools,
        inner_approval,
        execution_target,
    )
    target, api_key = _preflight_target_and_credential(
        execution_target,
        environment,
    )
    if not callable(transport):
        _raise_execution("transport")

    transport_entered = False

    def guarded_transport(request_value):
        nonlocal transport_entered
        if transport_entered:
            _raise_execution("transport_reuse")
        transport_entered = True

        # This is intentionally the only durable side effect in this module.
        # It remains immediately adjacent to the actual external-side-effect
        # dependency call and is never compensated after success.
        claim = claim_publication_regeneration_attempt(
            ledger_directory,
            plan,
            outer_approval,
        )
        if type(claim) is not PublicationRegenerationAttemptClaim:
            _raise_execution("claim")
        return transport(request_value)

    try:
        return execute_openai_model_invocation(
            request,
            resolved_tools,
            api_key,
            inner_approval,
            transport=guarded_transport,
            execution_target=target,
        )
    except PublicationRegenerationAttemptClaimError:
        # Phase 265 classifications, including already_consumed and
        # ambiguous persistence, are already safe and must remain observable.
        raise
    except PublicationRegenerationExecutionError:
        raise
    except Exception:
        # The existing executor normalizes its supported provider failures into
        # ModelInvocationResult.  Anything else is an unexpected safe boundary
        # failure; importantly, a successful claim is never deleted or reset.
        _raise_execution("provider_execution")


def _load_fresh_audit(audit_path: Path):
    try:
        return load_publication_readiness_audit(audit_path)
    except PublicationReadinessAuditError:
        _raise_execution("audit_load")
    except Exception:
        _raise_execution("audit_load")


def _validate_plan(
    plan: PublicationRegenerationPlan,
    audit,
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    execution_target: ModelExecutionTarget,
) -> None:
    try:
        validate_publication_regeneration_plan(
            plan,
            audit,
            request,
            resolved_tools,
            execution_target,
        )
    except Exception:
        _raise_execution("plan")


def _validate_outer_approval(
    plan: PublicationRegenerationPlan,
    approval: PublicationRegenerationApproval,
) -> None:
    try:
        validate_publication_regeneration_approval(plan, approval)
    except Exception:
        _raise_execution("outer_approval")


def _validate_inner_approval(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    approval: ModelInvocationExecutionApproval,
    execution_target: ModelExecutionTarget,
) -> None:
    try:
        target = validate_execution_target_for_provider(execution_target)
        validate_model_invocation_execution_approval(
            request,
            resolved_tools,
            approval,
            provider=target.provider,
            execution_target=target,
        )
    except (ModelExecutionTargetError, ModelInvocationExecutionApprovalError):
        _raise_execution("inner_approval")
    except Exception:
        _raise_execution("inner_approval")


def _preflight_target_and_credential(
    execution_target: ModelExecutionTarget,
    environment: Mapping[str, str] | None,
) -> tuple[ModelExecutionTarget, OpenAIApiKey]:
    try:
        target = validate_execution_target_for_provider(execution_target)
    except Exception:
        _raise_execution("execution_target")
    return target, _preflight_api_key(target, environment)


def _preflight_api_key(
    execution_target: ModelExecutionTarget,
    environment: Mapping[str, str] | None,
):
    try:
        return load_api_key_for_execution_target(execution_target, environment)
    except Exception:
        _raise_execution("credential")


def _raise_execution(classification: str) -> NoReturn:
    raise PublicationRegenerationExecutionError(classification) from None


__all__ = [
    "PublicationRegenerationExecutionError",
    "PublicationRegenerationExecutionFailureDetail",
    "execute_approved_publication_regeneration",
]
