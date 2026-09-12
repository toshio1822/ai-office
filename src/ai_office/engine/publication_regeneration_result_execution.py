"""Execute Phase 266 once and durably persist its normalized result evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from ai_office.engine.publication_regeneration import (
    PublicationRegenerationApproval,
    PublicationRegenerationAttemptClaim,
    PublicationRegenerationPlan,
    build_publication_regeneration_attempt_claim,
    load_publication_regeneration_attempt_claim,
    publication_regeneration_attempt_claim_path,
    publication_regeneration_consumption_key,
)
from ai_office.engine.publication_regeneration_execution import (
    execute_approved_publication_regeneration,
)
from ai_office.engine.publication_regeneration_result import (
    PublicationRegenerationResultRecord,
    build_publication_regeneration_result_record,
    persist_publication_regeneration_result,
    preflight_publication_regeneration_result_path,
)
from ai_office.execution_target import ModelExecutionTarget
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationRequest,
)
from ai_office.providers.openai import (
    OpenAIResponsesTransport,
    send_openai_responses_http_request,
)
from ai_office.tools import ToolDefinition

_RESULT_EXECUTION_ERROR_MESSAGE = "publication regeneration result execution is blocked"


@dataclass(frozen=True)
class PublicationRegenerationResultExecutionFailureDetail:
    """Safe classification for a post-provider result-evidence boundary."""

    classification: str


class PublicationRegenerationResultExecutionError(ValueError):
    """Raised when result evidence cannot be bound after Phase 266."""

    def __init__(self, classification: str = "execution") -> None:
        super().__init__(_RESULT_EXECUTION_ERROR_MESSAGE)
        self.detail = PublicationRegenerationResultExecutionFailureDetail(
            classification
        )


def execute_and_persist_approved_publication_regeneration(
    *,
    result_path: Path,
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
) -> PublicationRegenerationResultRecord:
    """Execute Phase 266 exactly once, then persist its exact result evidence.

    Result-target preflight is deliberately the first operation.  Phase 266 is
    called once and is never retried.  After it returns, the durable Phase 265
    claim is loaded and independently compared with a claim rebuilt from the
    exact plan and outer approval before any result record is built or written.
    """
    preflight_publication_regeneration_result_path(result_path)

    result = execute_approved_publication_regeneration(
        audit_path=audit_path,
        ledger_directory=ledger_directory,
        plan=plan,
        outer_approval=outer_approval,
        request=request,
        resolved_tools=resolved_tools,
        inner_approval=inner_approval,
        execution_target=execution_target,
        environment=environment,
        transport=transport,
    )

    loaded_claim = _load_consumed_claim(ledger_directory, outer_approval)
    expected_claim = _build_expected_claim(plan, outer_approval)
    if loaded_claim != expected_claim:
        _raise_result_execution("claim_mismatch")

    record = build_publication_regeneration_result_record(loaded_claim, result)
    return persist_publication_regeneration_result(
        result_path,
        record,
        allow_idempotent=False,
    )


def _load_consumed_claim(
    ledger_directory: Path,
    outer_approval: PublicationRegenerationApproval,
) -> PublicationRegenerationAttemptClaim:
    try:
        consumption_key = publication_regeneration_consumption_key(outer_approval)
        path = publication_regeneration_attempt_claim_path(
            ledger_directory,
            consumption_key,
        )
        return load_publication_regeneration_attempt_claim(path)
    except Exception:
        _raise_result_execution("claim_load")


def _build_expected_claim(
    plan: PublicationRegenerationPlan,
    outer_approval: PublicationRegenerationApproval,
) -> PublicationRegenerationAttemptClaim:
    try:
        return build_publication_regeneration_attempt_claim(plan, outer_approval)
    except Exception:
        _raise_result_execution("claim_expected")


def _raise_result_execution(classification: str) -> NoReturn:
    raise PublicationRegenerationResultExecutionError(classification) from None


__all__ = [
    "PublicationRegenerationResultExecutionError",
    "PublicationRegenerationResultExecutionFailureDetail",
    "execute_and_persist_approved_publication_regeneration",
]
