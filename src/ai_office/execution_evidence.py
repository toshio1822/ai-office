"""Run-bound immutable evidence for one model-provider attempt.

The provider boundary uses this module as a small write-ahead/evidence
contract.  It intentionally is not a general event-sourcing layer:

* one exclusive attempt record is committed before transport;
* a returned raw response is committed, losslessly, before provider parsing;
* one normalized result record is committed before the result can reach the
  terminal state owner; and
* later records refer to the exact attempt and are never rewritten.

The records live beside the Run Manifest in the authoritative Run namespace.
Authentication material is deliberately absent from every record and from the
safe errors raised by this module.
"""

from __future__ import annotations

import base64
import binascii
import errno
import json
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, cast

from ai_office.execution_target import (
    SUPPORTED_EXECUTION_PROTOCOLS,
    SUPPORTED_EXECUTION_PROVIDERS,
    ModelExecutionTarget,
    canonicalize_execution_target_url,
    execution_target_fingerprint,
    execution_target_for_name,
    is_supported_execution_provider,
    validate_execution_target_for_provider,
)
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationFailure,
    ModelInvocationFailureCategory,
    ModelInvocationFailureDiagnostics,
    ModelInvocationRequest,
    ModelInvocationResult,
    ModelInvocationResultProvenance,
    ModelInvocationSuccess,
    build_model_invocation_task_input,
    validate_model_invocation_execution_approval,
)
from ai_office.tools import ToolDefinition

if TYPE_CHECKING:
    from ai_office.runtime.run_binding import WorkflowRunBinding

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DEFINITION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PATH_TYPE = type(Path())

_ATTEMPT_SCHEMA = "workflow-execution-attempt.v1"
_RECOVERY_ATTEMPT_SCHEMA = "workflow-execution-attempt.v2"
_RAW_SCHEMA = "workflow-execution-raw-response.v1"
_RESULT_SCHEMA = "workflow-execution-normalized-result.v1"
_ATTEMPT_PREFIX = "execution-attempt"
_RAW_PREFIX = "execution-raw-response"
_RESULT_PREFIX = "execution-normalized-result"

_ATTEMPT_V1_KEYS = frozenset(
    {
        "approved_by",
        "attempt_id",
        "employee_id",
        "execution_approval_evidence_sha256",
        "execution_approval_id",
        "execution_target_allow_loopback_http",
        "execution_target_endpoint",
        "execution_target_fingerprint",
        "execution_target_protocol",
        "invocation_fingerprint",
        "manifest_digest",
        "provider",
        "request_body_length",
        "request_body_sha256",
        "resolved_tools_sha256",
        "run_id",
        "schema_version",
        "state",
        "step_id",
        "step_index",
        "system_instruction_sha256",
        "task_input_sha256",
        "workflow_id",
    }
)
_ATTEMPT_V2_KEYS = _ATTEMPT_V1_KEYS | frozenset(
    {
        "previous_attempt_evidence_sha256",
        "previous_attempt_id",
        "recovery_approval_evidence_sha256",
        "recovery_approval_id",
        "recovery_decision_sha256",
    }
)
_RAW_KEYS = frozenset(
    {
        "attempt_evidence_sha256",
        "attempt_id",
        "body_base64",
        "body_length",
        "body_sha256",
        "manifest_digest",
        "provider",
        "request_id",
        "run_id",
        "safe_headers",
        "schema_version",
        "status_code",
        "step_id",
        "workflow_id",
    }
)
_RESULT_KEYS = frozenset(
    {
        "attempt_evidence_sha256",
        "attempt_id",
        "category",
        "employee_id",
        "failure_category",
        "failure_message",
        "manifest_digest",
        "normalized_result_sha256",
        "provider",
        "provider_error_code",
        "provider_error_type",
        "raw_response_body_sha256",
        "raw_response_evidence_sha256",
        "request_id",
        "response_diagnostics",
        "response_id",
        "run_id",
        "schema_version",
        "status",
        "status_code",
        "step_id",
        "text",
        "text_parts",
        "workflow_id",
    }
)
_EVENT_LINK_FIELDS = (
    "execution_attempt_id",
    "execution_attempt_evidence_sha256",
    "normalized_result_evidence_sha256",
    "raw_response_evidence_sha256",
    "raw_response_body_sha256",
)
_SAFE_RESPONSE_HEADERS = frozenset(
    {"content-type", "x-request-id", "request-id", "openai-request-id"}
)
_FAILURE_CATEGORIES = frozenset(
    {
        "api_error",
        "transport_error",
        "invalid_response",
        "invalid_output",
        "invalid_request",
        "approval_required",
    }
)
_RESPONSE_BODY_KINDS = frozenset(
    {
        "empty",
        "json",
        "sse",
        "html",
        "plaintext",
        "malformed_json",
        "non_utf8",
    }
)
_RESPONSE_DERIVED_CATEGORIES = frozenset(
    {"api_error", "invalid_response", "invalid_output"}
)


@dataclass(frozen=True)
class ExecutionEvidenceFailureDetail:
    """Safe classification for one evidence operation."""

    classification: str


class ExecutionEvidenceError(ValueError):
    """Raised when execution evidence is not a safe exact contract."""

    def __init__(self, classification: str = "contract") -> None:
        super().__init__("workflow execution evidence is invalid")
        self.classification = classification
        self.detail = ExecutionEvidenceFailureDetail(classification)


class ExecutionEvidencePersistenceError(ExecutionEvidenceError):
    """Raised when a durable evidence commit cannot be proven."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, "workflow execution evidence persistence failed")
        self.classification = classification
        self.detail = ExecutionEvidenceFailureDetail(classification)


class ExecutionEvidenceConflictError(ExecutionEvidencePersistenceError):
    """Raised when immutable evidence identity contains different bytes."""


class ExecutionAttemptAlreadyClaimedError(ExecutionEvidencePersistenceError):
    """Raised when a Run/step already has an attempt claim."""

    def __init__(self) -> None:
        ValueError.__init__(self, "workflow execution attempt is already claimed")
        self.classification = "already_claimed"
        self.detail = ExecutionEvidenceFailureDetail("already_claimed")


class ExecutionEvidenceLoadError(ExecutionEvidenceError):
    """Raised when durable evidence is malformed or incomplete."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, "workflow execution evidence could not be loaded")
        self.classification = classification
        self.detail = ExecutionEvidenceFailureDetail(classification)


@dataclass(frozen=True)
class ExecutionEvidenceContext:
    """Exact Run-bound inputs at the provider boundary."""

    store_root: Path
    binding: WorkflowRunBinding
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    request: ModelInvocationRequest
    resolved_tools: tuple[ToolDefinition, ...]
    approval: ModelInvocationExecutionApproval
    target: ModelExecutionTarget
    recovery_assessment: object | None = None
    recovery_approval: object | None = None

    def __post_init__(self) -> None:
        _validate_context(self)


@dataclass(frozen=True)
class ExecutionAttemptEvidence:
    """The immutable write-ahead claim committed before transport."""

    schema_version: Literal[
        "workflow-execution-attempt.v1", "workflow-execution-attempt.v2"
    ]
    attempt_id: str
    run_id: str
    manifest_digest: str
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    provider: str
    execution_target_fingerprint: str
    execution_target_endpoint: str
    execution_target_protocol: str
    execution_target_allow_loopback_http: bool
    invocation_fingerprint: str
    system_instruction_sha256: str
    task_input_sha256: str
    resolved_tools_sha256: str
    request_body_sha256: str
    request_body_length: int
    execution_approval_id: str
    approved_by: str
    execution_approval_evidence_sha256: str
    state: Literal["claimed"]
    recovery_decision_sha256: str | None = None
    recovery_approval_id: str | None = None
    recovery_approval_evidence_sha256: str | None = None
    previous_attempt_id: str | None = None
    previous_attempt_evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_attempt(self)

    @property
    def digest(self) -> str:
        return _digest(_canonical_json(_attempt_dict(self)))

    @property
    def wire_payload_sha256(self) -> str:
        """Compatibility/readability spelling for the unauthenticated body digest."""
        return self.request_body_sha256

    @property
    def wire_payload_length(self) -> int:
        return self.request_body_length


@dataclass(frozen=True)
class RawProviderResponseEvidence:
    """Lossless response body bytes plus safe response metadata."""

    schema_version: Literal["workflow-execution-raw-response.v1"]
    attempt_id: str
    attempt_evidence_sha256: str
    run_id: str
    manifest_digest: str
    workflow_id: str
    step_id: str
    provider: str
    status_code: int
    request_id: str | None
    safe_headers: tuple[tuple[str, str], ...]
    body_base64: str
    body_length: int
    body_sha256: str

    def __post_init__(self) -> None:
        _validate_raw(self)

    @property
    def body(self) -> bytes:
        try:
            return base64.b64decode(self.body_base64.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError, binascii.Error):
            _raise_load("raw_body")

    @property
    def digest(self) -> str:
        return _digest(_canonical_json(_raw_dict(self)))


@dataclass(frozen=True)
class NormalizedExecutionResultEvidence:
    """Provider-independent result bound to one attempt and raw response."""

    schema_version: Literal["workflow-execution-normalized-result.v1"]
    attempt_id: str
    attempt_evidence_sha256: str
    run_id: str
    manifest_digest: str
    workflow_id: str
    step_id: str
    employee_id: str
    provider: str
    category: Literal["success", "failure"]
    failure_category: ModelInvocationFailureCategory | None
    normalized_result_sha256: str
    raw_response_evidence_sha256: str | None
    raw_response_body_sha256: str | None
    response_id: str | None
    request_id: str | None
    status: str | None
    text_parts: tuple[str, ...] | None
    text: str | None
    failure_message: str | None
    status_code: int | None
    provider_error_type: str | None
    provider_error_code: str | None
    response_diagnostics: ModelInvocationFailureDiagnostics | None

    def __post_init__(self) -> None:
        _validate_result(self)

    @property
    def digest(self) -> str:
        return _digest(_canonical_json(_result_dict(self)))

    @property
    def result(self) -> ModelInvocationResult:
        provenance = ModelInvocationResultProvenance(
            attempt_id=self.attempt_id,
            attempt_evidence_sha256=self.attempt_evidence_sha256,
            normalized_result_evidence_sha256=self.digest,
            raw_response_evidence_sha256=self.raw_response_evidence_sha256,
            raw_response_body_sha256=self.raw_response_body_sha256,
        )
        if self.category == "success":
            assert self.response_id is not None
            assert self.status is not None
            assert self.text_parts is not None
            assert self.text is not None
            return ModelInvocationSuccess(
                provider=self.provider,
                response_id=self.response_id,
                request_id=self.request_id,
                status=self.status,
                text_parts=self.text_parts,
                text=self.text,
                _execution_evidence=provenance,
            )
        assert self.failure_category is not None
        assert self.failure_message is not None
        return ModelInvocationFailure(
            provider=self.provider,
            category=self.failure_category,
            message=self.failure_message,
            request_id=self.request_id,
            status_code=self.status_code,
            provider_error_type=self.provider_error_type,
            provider_error_code=self.provider_error_code,
            response_diagnostics=self.response_diagnostics,
            _execution_evidence=provenance,
        )


@dataclass(frozen=True)
class ExecutionEvidenceInspection:
    """Safe Run inspection; raw response body bytes are intentionally absent."""

    attempt_id: str
    run_id: str
    manifest_digest: str
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    provider: str
    invocation_fingerprint: str
    execution_target_fingerprint: str
    execution_approval_id: str
    transport_claimed: bool
    raw_response_received: bool
    raw_response_evidence_sha256: str | None
    raw_response_body_sha256: str | None
    normalized_result_evidence_sha256: str | None
    normalized_result_category: str | None
    ambiguous_or_unresolved: bool
    final_step_outcome_linked: bool
    previous_attempt_id: str | None = None
    recovery_approval_id: str | None = None
    recovery_decision_sha256: str | None = None


# ---------------------------------------------------------------------------
# Provider-boundary API


def build_execution_evidence_context(
    *,
    store_root: Path,
    binding: WorkflowRunBinding,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    approval: ModelInvocationExecutionApproval,
    target: ModelExecutionTarget,
    recovery_assessment: object | None = None,
    recovery_approval: object | None = None,
) -> ExecutionEvidenceContext:
    """Build a provider-free exact context.  No durable/external effect occurs."""
    return ExecutionEvidenceContext(
        store_root=store_root,
        binding=binding,
        workflow_id=workflow_id,
        step_id=step_id,
        step_index=step_index,
        employee_id=employee_id,
        request=request,
        resolved_tools=resolved_tools,
        approval=approval,
        target=target,
        recovery_assessment=recovery_assessment,
        recovery_approval=recovery_approval,
    )


def claim_execution_attempt(
    context: ExecutionEvidenceContext,
    http_request: object,
) -> ExecutionAttemptEvidence:
    """Commit the exact attempt claim before the transport callable is entered."""
    approval_evidence = _validate_context_and_approval(context)
    body = _validated_unauthenticated_request(context, http_request)

    existing = tuple(
        attempt
        for attempt in list_run_execution_evidence(
            context.store_root, context.binding.run_id
        )
        if _same_step(attempt, context)
    )
    if context.recovery_assessment is None:
        if existing:
            raise ExecutionAttemptAlreadyClaimedError
    else:
        assessment = context.recovery_assessment
        recovery_approval = context.recovery_approval
        if (
            getattr(assessment, "action", None)
            not in {"retry_failed", "retry_ambiguous"}
            or not existing
            or not any(
                attempt.attempt_id
                == getattr(assessment, "previous_attempt_id", None)
                and attempt.digest
                == getattr(assessment, "previous_attempt_evidence_sha256", None)
                for attempt in existing
            )
            or any(
                attempt.recovery_approval_id
                == getattr(recovery_approval, "approval_id", None)
                for attempt in existing
            )
            or any(
                attempt.execution_approval_id == approval_evidence.approval_id
                or attempt.execution_approval_evidence_sha256
                == approval_evidence.digest
                for attempt in existing
            )
        ):
            raise ExecutionAttemptAlreadyClaimedError

    claim = _build_attempt(context, body, approval_evidence.digest)
    path = execution_attempt_evidence_path(
        context.store_root, claim.run_id, claim.attempt_id
    )
    _persist_attempt(path, _canonical_json(_attempt_dict(claim)))
    return claim


def ensure_no_execution_attempt(context: ExecutionEvidenceContext) -> None:
    """Fail closed for a restart with an existing unresolved/complete attempt."""
    _validate_context_and_approval(context)
    if any(
        _same_step(attempt, context)
        for attempt in list_run_execution_evidence(
            context.store_root, context.binding.run_id
        )
    ):
        raise ExecutionAttemptAlreadyClaimedError


def persist_raw_response_evidence(
    context: ExecutionEvidenceContext,
    attempt: ExecutionAttemptEvidence,
    raw_response: object,
) -> RawProviderResponseEvidence:
    """Persist returned bytes before provider parsing/normalization is called."""
    _validate_attempt_context(context, attempt)
    _require_persisted_attempt(context, attempt)
    status_code = getattr(raw_response, "status_code", None)
    headers = getattr(raw_response, "headers", None)
    body = getattr(raw_response, "body", None)
    if (
        type(status_code) is not int
        or isinstance(status_code, bool)
        or not 100 <= status_code <= 599
        or type(headers) is not tuple
        or type(body) is not bytes
    ):
        _raise_contract("raw_response")

    safe_headers = _safe_response_headers(headers)
    request_id = _response_request_id(safe_headers)
    record = RawProviderResponseEvidence(
        schema_version=_RAW_SCHEMA,
        attempt_id=attempt.attempt_id,
        attempt_evidence_sha256=attempt.digest,
        run_id=attempt.run_id,
        manifest_digest=attempt.manifest_digest,
        workflow_id=attempt.workflow_id,
        step_id=attempt.step_id,
        provider=attempt.provider,
        status_code=status_code,
        request_id=request_id,
        safe_headers=safe_headers,
        body_base64=base64.b64encode(body).decode("ascii"),
        body_length=len(body),
        body_sha256=_digest(body),
    )
    path = execution_raw_response_evidence_path(
        context.store_root, record.run_id, record.attempt_id
    )
    _persist_idempotent(path, _canonical_json(_raw_dict(record)))
    return record


def persist_normalized_result_evidence(
    context: ExecutionEvidenceContext,
    attempt: ExecutionAttemptEvidence,
    result: ModelInvocationResult,
    *,
    raw_response: RawProviderResponseEvidence | None,
) -> NormalizedExecutionResultEvidence:
    """Persist one normalized outcome before terminal state transition."""
    _validate_attempt_context(context, attempt)
    _require_persisted_attempt(context, attempt)
    if type(result) not in (ModelInvocationSuccess, ModelInvocationFailure):
        _raise_contract("normalized_result")
    if raw_response is not None:
        _validate_raw_context(context, attempt, raw_response)
        _require_persisted_raw(context, raw_response)
    if (
        type(result) is ModelInvocationSuccess
        or (
            type(result) is ModelInvocationFailure
            and result.category in _RESPONSE_DERIVED_CATEGORIES
        )
    ) and raw_response is None:
        _raise_contract("raw_binding")

    record = _build_result_record(context, attempt, result, raw_response)
    path = execution_normalized_result_evidence_path(
        context.store_root, record.run_id, record.attempt_id
    )
    _persist_idempotent(path, _canonical_json(_result_dict(record)))
    return record


def execution_evidence_of_result(
    result: object,
) -> tuple[str, str, str, str | None, str | None] | None:
    """Return immutable provenance from a result created from saved evidence."""
    if type(result) not in (ModelInvocationSuccess, ModelInvocationFailure):
        return None
    provenance = result._execution_evidence
    if type(provenance) is not ModelInvocationResultProvenance:
        return None
    return (
        provenance.attempt_id,
        provenance.attempt_evidence_sha256,
        provenance.normalized_result_evidence_sha256,
        provenance.raw_response_evidence_sha256,
        provenance.raw_response_body_sha256,
    )


# ---------------------------------------------------------------------------
# Strict read-only API


def execution_attempt_evidence_path(root: Path, run_id: str, attempt_id: str) -> Path:
    return _evidence_path(root, run_id, _ATTEMPT_PREFIX, attempt_id)


def execution_raw_response_evidence_path(
    root: Path, run_id: str, attempt_id: str
) -> Path:
    return _evidence_path(root, run_id, _RAW_PREFIX, attempt_id)


def execution_normalized_result_evidence_path(
    root: Path, run_id: str, attempt_id: str
) -> Path:
    return _evidence_path(root, run_id, _RESULT_PREFIX, attempt_id)


def load_execution_attempt_evidence(
    root: Path, run_id: str, attempt_id: str
) -> ExecutionAttemptEvidence:
    """Strictly load one immutable attempt claim."""
    manifest_digest = _validate_run_namespace(root, run_id)
    _validate_sha256(attempt_id, "attempt")
    path = execution_attempt_evidence_path(root, run_id, attempt_id)
    value = _load_record(path, (_ATTEMPT_V1_KEYS, _ATTEMPT_V2_KEYS), _parse_attempt)
    assert type(value) is ExecutionAttemptEvidence
    if (
        value.run_id != run_id
        or value.attempt_id != attempt_id
        or value.manifest_digest != manifest_digest
    ):
        _raise_load("identity")
    _validate_attempt_authority(root, value)
    return value


def load_raw_response_evidence(
    root: Path, run_id: str, attempt_id: str
) -> RawProviderResponseEvidence:
    """Deliberately retrieve exact raw bytes through the evidence API."""
    attempt = load_execution_attempt_evidence(root, run_id, attempt_id)
    _validate_sha256(attempt_id, "attempt")
    value = _load_record(
        execution_raw_response_evidence_path(root, run_id, attempt_id),
        _RAW_KEYS,
        _parse_raw,
    )
    assert type(value) is RawProviderResponseEvidence
    if (
        value.run_id != run_id
        or value.attempt_id != attempt_id
        or value.attempt_evidence_sha256 != attempt.digest
        or value.manifest_digest != attempt.manifest_digest
        or value.workflow_id != attempt.workflow_id
        or value.step_id != attempt.step_id
        or value.provider != attempt.provider
    ):
        _raise_load("raw_binding")
    return value


def load_normalized_result_evidence(
    root: Path, run_id: str, attempt_id: str
) -> NormalizedExecutionResultEvidence:
    """Strictly load the normalized result joined to an attempt/raw record."""
    attempt = load_execution_attempt_evidence(root, run_id, attempt_id)
    value = _load_record(
        execution_normalized_result_evidence_path(root, run_id, attempt_id),
        _RESULT_KEYS,
        _parse_result,
    )
    assert type(value) is NormalizedExecutionResultEvidence
    if (
        value.run_id != run_id
        or value.attempt_id != attempt_id
        or value.attempt_evidence_sha256 != attempt.digest
        or value.manifest_digest != attempt.manifest_digest
        or value.workflow_id != attempt.workflow_id
        or value.step_id != attempt.step_id
        or value.employee_id != attempt.employee_id
        or value.provider != attempt.provider
    ):
        _raise_load("result_binding")
    if value.raw_response_evidence_sha256 is not None:
        raw = load_raw_response_evidence(root, run_id, attempt_id)
        if (
            value.raw_response_evidence_sha256 != raw.digest
            or value.raw_response_body_sha256 != raw.body_sha256
        ):
            _raise_load("raw_binding")
    elif (
        execution_raw_response_evidence_path(root, run_id, attempt_id).exists()
        or execution_raw_response_evidence_path(root, run_id, attempt_id).is_symlink()
    ):
        _raise_load("raw_binding")
    return value


def list_run_execution_evidence(
    root: Path, run_id: str
) -> tuple[ExecutionAttemptEvidence, ...]:
    """Strictly load every attempt claim in one authoritative Run namespace."""
    manifest_digest = _validate_run_namespace(root, run_id)
    marker = f"{run_id}.{_ATTEMPT_PREFIX}."
    try:
        paths = sorted(root.glob(f"{marker}*.json"), key=lambda path: path.name)
    except Exception:
        _raise_load("listing")
    values: list[ExecutionAttemptEvidence] = []
    for path in paths:
        suffix = path.name.removeprefix(marker).removesuffix(".json")
        _validate_sha256(suffix, "identity", load=True)
        value = _load_record(
            path, (_ATTEMPT_V1_KEYS, _ATTEMPT_V2_KEYS), _parse_attempt
        )
        assert type(value) is ExecutionAttemptEvidence
        if (
            value.run_id != run_id
            or value.attempt_id != suffix
            or value.manifest_digest != manifest_digest
        ):
            _raise_load("identity")
        _validate_attempt_authority(root, value)
        values.append(value)
    attempt_ids = {value.attempt_id for value in values}
    for prefix in (_RAW_PREFIX, _RESULT_PREFIX):
        marker = f"{run_id}.{prefix}."
        try:
            sidecars = sorted(root.glob(f"{marker}*.json"), key=lambda item: item.name)
        except Exception:
            _raise_load("listing")
        for path in sidecars:
            suffix = path.name.removeprefix(marker).removesuffix(".json")
            _validate_sha256(suffix, "identity", load=True)
            if suffix not in attempt_ids:
                _raise_load("orphan")
    for attempt in values:
        raw_path = execution_raw_response_evidence_path(
            root, run_id, attempt.attempt_id
        )
        if raw_path.exists() or raw_path.is_symlink():
            load_raw_response_evidence(root, run_id, attempt.attempt_id)
        result_path = execution_normalized_result_evidence_path(
            root, run_id, attempt.attempt_id
        )
        if result_path.exists() or result_path.is_symlink():
            load_normalized_result_evidence(root, run_id, attempt.attempt_id)
    _validate_attempt_lineage(values)
    return tuple(values)


def inspect_run_execution_evidence(
    root: Path, run_id: str
) -> tuple[ExecutionEvidenceInspection, ...]:
    """Return safe metadata for a Run without provider/tool/network effects."""
    attempts = list_run_execution_evidence(root, run_id)
    terminal_events = _terminal_events(root, run_id)
    inspections: list[ExecutionEvidenceInspection] = []
    for attempt in attempts:
        raw_path = execution_raw_response_evidence_path(
            root, run_id, attempt.attempt_id
        )
        result_path = execution_normalized_result_evidence_path(
            root, run_id, attempt.attempt_id
        )
        raw: RawProviderResponseEvidence | None = None
        normalized: NormalizedExecutionResultEvidence | None = None
        if raw_path.exists() or raw_path.is_symlink():
            raw = load_raw_response_evidence(root, run_id, attempt.attempt_id)
        if result_path.exists() or result_path.is_symlink():
            normalized = load_normalized_result_evidence(
                root, run_id, attempt.attempt_id
            )
        category = None
        if normalized is not None:
            category = (
                "success"
                if normalized.category == "success"
                else cast(str, normalized.failure_category)
            )
        final_linked = normalized is not None and any(
            _event_links_evidence(
                event,
                attempt=attempt,
                raw=raw,
                normalized=normalized,
            )
            for event in terminal_events
        )
        ambiguous = (
            raw is None
            or normalized is None
            or not final_linked
            or (
                normalized is not None
                and normalized.failure_category == "transport_error"
            )
        )
        inspections.append(
            ExecutionEvidenceInspection(
                attempt_id=attempt.attempt_id,
                run_id=attempt.run_id,
                manifest_digest=attempt.manifest_digest,
                workflow_id=attempt.workflow_id,
                step_id=attempt.step_id,
                step_index=attempt.step_index,
                employee_id=attempt.employee_id,
                provider=attempt.provider,
                invocation_fingerprint=attempt.invocation_fingerprint,
                execution_target_fingerprint=attempt.execution_target_fingerprint,
                execution_approval_id=attempt.execution_approval_id,
                transport_claimed=True,
                raw_response_received=raw is not None,
                raw_response_evidence_sha256=(None if raw is None else raw.digest),
                raw_response_body_sha256=(None if raw is None else raw.body_sha256),
                normalized_result_evidence_sha256=(
                    None if normalized is None else normalized.digest
                ),
                normalized_result_category=category,
                ambiguous_or_unresolved=ambiguous,
                final_step_outcome_linked=final_linked,
                previous_attempt_id=attempt.previous_attempt_id,
                recovery_approval_id=attempt.recovery_approval_id,
                recovery_decision_sha256=attempt.recovery_decision_sha256,
            )
        )
    return tuple(inspections)


def _validate_run_terminal_event(root: Path, binding: object, event: object) -> None:
    """Require a Run's terminal event to match its authoritative evidence."""
    run_id = getattr(binding, "run_id", None)
    manifest_digest = getattr(binding, "manifest_digest", None)
    if type(run_id) is not str or type(manifest_digest) is not str:
        _raise_load("terminal_binding")

    linkage = tuple(
        getattr(event, name, None)
        for name in (
            "execution_attempt_id",
            "execution_attempt_evidence_sha256",
            "normalized_result_evidence_sha256",
            "raw_response_evidence_sha256",
            "raw_response_body_sha256",
        )
    )
    event_identity = (
        getattr(event, "workflow_id", None),
        getattr(event, "step_id", None),
        getattr(event, "step_index", None),
        getattr(event, "employee_id", None),
    )

    if getattr(event, "event_type", None) == "step_recovery_started":
        if (
            type(linkage[0]) is not str
            or type(linkage[1]) is not str
            or any(value is not None for value in linkage[2:])
        ):
            _raise_load("recovery_event")
        attempt = load_execution_attempt_evidence(root, run_id, linkage[0])
        if (
            attempt.schema_version != _RECOVERY_ATTEMPT_SCHEMA
            or attempt.digest != linkage[1]
            or attempt.manifest_digest != manifest_digest
            or (
                attempt.workflow_id,
                attempt.step_id,
                attempt.step_index,
                attempt.employee_id,
                attempt.provider,
            )
            != (*event_identity, getattr(event, "provider", None))
        ):
            _raise_load("recovery_event")
        return

    if not any(value is not None for value in linkage):
        attempts = list_run_execution_evidence(root, run_id)
        matching_attempt_exists = any(
            (
                attempt.workflow_id,
                attempt.step_id,
                attempt.step_index,
                attempt.employee_id,
            )
            == event_identity
            for attempt in attempts
        )
        requires_evidence = (
            getattr(event, "event_type", None) == "step_succeeded"
            or getattr(event, "failure_category", None)
            in {"api_error", "transport_error", "invalid_response", "invalid_output"}
        )
        if requires_evidence or matching_attempt_exists:
            _raise_load("terminal_evidence")
        return

    attempt_id, attempt_digest, result_digest, raw_digest, raw_body_digest = linkage
    if (
        any(type(value) is not str for value in linkage[:3])
        or (raw_digest is None) != (raw_body_digest is None)
    ):
        _raise_load("terminal_evidence")

    attempt = load_execution_attempt_evidence(root, run_id, cast(str, attempt_id))
    if (
        attempt.digest != attempt_digest
        or attempt.manifest_digest != manifest_digest
        or (
            attempt.workflow_id,
            attempt.step_id,
            attempt.step_index,
            attempt.employee_id,
            attempt.provider,
        )
        != (*event_identity, getattr(event, "provider", None))
    ):
        _raise_load("terminal_attempt")

    normalized = load_normalized_result_evidence(root, run_id, attempt.attempt_id)
    if normalized.digest != result_digest:
        _raise_load("terminal_result")

    raw: RawProviderResponseEvidence | None = None
    if normalized.raw_response_evidence_sha256 is not None:
        raw = load_raw_response_evidence(root, run_id, attempt.attempt_id)
    if (
        (None if raw is None else raw.digest) != raw_digest
        or (None if raw is None else raw.body_sha256) != raw_body_digest
    ):
        _raise_load("terminal_raw_response")

    result = normalized.result
    if type(result) is ModelInvocationSuccess:
        matches_result = (
            getattr(event, "event_type", None) == "step_succeeded"
            and getattr(event, "next_status", None) == "succeeded"
            and getattr(event, "failure_category", None) is None
            and getattr(event, "response_id", None) == result.response_id
            and getattr(event, "request_id", None) == result.request_id
            and getattr(event, "output_text", None) == result.text
            and getattr(event, "message", None) is None
            and getattr(event, "response_diagnostics", None) is None
        )
    else:
        assert type(result) is ModelInvocationFailure
        matches_result = (
            getattr(event, "event_type", None) == "step_failed"
            and getattr(event, "next_status", None) == "failed"
            and getattr(event, "failure_category", None) == result.category
            and getattr(event, "response_id", None) is None
            and getattr(event, "request_id", None) == result.request_id
            and getattr(event, "output_text", None) is None
            and getattr(event, "message", None) == result.message
            and getattr(event, "response_diagnostics", None)
            == result.response_diagnostics
        )
    if not matches_result:
        _raise_load("terminal_outcome")


# Discoverable aliases; they do not introduce another authority or storage type.
persist_execution_attempt = claim_execution_attempt
load_execution_raw_response_evidence = load_raw_response_evidence
load_execution_normalized_result_evidence = load_normalized_result_evidence


# ---------------------------------------------------------------------------
# Construction and validation helpers


def _is_workflow_run_binding(value: object) -> bool:
    try:
        from ai_office.runtime.run_binding import WorkflowRunBinding
    except Exception:
        return False
    return type(value) is WorkflowRunBinding


def _validate_context(context: ExecutionEvidenceContext) -> None:
    if type(context.store_root) is not _PATH_TYPE:
        _raise_contract("store")
    try:
        if context.store_root.is_symlink() or not context.store_root.is_dir():
            _raise_contract("store")
    except ExecutionEvidenceError:
        raise
    except Exception:
        _raise_contract("store")
    if not _is_workflow_run_binding(context.binding):
        _raise_contract("run_binding")
    _validate_run_id(context.binding.run_id, load=False)
    _validate_sha256(context.binding.manifest_digest, "manifest")
    _validate_definition(context.workflow_id, "workflow")
    _validate_definition(context.step_id, "step")
    _validate_definition(context.employee_id, "employee")
    if (
        type(context.step_index) is not int
        or isinstance(context.step_index, bool)
        or context.step_index < 1
    ):
        _raise_contract("step")
    if type(context.request) is not ModelInvocationRequest:
        _raise_contract("request")
    if type(context.resolved_tools) is not tuple or any(
        type(tool) is not ToolDefinition for tool in context.resolved_tools
    ):
        _raise_contract("tools")
    if type(context.approval) is not ModelInvocationExecutionApproval:
        _raise_contract("approval")
    if type(context.target) is not ModelExecutionTarget:
        _raise_contract("target")
    try:
        target = validate_execution_target_for_provider(
            context.target, provider=context.approval.provider
        )
    except Exception:
        _raise_contract("target")
    if target != context.target:
        _raise_contract("target")
    if (
        context.request.run_id != context.binding.run_id
        or context.request.manifest_digest != context.binding.manifest_digest
    ):
        _raise_contract("run_binding")
    if (
        tuple(tool.name for tool in context.resolved_tools)
        != context.request.allowed_tools
    ):
        _raise_contract("tools")
    if (context.recovery_assessment is None) != (context.recovery_approval is None):
        _raise_contract("recovery")
    if context.recovery_assessment is not None:
        assessment = context.recovery_assessment
        if (
            getattr(assessment, "eligible", None) is not True
            or getattr(assessment, "action", None)
            not in {"retry_failed", "retry_ambiguous"}
            or getattr(assessment, "run_id", None) != context.binding.run_id
            or getattr(assessment, "manifest_digest", None)
            != context.binding.manifest_digest
            or getattr(assessment, "workflow_id", None) != context.workflow_id
            or getattr(assessment, "step_id", None) != context.step_id
            or getattr(assessment, "step_index", None) != context.step_index
            or getattr(assessment, "employee_id", None) != context.employee_id
            or getattr(assessment, "invocation_fingerprint", None)
            != context.approval.request_fingerprint
            or getattr(assessment, "provider", None) != context.target.provider
            or getattr(assessment, "execution_target_fingerprint", None)
            != execution_target_fingerprint(context.target)
        ):
            _raise_contract("recovery")


def _validate_context_and_approval(context: ExecutionEvidenceContext):
    _validate_context(context)
    try:
        validate_model_invocation_execution_approval(
            context.request,
            context.resolved_tools,
            context.approval,
            provider=context.target.provider,
            execution_target=context.target,
        )
    except Exception:
        _raise_contract("execution_approval")

    try:
        from ai_office.engine.workflow_approval_evidence import (
            load_execution_approval_evidence,
            validate_execution_approval_evidence,
        )
        from ai_office.engine.workflow_run_manifest import (
            WorkflowRunManifestStore,
            load_workflow_run_manifest,
        )

        store = WorkflowRunManifestStore(context.store_root)
        manifest = load_workflow_run_manifest(store, context.binding.run_id)
        if (
            manifest.workflow_id != context.workflow_id
            or manifest.digest != context.binding.manifest_digest
        ):
            _raise_contract("manifest")
        evidence = load_execution_approval_evidence(
            store,
            context.binding.run_id,
            context.approval.approval_id,
        )
        validate_execution_approval_evidence(
            evidence,
            binding=context.binding,
            workflow_id=context.workflow_id,
            step_id=context.step_id,
            step_index=context.step_index,
            employee_id=context.employee_id,
            provider=context.target.provider,
            execution_target_fingerprint_value=execution_target_fingerprint(
                context.target
            ),
            request_fingerprint=context.approval.request_fingerprint,
        )
        if context.recovery_assessment is not None:
            from ai_office.engine.workflow_recovery import (
                validate_workflow_recovery_authorization,
            )

            validate_workflow_recovery_authorization(
                context.store_root,
                context.recovery_assessment,
                context.recovery_approval,
            )
        return evidence
    except ExecutionEvidenceError:
        raise
    except Exception:
        _raise_contract("approval_evidence")


def _validated_unauthenticated_request(
    context: ExecutionEvidenceContext, request: object
) -> str:
    if type(getattr(request, "method", None)) is not str:
        _raise_contract("request")
    if getattr(request, "method") != "POST":
        _raise_contract("request")
    if type(getattr(request, "url", None)) is not str:
        _raise_contract("target")
    if getattr(request, "url") != context.target.endpoint:
        _raise_contract("target_binding")
    body = getattr(request, "body", None)
    if type(body) is not str:
        _raise_contract("request_body")
    headers = getattr(request, "headers", None)
    if type(headers) is not tuple:
        _raise_contract("request_headers")
    for item in headers:
        if (
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not str
        ):
            _raise_contract("request_headers")
        if item[0].lower() == "authorization":
            _raise_contract("credential")
    return body


def _build_attempt(
    context: ExecutionEvidenceContext,
    body: str,
    approval_evidence_digest: str,
) -> ExecutionAttemptEvidence:
    target = context.target
    tool_value = [
        {
            "description": tool.description,
            "name": tool.name,
            "parameters": [
                {
                    "description": parameter.description,
                    "name": parameter.name,
                    "required": parameter.required,
                    "type": parameter.type,
                }
                for parameter in tool.parameters
            ],
        }
        for tool in context.resolved_tools
    ]
    task_input = build_model_invocation_task_input(context.request).encode("utf-8")
    recovery_assessment = context.recovery_assessment
    recovery_approval = context.recovery_approval
    values: dict[str, object] = {
        "approved_by": context.approval.approved_by,
        "employee_id": context.employee_id,
        "execution_approval_evidence_sha256": approval_evidence_digest,
        "execution_approval_id": context.approval.approval_id,
        "execution_target_allow_loopback_http": target.allow_loopback_http,
        "execution_target_endpoint": target.endpoint,
        "execution_target_fingerprint": execution_target_fingerprint(target),
        "execution_target_protocol": target.protocol,
        "invocation_fingerprint": context.approval.request_fingerprint,
        "manifest_digest": context.binding.manifest_digest,
        "provider": target.provider,
        "request_body_length": len(body.encode("utf-8")),
        "request_body_sha256": _digest(body.encode("utf-8")),
        "resolved_tools_sha256": _digest(_canonical_json(tool_value)),
        "run_id": context.binding.run_id,
        "schema_version": (
            _ATTEMPT_SCHEMA
            if recovery_assessment is None
            else _RECOVERY_ATTEMPT_SCHEMA
        ),
        "state": "claimed",
        "step_id": context.step_id,
        "step_index": context.step_index,
        "system_instruction_sha256": _digest(
            context.request.system_instructions.encode("utf-8")
        ),
        "task_input_sha256": _digest(task_input),
        "workflow_id": context.workflow_id,
    }
    if recovery_assessment is not None and recovery_approval is not None:
        values.update(
            {
                "previous_attempt_evidence_sha256": (
                    recovery_assessment.previous_attempt_evidence_sha256
                ),
                "previous_attempt_id": recovery_assessment.previous_attempt_id,
                "recovery_approval_evidence_sha256": recovery_approval.digest,
                "recovery_approval_id": recovery_approval.approval_id,
                "recovery_decision_sha256": recovery_assessment.digest,
            }
        )
    attempt_id = _digest(_canonical_json(values))
    return ExecutionAttemptEvidence(
        attempt_id=attempt_id,
        **values,  # type: ignore[arg-type]
    )


def _build_result_record(
    context: ExecutionEvidenceContext,
    attempt: ExecutionAttemptEvidence,
    result: ModelInvocationResult,
    raw: RawProviderResponseEvidence | None,
) -> NormalizedExecutionResultEvidence:
    raw_digest = None if raw is None else raw.digest
    raw_body_digest = None if raw is None else raw.body_sha256
    raw_status = None if raw is None else raw.status_code
    if type(result) is ModelInvocationSuccess:
        values: dict[str, object] = {
            "category": "success",
            "failure_category": None,
            "failure_message": None,
            "provider_error_code": None,
            "provider_error_type": None,
            "response_diagnostics": None,
            "response_id": result.response_id,
            "request_id": result.request_id,
            "status": result.status,
            "status_code": raw_status,
            "text": result.text,
            "text_parts": result.text_parts,
        }
    else:
        assert type(result) is ModelInvocationFailure
        values = {
            "category": "failure",
            "failure_category": result.category,
            "failure_message": result.message,
            "provider_error_code": result.provider_error_code,
            "provider_error_type": result.provider_error_type,
            "response_diagnostics": result.response_diagnostics,
            "response_id": None,
            "request_id": result.request_id,
            "status": None,
            "status_code": result.status_code,
            "text": None,
            "text_parts": None,
        }
    base: dict[str, object] = {
        "attempt_evidence_sha256": attempt.digest,
        "attempt_id": attempt.attempt_id,
        "category": values["category"],
        "employee_id": context.employee_id,
        "failure_category": values["failure_category"],
        "failure_message": values["failure_message"],
        "manifest_digest": context.binding.manifest_digest,
        "normalized_result_sha256": "0" * 64,
        "provider": context.target.provider,
        "provider_error_code": values["provider_error_code"],
        "provider_error_type": values["provider_error_type"],
        "raw_response_body_sha256": raw_body_digest,
        "raw_response_evidence_sha256": raw_digest,
        "request_id": values["request_id"],
        "response_diagnostics": values["response_diagnostics"],
        "response_id": values["response_id"],
        "run_id": context.binding.run_id,
        "schema_version": _RESULT_SCHEMA,
        "status": values["status"],
        "status_code": values["status_code"],
        "step_id": context.step_id,
        "text": values["text"],
        "text_parts": values["text_parts"],
        "workflow_id": context.workflow_id,
    }
    digest_values = {
        key: value
        for key, value in base.items()
        if key != "normalized_result_sha256"
    }
    diagnostics = values["response_diagnostics"]
    if diagnostics is not None:
        assert type(diagnostics) is ModelInvocationFailureDiagnostics
        digest_values["response_diagnostics"] = _response_diagnostics_dict(
            diagnostics
        )
    base["normalized_result_sha256"] = _digest(_canonical_json(digest_values))
    return NormalizedExecutionResultEvidence(**base)  # type: ignore[arg-type]


def _validate_attempt_context(
    context: ExecutionEvidenceContext, attempt: ExecutionAttemptEvidence
) -> None:
    _validate_context(context)
    tool_value = [
        {
            "description": tool.description,
            "name": tool.name,
            "parameters": [
                {
                    "description": parameter.description,
                    "name": parameter.name,
                    "required": parameter.required,
                    "type": parameter.type,
                }
                for parameter in tool.parameters
            ],
        }
        for tool in context.resolved_tools
    ]
    if not (
        _same_step(attempt, context)
        and attempt.provider == context.target.provider
        and attempt.invocation_fingerprint == context.approval.request_fingerprint
        and attempt.execution_target_fingerprint
        == execution_target_fingerprint(context.target)
        and attempt.system_instruction_sha256
        == _digest(context.request.system_instructions.encode("utf-8"))
        and attempt.task_input_sha256
        == _digest(build_model_invocation_task_input(context.request).encode("utf-8"))
        and attempt.resolved_tools_sha256 == _digest(_canonical_json(tool_value))
    ):
        _raise_contract("attempt_binding")


def _validate_raw_context(
    context: ExecutionEvidenceContext,
    attempt: ExecutionAttemptEvidence,
    raw: RawProviderResponseEvidence,
) -> None:
    if not (
        raw.attempt_id == attempt.attempt_id
        and raw.attempt_evidence_sha256 == attempt.digest
        and raw.run_id == context.binding.run_id
        and raw.manifest_digest == context.binding.manifest_digest
        and raw.workflow_id == context.workflow_id
        and raw.step_id == context.step_id
        and raw.provider == context.target.provider
    ):
        _raise_contract("raw_binding")


def _require_persisted_attempt(
    context: ExecutionEvidenceContext, attempt: ExecutionAttemptEvidence
) -> None:
    loaded = load_execution_attempt_evidence(
        context.store_root, attempt.run_id, attempt.attempt_id
    )
    if loaded != attempt:
        _raise_contract("attempt_binding")


def _require_persisted_raw(
    context: ExecutionEvidenceContext, raw: RawProviderResponseEvidence
) -> None:
    loaded = load_raw_response_evidence(context.store_root, raw.run_id, raw.attempt_id)
    if loaded != raw:
        _raise_contract("raw_binding")


def _same_step(
    attempt: ExecutionAttemptEvidence, context: ExecutionEvidenceContext
) -> bool:
    return (
        attempt.run_id == context.binding.run_id
        and attempt.manifest_digest == context.binding.manifest_digest
        and attempt.workflow_id == context.workflow_id
        and attempt.step_id == context.step_id
        and attempt.step_index == context.step_index
        and attempt.employee_id == context.employee_id
    )


# ---------------------------------------------------------------------------
# Durable record encoding/decoding


def _attempt_dict(value: ExecutionAttemptEvidence) -> dict[str, object]:
    record: dict[str, object] = {
        "approved_by": value.approved_by,
        "attempt_id": value.attempt_id,
        "employee_id": value.employee_id,
        "execution_approval_evidence_sha256": value.execution_approval_evidence_sha256,
        "execution_approval_id": value.execution_approval_id,
        "execution_target_allow_loopback_http": (
            value.execution_target_allow_loopback_http
        ),
        "execution_target_endpoint": value.execution_target_endpoint,
        "execution_target_fingerprint": value.execution_target_fingerprint,
        "execution_target_protocol": value.execution_target_protocol,
        "invocation_fingerprint": value.invocation_fingerprint,
        "manifest_digest": value.manifest_digest,
        "provider": value.provider,
        "request_body_length": value.request_body_length,
        "request_body_sha256": value.request_body_sha256,
        "resolved_tools_sha256": value.resolved_tools_sha256,
        "run_id": value.run_id,
        "schema_version": value.schema_version,
        "state": value.state,
        "step_id": value.step_id,
        "step_index": value.step_index,
        "system_instruction_sha256": value.system_instruction_sha256,
        "task_input_sha256": value.task_input_sha256,
        "workflow_id": value.workflow_id,
    }
    if value.schema_version == _RECOVERY_ATTEMPT_SCHEMA:
        record.update(
            {
                "previous_attempt_evidence_sha256": (
                    value.previous_attempt_evidence_sha256
                ),
                "previous_attempt_id": value.previous_attempt_id,
                "recovery_approval_evidence_sha256": (
                    value.recovery_approval_evidence_sha256
                ),
                "recovery_approval_id": value.recovery_approval_id,
                "recovery_decision_sha256": value.recovery_decision_sha256,
            }
        )
    return record


def _raw_dict(value: RawProviderResponseEvidence) -> dict[str, object]:
    return {
        "attempt_evidence_sha256": value.attempt_evidence_sha256,
        "attempt_id": value.attempt_id,
        "body_base64": value.body_base64,
        "body_length": value.body_length,
        "body_sha256": value.body_sha256,
        "manifest_digest": value.manifest_digest,
        "provider": value.provider,
        "request_id": value.request_id,
        "run_id": value.run_id,
        "safe_headers": [list(item) for item in value.safe_headers],
        "schema_version": value.schema_version,
        "status_code": value.status_code,
        "step_id": value.step_id,
        "workflow_id": value.workflow_id,
    }


def _result_dict(value: NormalizedExecutionResultEvidence) -> dict[str, object]:
    diagnostics = value.response_diagnostics
    return {
        "attempt_evidence_sha256": value.attempt_evidence_sha256,
        "attempt_id": value.attempt_id,
        "category": value.category,
        "employee_id": value.employee_id,
        "failure_category": value.failure_category,
        "failure_message": value.failure_message,
        "manifest_digest": value.manifest_digest,
        "normalized_result_sha256": value.normalized_result_sha256,
        "provider": value.provider,
        "provider_error_code": value.provider_error_code,
        "provider_error_type": value.provider_error_type,
        "raw_response_body_sha256": value.raw_response_body_sha256,
        "raw_response_evidence_sha256": value.raw_response_evidence_sha256,
        "request_id": value.request_id,
        "response_diagnostics": (
            None if diagnostics is None else _response_diagnostics_dict(diagnostics)
        ),
        "response_id": value.response_id,
        "run_id": value.run_id,
        "schema_version": value.schema_version,
        "status": value.status,
        "status_code": value.status_code,
        "step_id": value.step_id,
        "text": value.text,
        "text_parts": None if value.text_parts is None else list(value.text_parts),
        "workflow_id": value.workflow_id,
    }


def _response_diagnostics_dict(
    diagnostics: ModelInvocationFailureDiagnostics,
) -> dict[str, object]:
    return {
        "body_kind": diagnostics.body_kind,
        "body_length": diagnostics.body_length,
        "content_type": diagnostics.content_type,
        "status_code": diagnostics.status_code,
    }


def _parse_attempt(value: Mapping[str, object]) -> ExecutionAttemptEvidence:
    try:
        record = ExecutionAttemptEvidence(**value)  # type: ignore[arg-type]
    except Exception:
        _raise_load("attempt")
    return record


def _parse_raw(value: Mapping[str, object]) -> RawProviderResponseEvidence:
    try:
        headers = value["safe_headers"]
        if type(headers) is not list or any(
            type(item) is not list or len(item) != 2 for item in headers
        ):
            _raise_load("raw_headers")
        pairs = tuple((item[0], item[1]) for item in headers)
        return RawProviderResponseEvidence(
            **{**value, "safe_headers": pairs}  # type: ignore[arg-type]
        )
    except ExecutionEvidenceLoadError:
        raise
    except Exception:
        _raise_load("raw")


def _parse_result(value: Mapping[str, object]) -> NormalizedExecutionResultEvidence:
    try:
        diagnostics_value = value["response_diagnostics"]
        diagnostics = None
        if diagnostics_value is not None:
            if type(diagnostics_value) is not dict:
                _raise_load("diagnostics")
            diagnostics = ModelInvocationFailureDiagnostics(**diagnostics_value)
        text_parts = value["text_parts"]
        if text_parts is not None:
            if type(text_parts) is not list:
                _raise_load("text_parts")
            text_parts = tuple(text_parts)
        return NormalizedExecutionResultEvidence(
            **{
                **value,
                "response_diagnostics": diagnostics,
                "text_parts": text_parts,
            }  # type: ignore[arg-type]
        )
    except ExecutionEvidenceLoadError:
        raise
    except Exception:
        _raise_load("result")


def _load_record(
    path: Path,
    keys: frozenset[str] | tuple[frozenset[str], ...],
    parser,
):
    if type(path) is not _PATH_TYPE or path.is_symlink() or not path.is_file():
        _raise_load("target")
    try:
        contents = path.read_bytes()
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except Exception:
        _raise_load("parse")
    allowed = keys if isinstance(keys, tuple) else (keys,)
    if type(value) is not dict or frozenset(value) not in allowed:
        _raise_load("fields")
    try:
        record = parser(value)
        canonical = _canonical_record_bytes(record)
    except ExecutionEvidenceLoadError:
        raise
    except Exception:
        _raise_load("record")
    if canonical != contents:
        _raise_load("noncanonical")
    return record


def _canonical_record_bytes(value: object) -> bytes:
    if type(value) is ExecutionAttemptEvidence:
        return _canonical_json(_attempt_dict(value))
    if type(value) is RawProviderResponseEvidence:
        return _canonical_json(_raw_dict(value))
    if type(value) is NormalizedExecutionResultEvidence:
        return _canonical_json(_result_dict(value))
    _raise_load("record")


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except Exception:
        _raise_contract("serialization")


def _persist_attempt(path: Path, contents: bytes) -> None:
    _validate_persist_path(path)
    try:
        handle = path.open("xb")
    except FileExistsError:
        raise ExecutionAttemptAlreadyClaimedError from None
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise ExecutionAttemptAlreadyClaimedError from None
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")
    _persist_new(handle, path.parent, contents)


def _persist_idempotent(path: Path, contents: bytes) -> None:
    _validate_persist_path(path)
    try:
        handle = path.open("xb")
    except FileExistsError:
        _accept_existing(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _accept_existing(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")
    _persist_new(handle, path.parent, contents)


def _accept_existing(path: Path, contents: bytes) -> None:
    try:
        if path.is_symlink() or not path.is_file():
            _raise_persistence("target")
        existing = path.read_bytes()
    except ExecutionEvidencePersistenceError:
        raise
    except Exception:
        _raise_persistence("target")
    if existing != contents:
        _raise_conflict()


def _persist_new(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _handle_scope(handle) as active:
            written = active.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short evidence write")
            active.flush()  # type: ignore[attr-defined]
            os.fsync(active.fileno())  # type: ignore[attr-defined]
    except Exception:
        # Exclusive creation succeeded; retain the target.  A later strict load
        # decides whether it is usable, and normal execution must not replay it.
        _raise_persistence("ambiguous")
    try:
        _fsync_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _handle_scope(handle: object) -> Iterator[object]:
    enter = getattr(handle, "__enter__", None)
    exit_ = getattr(handle, "__exit__", None)
    if callable(enter) and callable(exit_):
        with handle as active:
            yield active
        return
    try:
        yield handle
    finally:
        close = getattr(handle, "close", None)
        if not callable(close):
            raise OSError("evidence handle cannot close")
        close()


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(
        os.fspath(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_persist_path(path: Path) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_persistence("path_type")
    try:
        if (
            not path.parent.is_dir()
            or path.parent.is_symlink()
            or path.is_symlink()
            or path.is_dir()
            or (path.exists() and not path.is_file())
        ):
            _raise_persistence("target")
    except ExecutionEvidencePersistenceError:
        raise
    except Exception:
        _raise_persistence("target")


# ---------------------------------------------------------------------------
# Validation, namespaces and inspection linkage


def _validate_attempt(value: object, *, load: bool = False) -> None:
    fail = _raise_load if load else _raise_contract
    if type(value) is not ExecutionAttemptEvidence:
        fail("attempt")
    assert isinstance(value, ExecutionAttemptEvidence)
    if (
        value.schema_version not in {_ATTEMPT_SCHEMA, _RECOVERY_ATTEMPT_SCHEMA}
        or value.state != "claimed"
    ):
        fail("attempt")
    _validate_sha256(value.attempt_id, "attempt", load=load)
    _validate_run_id(value.run_id, load=load)
    _validate_sha256(value.manifest_digest, "manifest", load=load)
    for field, item in (
        ("workflow", value.workflow_id),
        ("step", value.step_id),
        ("employee", value.employee_id),
        ("provider", value.provider),
        ("endpoint", value.execution_target_endpoint),
        ("protocol", value.execution_target_protocol),
        ("approval", value.execution_approval_id),
        ("approved_by", value.approved_by),
    ):
        _validate_text(item, field, load=load)
    if value.provider in SUPPORTED_EXECUTION_PROVIDERS:
        try:
            target = validate_execution_target_for_provider(
                execution_target_for_name(value.provider), provider=value.provider
            )
        except Exception:
            fail("target")
        if (
            value.execution_target_endpoint != target.endpoint
            or value.execution_target_protocol != target.protocol
            or value.execution_target_allow_loopback_http != target.allow_loopback_http
            or value.execution_target_fingerprint
            != execution_target_fingerprint(target)
        ):
            fail("target")
    else:
        try:
            canonical_endpoint = canonicalize_execution_target_url(
                value.execution_target_endpoint
            )
        except Exception:
            fail("target")
        if (
            not is_supported_execution_provider(value.provider)
            or value.execution_target_protocol not in SUPPORTED_EXECUTION_PROTOCOLS
            or value.execution_target_allow_loopback_http
            or canonical_endpoint != value.execution_target_endpoint
            or not canonical_endpoint.startswith("https://")
        ):
            fail("target")
    if (
        type(value.step_index) is not int
        or isinstance(value.step_index, bool)
        or value.step_index < 1
    ):
        fail("attempt")
    if type(value.execution_target_allow_loopback_http) is not bool:
        fail("attempt")
    for field, item in (
        ("target", value.execution_target_fingerprint),
        ("invocation", value.invocation_fingerprint),
        ("system", value.system_instruction_sha256),
        ("task", value.task_input_sha256),
        ("tools", value.resolved_tools_sha256),
        ("body", value.request_body_sha256),
        ("approval_evidence", value.execution_approval_evidence_sha256),
    ):
        _validate_sha256(item, field, load=load)
    recovery_values = (
        value.recovery_decision_sha256,
        value.recovery_approval_id,
        value.recovery_approval_evidence_sha256,
        value.previous_attempt_id,
        value.previous_attempt_evidence_sha256,
    )
    if value.schema_version == _ATTEMPT_SCHEMA:
        if any(item is not None for item in recovery_values):
            fail("attempt_lineage")
    else:
        if (
            any(item is None for item in recovery_values)
            or value.recovery_approval_id == value.execution_approval_id
        ):
            fail("attempt_lineage")
        _validate_sha256(value.recovery_decision_sha256, "recovery_decision", load=load)
        _validate_sha256(
            value.recovery_approval_evidence_sha256,
            "recovery_approval_evidence",
            load=load,
        )
        _validate_sha256(
            value.previous_attempt_evidence_sha256, "previous_attempt", load=load
        )
        _validate_sha256(value.previous_attempt_id, "previous_attempt", load=load)
        _validate_text(value.recovery_approval_id, "recovery_approval", load=load)
    if (
        type(value.request_body_length) is not int
        or isinstance(value.request_body_length, bool)
        or value.request_body_length < 0
    ):
        fail("attempt")
    expected_id = _digest(
        _canonical_json(
            {
                key: item
                for key, item in _attempt_dict(value).items()
                if key != "attempt_id"
            }
        )
    )
    if expected_id != value.attempt_id:
        fail("integrity")


def _validate_attempt_authority(root: Path, value: ExecutionAttemptEvidence) -> None:
    """Rejoin an attempt to Milestone 2's sole approval authority."""
    try:
        from ai_office.engine.workflow_approval_evidence import (
            load_execution_approval_evidence,
        )
        from ai_office.engine.workflow_run_manifest import WorkflowRunManifestStore

        approval = load_execution_approval_evidence(
            WorkflowRunManifestStore(root),
            value.run_id,
            value.execution_approval_id,
        )
    except Exception:
        _raise_load("approval_binding")
    if not (
        approval.digest == value.execution_approval_evidence_sha256
        and approval.run_id == value.run_id
        and approval.manifest_digest == value.manifest_digest
        and approval.workflow_id == value.workflow_id
        and approval.step_id == value.step_id
        and approval.step_index == value.step_index
        and approval.employee_id == value.employee_id
        and approval.provider == value.provider
        and approval.execution_target_fingerprint == value.execution_target_fingerprint
        and approval.request_fingerprint == value.invocation_fingerprint
        and approval.approved_by == value.approved_by
        and approval.approval_id == value.execution_approval_id
    ):
        _raise_load("approval_binding")
    if value.schema_version == _RECOVERY_ATTEMPT_SCHEMA:
        try:
            from ai_office.engine.workflow_approval_evidence import (
                load_recovery_approval_evidence,
            )
            from ai_office.engine.workflow_run_manifest import (
                WorkflowRunManifestStore,
            )

            store = WorkflowRunManifestStore(root)
            recovery_approval = load_recovery_approval_evidence(
                store, value.run_id, cast(str, value.recovery_approval_id)
            )
            previous = load_execution_attempt_evidence(
                root, value.run_id, cast(str, value.previous_attempt_id)
            )
        except Exception:
            _raise_load("recovery_binding")
        if not (
            recovery_approval.digest == value.recovery_approval_evidence_sha256
            and recovery_approval.run_id == value.run_id
            and recovery_approval.manifest_digest == value.manifest_digest
            and recovery_approval.workflow_id == value.workflow_id
            and recovery_approval.step_id == value.step_id
            and recovery_approval.step_index == value.step_index
            and recovery_approval.employee_id == value.employee_id
            and recovery_approval.recovery_action
            in {"retry_failed", "retry_ambiguous"}
            and recovery_approval.recovery_decision_sha256
            == value.recovery_decision_sha256
            and recovery_approval.previous_attempt_id == previous.attempt_id
            and recovery_approval.previous_attempt_evidence_sha256 == previous.digest
            and previous.attempt_id == value.previous_attempt_id
            and previous.digest == value.previous_attempt_evidence_sha256
            and previous.schema_version in {_ATTEMPT_SCHEMA, _RECOVERY_ATTEMPT_SCHEMA}
            and (
                previous.run_id,
                previous.manifest_digest,
                previous.workflow_id,
                previous.step_id,
                previous.step_index,
                previous.employee_id,
            )
            == (
                value.run_id,
                value.manifest_digest,
                value.workflow_id,
                value.step_id,
                value.step_index,
                value.employee_id,
            )
            and previous.execution_approval_id != value.execution_approval_id
            and previous.execution_approval_evidence_sha256
            != value.execution_approval_evidence_sha256
        ):
            _raise_load("recovery_binding")


def _validate_attempt_lineage(
    attempts: list[ExecutionAttemptEvidence],
) -> None:
    """Require each same-step attempt set to be one immutable recovery chain."""
    groups: dict[tuple[object, ...], list[ExecutionAttemptEvidence]] = {}
    for attempt in attempts:
        key = (
            attempt.run_id,
            attempt.manifest_digest,
            attempt.workflow_id,
            attempt.step_id,
            attempt.step_index,
            attempt.employee_id,
        )
        groups.setdefault(key, []).append(attempt)

    for group in groups.values():
        by_id = {attempt.attempt_id: attempt for attempt in group}
        roots = [
            attempt
            for attempt in group
            if attempt.schema_version == _ATTEMPT_SCHEMA
            and attempt.previous_attempt_id is None
        ]
        if len(roots) != 1 or len(by_id) != len(group):
            _raise_load("attempt_lineage")
        children: dict[str, ExecutionAttemptEvidence] = {}
        recovery_approval_ids: set[str] = set()
        execution_approval_ids: set[str] = set()
        for attempt in group:
            if attempt.execution_approval_id in execution_approval_ids:
                _raise_load("attempt_lineage")
            execution_approval_ids.add(attempt.execution_approval_id)
            if attempt.schema_version == _ATTEMPT_SCHEMA:
                continue
            parent_id = cast(str, attempt.previous_attempt_id)
            if parent_id in children:
                _raise_load("attempt_lineage")
            parent = by_id.get(parent_id)
            if (
                parent is None
                or parent.digest != attempt.previous_attempt_evidence_sha256
                or attempt.recovery_approval_id in recovery_approval_ids
            ):
                _raise_load("attempt_lineage")
            recovery_approval_ids.add(cast(str, attempt.recovery_approval_id))
            children[parent_id] = attempt

        visited: set[str] = set()
        current = roots[0]
        while current.attempt_id not in visited:
            visited.add(current.attempt_id)
            child = children.get(current.attempt_id)
            if child is None:
                break
            current = child
        if len(visited) != len(group):
            _raise_load("attempt_lineage")


def _validate_raw(value: object, *, load: bool = False) -> None:
    fail = _raise_load if load else _raise_contract
    if type(value) is not RawProviderResponseEvidence:
        fail("raw")
    assert isinstance(value, RawProviderResponseEvidence)
    if value.schema_version != _RAW_SCHEMA:
        fail("raw")
    _validate_sha256(value.attempt_id, "attempt", load=load)
    _validate_sha256(value.attempt_evidence_sha256, "attempt_evidence", load=load)
    _validate_run_id(value.run_id, load=load)
    _validate_sha256(value.manifest_digest, "manifest", load=load)
    for field, item in (
        ("workflow", value.workflow_id),
        ("step", value.step_id),
        ("provider", value.provider),
    ):
        _validate_text(item, field, load=load)
    if (
        type(value.status_code) is not int
        or isinstance(value.status_code, bool)
        or not 100 <= value.status_code <= 599
    ):
        fail("raw")
    if value.request_id is not None:
        _validate_text(value.request_id, "request_id", load=load)
    if type(value.safe_headers) is not tuple:
        fail("raw_headers")
    for name, item in value.safe_headers:
        if (
            type(name) is not str
            or name != name.lower()
            or name not in _SAFE_RESPONSE_HEADERS
        ):
            fail("raw_headers")
        _validate_text(item, "raw_headers", load=load)
    if _response_request_id(value.safe_headers) != value.request_id:
        fail("raw_headers")
    try:
        body = base64.b64decode(value.body_base64.encode("ascii"), validate=True)
    except Exception:
        fail("raw_body")
    if (
        value.body_base64 != base64.b64encode(body).decode("ascii")
        or value.body_length != len(body)
        or value.body_sha256 != _digest(body)
        or value.body != body
    ):
        fail("raw_body")
    if value.digest != _digest(_canonical_json(_raw_dict(value))):
        fail("integrity")


def _validate_result(value: object, *, load: bool = False) -> None:
    fail = _raise_load if load else _raise_contract
    if type(value) is not NormalizedExecutionResultEvidence:
        fail("result")
    assert isinstance(value, NormalizedExecutionResultEvidence)
    if value.schema_version != _RESULT_SCHEMA or value.category not in {
        "success",
        "failure",
    }:
        fail("result")
    _validate_sha256(value.attempt_id, "attempt", load=load)
    _validate_sha256(value.attempt_evidence_sha256, "attempt_evidence", load=load)
    _validate_sha256(value.normalized_result_sha256, "normalized_result", load=load)
    _validate_run_id(value.run_id, load=load)
    _validate_sha256(value.manifest_digest, "manifest", load=load)
    for field, item in (
        ("workflow", value.workflow_id),
        ("step", value.step_id),
        ("employee", value.employee_id),
        ("provider", value.provider),
    ):
        _validate_text(item, field, load=load)
    for field, item in (
        ("raw", value.raw_response_evidence_sha256),
        ("raw_body", value.raw_response_body_sha256),
    ):
        if item is not None:
            _validate_sha256(item, field, load=load)
    if (value.raw_response_evidence_sha256 is None) != (
        value.raw_response_body_sha256 is None
    ):
        fail("raw_binding")
    if value.category == "success":
        if (
            not value.response_id
            or not value.status
            or value.text_parts is None
            or value.text is None
            or value.text != "".join(value.text_parts)
            or value.failure_category is not None
            or value.failure_message is not None
        ):
            fail("result")
        if value.raw_response_evidence_sha256 is None:
            fail("raw_binding")
        if type(value.text_parts) is not tuple or any(
            type(part) is not str for part in value.text_parts
        ):
            fail("result")
    else:
        if value.failure_category not in _FAILURE_CATEGORIES:
            fail("result")
        _validate_text(value.failure_message, "failure_message", load=load)
        if (
            value.failure_category in _RESPONSE_DERIVED_CATEGORIES
            and value.raw_response_evidence_sha256 is None
        ):
            fail("raw_binding")
        if (
            value.response_id is not None
            or value.status is not None
            or value.text is not None
            or value.text_parts is not None
        ):
            fail("result")
    for field, item in (
        ("request_id", value.request_id),
        ("provider_error_type", value.provider_error_type),
        ("provider_error_code", value.provider_error_code),
    ):
        if item is not None:
            _validate_text(item, field, load=load)
    if value.status_code is not None and (
        type(value.status_code) is not int
        or isinstance(value.status_code, bool)
        or not 100 <= value.status_code <= 599
    ):
        fail("result")
    diagnostics = value.response_diagnostics
    if diagnostics is not None:
        if (
            type(diagnostics) is not ModelInvocationFailureDiagnostics
            or type(diagnostics.status_code) is not int
            or type(diagnostics.body_length) is not int
            or diagnostics.body_length < 0
            or diagnostics.body_kind not in _RESPONSE_BODY_KINDS
            or (
                diagnostics.content_type is not None
                and type(diagnostics.content_type) is not str
            )
        ):
            fail("diagnostics")
    expected_digest = _digest(
        _canonical_json(
            {
                key: item
                for key, item in _result_dict(value).items()
                if key != "normalized_result_sha256"
            }
        )
    )
    if expected_digest != value.normalized_result_sha256:
        fail("integrity")


def _validate_run_namespace(root: Path, run_id: str) -> str:
    if type(root) is not _PATH_TYPE:
        _raise_load("store")
    try:
        if root.is_symlink() or not root.is_dir():
            _raise_load("store")
    except Exception:
        _raise_load("store")
    _validate_run_id(run_id, load=True)
    try:
        from ai_office.engine.workflow_run_manifest import (
            WorkflowRunManifestStore,
            load_workflow_run_manifest,
        )

        manifest = load_workflow_run_manifest(WorkflowRunManifestStore(root), run_id)
        if manifest.run_id != run_id:
            _raise_load("manifest")
        return manifest.digest
    except ExecutionEvidenceLoadError:
        raise
    except Exception:
        _raise_load("manifest")


def _evidence_path(root: Path, run_id: str, prefix: str, identity: str) -> Path:
    if type(root) is not _PATH_TYPE or root.is_symlink() or not root.is_dir():
        _raise_contract("store")
    _validate_run_id(run_id, load=False)
    _validate_sha256(identity, "identity")
    if prefix not in {_ATTEMPT_PREFIX, _RAW_PREFIX, _RESULT_PREFIX}:
        _raise_contract("prefix")
    return root / f"{run_id}.{prefix}.{identity}.json"


def _terminal_events(root: Path, run_id: str) -> tuple[object, ...]:
    try:
        from ai_office.engine.workflow_run_manifest import WorkflowRunManifestStore
        from ai_office.storage import load_workflow_execution_history

        store = WorkflowRunManifestStore(root)
        state_path, events_path = store.execution_paths(run_id)
        if not state_path.exists() and not events_path.exists():
            return ()
        if not state_path.is_file() or not events_path.is_file():
            _raise_load("terminal_history")
        history = load_workflow_execution_history(store.execution_targets(run_id))
    except ExecutionEvidenceLoadError:
        raise
    except Exception:
        _raise_load("terminal_history")
    if not history.events:
        return ()
    return tuple(history.events)


def _event_links_evidence(
    event: object,
    *,
    attempt: ExecutionAttemptEvidence,
    raw: RawProviderResponseEvidence | None,
    normalized: NormalizedExecutionResultEvidence,
) -> bool:
    return (
        getattr(event, "event_type", None) in {"step_succeeded", "step_failed"}
        and getattr(event, "next_status", None) in {"succeeded", "failed"}
        and getattr(event, "run_id", None) == attempt.run_id
        and getattr(event, "manifest_digest", None) == attempt.manifest_digest
        and getattr(event, "workflow_id", None) == attempt.workflow_id
        and getattr(event, "step_id", None) == attempt.step_id
        and getattr(event, "step_index", None) == attempt.step_index
        and getattr(event, "employee_id", None) == attempt.employee_id
        and getattr(event, "execution_attempt_id", None) == attempt.attempt_id
        and getattr(event, "execution_attempt_evidence_sha256", None) == attempt.digest
        and getattr(event, "normalized_result_evidence_sha256", None)
        == normalized.digest
        and getattr(event, "raw_response_evidence_sha256", None)
        == (None if raw is None else raw.digest)
        and getattr(event, "raw_response_body_sha256", None)
        == (None if raw is None else raw.body_sha256)
    )


def _response_request_id(
    headers: tuple[tuple[str, str], ...],
) -> str | None:
    for preferred in ("x-request-id", "request-id", "openai-request-id"):
        for name, value in headers:
            if name == preferred:
                return value
    return None


def _safe_response_headers(value: object) -> tuple[tuple[str, str], ...]:
    if type(value) is not tuple:
        _raise_contract("raw_headers")
    result: list[tuple[str, str]] = []
    for item in value:
        if (
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not str
        ):
            _raise_contract("raw_headers")
        name = item[0].lower()
        if name in _SAFE_RESPONSE_HEADERS:
            _validate_text(item[1], "raw_headers")
            result.append((name, item[1]))
    return tuple(result)


def _validate_definition(value: object, classification: str) -> None:
    if type(value) is not str or _DEFINITION_PATTERN.fullmatch(value) is None:
        _raise_contract(classification)


def _validate_text(value: object, classification: str, *, load: bool = False) -> None:
    fail = _raise_load if load else _raise_contract
    if type(value) is not str or value == "" or any(ord(char) < 32 for char in value):
        fail(classification)


def _validate_run_id(value: object, *, load: bool) -> None:
    fail = _raise_load if load else _raise_contract
    if type(value) is not str or _RUN_ID_PATTERN.fullmatch(value) is None:
        fail("run_id")


def _validate_sha256(value: object, classification: str, *, load: bool = False) -> None:
    fail = _raise_load if load else _raise_contract
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        fail(classification)


def _raise_contract(classification: str) -> NoReturn:
    raise ExecutionEvidenceError(classification) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise ExecutionEvidencePersistenceError(classification) from None


def _raise_conflict() -> NoReturn:
    raise ExecutionEvidenceConflictError("conflict") from None


def _raise_load(classification: str) -> NoReturn:
    raise ExecutionEvidenceLoadError(classification) from None


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> NoReturn:
    del value
    raise ValueError("nonstandard JSON constant")


__all__ = [
    "ExecutionAttemptAlreadyClaimedError",
    "ExecutionAttemptEvidence",
    "ExecutionEvidenceConflictError",
    "ExecutionEvidenceContext",
    "ExecutionEvidenceError",
    "ExecutionEvidenceFailureDetail",
    "ExecutionEvidenceInspection",
    "ExecutionEvidenceLoadError",
    "ExecutionEvidencePersistenceError",
    "NormalizedExecutionResultEvidence",
    "RawProviderResponseEvidence",
    "build_execution_evidence_context",
    "claim_execution_attempt",
    "ensure_no_execution_attempt",
    "execution_attempt_evidence_path",
    "execution_evidence_of_result",
    "execution_normalized_result_evidence_path",
    "execution_raw_response_evidence_path",
    "inspect_run_execution_evidence",
    "list_run_execution_evidence",
    "load_execution_attempt_evidence",
    "load_execution_normalized_result_evidence",
    "load_execution_raw_response_evidence",
    "load_normalized_result_evidence",
    "load_raw_response_evidence",
    "persist_execution_attempt",
    "persist_normalized_result_evidence",
    "persist_raw_response_evidence",
]
