"""Single-step runtime result wrapping for explicit OpenAI execution."""

from collections.abc import Callable
from dataclasses import dataclass

from ai_office.execution_evidence import (
    ExecutionAttemptEvidence,
    ExecutionEvidenceContext,
    ExecutionEvidenceError,
    execution_evidence_of_result,
)
from ai_office.execution_target import (
    ModelExecutionTargetError,
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
    ModelInvocationSuccess,
)
from ai_office.planning import StepExecutionRequest
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesTransport,
    execute_openai_model_invocation,
    send_openai_responses_http_request,
)
from ai_office.runtime.run_binding import (
    WorkflowRunBinding,
    binding_of,
    select_run_binding,
)
from ai_office.tools import ToolDefinition

_INPUT_ERROR_MESSAGE = "runtime step execution inputs are inconsistent"
_UNSET = object()


@dataclass(frozen=True)
class StepRuntimeExecutionInput:
    """Already-prepared, credential-free inputs for one runtime step."""

    step_request: StepExecutionRequest
    invocation_request: ModelInvocationRequest
    resolved_tools: tuple[ToolDefinition, ...]
    approval: ModelInvocationExecutionApproval
    execution_evidence: ExecutionEvidenceContext
    attempt_claimed: Callable[[ExecutionAttemptEvidence], None] | None = None


@dataclass(frozen=True, init=False)
class StepRuntimeExecutionSuccess:
    """A successful provider invocation with its original step and Run identity."""

    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    invocation_result: ModelInvocationSuccess

    def __init__(
        self,
        workflow_id: str,
        step_id: str,
        step_index: int,
        employee_id: str,
        invocation_result: ModelInvocationSuccess,
        *,
        run_id: str | None = None,
        manifest_digest: str | None = None,
        binding: WorkflowRunBinding | None = None,
    ) -> None:
        _initialize_runtime_result(
            self,
            workflow_id,
            step_id,
            step_index,
            employee_id,
            invocation_result,
            run_id=run_id,
            manifest_digest=manifest_digest,
            binding=binding,
        )

    @property
    def run_id(self) -> str | None:
        binding = binding_of(self)
        return None if binding is None else binding.run_id

    @property
    def manifest_digest(self) -> str | None:
        binding = binding_of(self)
        return None if binding is None else binding.manifest_digest

    def __eq__(self, other: object) -> bool:
        if type(other) is not StepRuntimeExecutionSuccess:
            return NotImplemented
        assert isinstance(other, StepRuntimeExecutionSuccess)
        return (
            self.workflow_id,
            self.step_id,
            self.step_index,
            self.employee_id,
            self.invocation_result,
            binding_of(self),
        ) == (
            other.workflow_id,
            other.step_id,
            other.step_index,
            other.employee_id,
            other.invocation_result,
            binding_of(other),
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.workflow_id,
                self.step_id,
                self.step_index,
                self.employee_id,
                self.invocation_result,
                binding_of(self),
            )
        )


@dataclass(frozen=True, init=False)
class StepRuntimeExecutionFailure:
    """A failed provider invocation with its original step and Run identity."""

    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    invocation_result: ModelInvocationFailure

    def __init__(
        self,
        workflow_id: str,
        step_id: str,
        step_index: int,
        employee_id: str,
        invocation_result: ModelInvocationFailure,
        *,
        run_id: str | None = None,
        manifest_digest: str | None = None,
        binding: WorkflowRunBinding | None = None,
    ) -> None:
        _initialize_runtime_result(
            self,
            workflow_id,
            step_id,
            step_index,
            employee_id,
            invocation_result,
            run_id=run_id,
            manifest_digest=manifest_digest,
            binding=binding,
        )

    @property
    def run_id(self) -> str | None:
        binding = binding_of(self)
        return None if binding is None else binding.run_id

    @property
    def manifest_digest(self) -> str | None:
        binding = binding_of(self)
        return None if binding is None else binding.manifest_digest

    def __eq__(self, other: object) -> bool:
        if type(other) is not StepRuntimeExecutionFailure:
            return NotImplemented
        assert isinstance(other, StepRuntimeExecutionFailure)
        return (
            self.workflow_id,
            self.step_id,
            self.step_index,
            self.employee_id,
            self.invocation_result,
            binding_of(self),
        ) == (
            other.workflow_id,
            other.step_id,
            other.step_index,
            other.employee_id,
            other.invocation_result,
            binding_of(other),
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.workflow_id,
                self.step_id,
                self.step_index,
                self.employee_id,
                self.invocation_result,
                binding_of(self),
            )
        )


StepRuntimeExecutionResult = StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure


class StepRuntimeExecutionInputError(ValueError):
    """Raised when prepared runtime execution inputs are inconsistent."""


def execute_openai_runtime_step(
    execution_input: StepRuntimeExecutionInput,
    api_key: OpenAIApiKey,
    *,
    transport: OpenAIResponsesTransport = send_openai_responses_http_request,
) -> StepRuntimeExecutionResult:
    """Execute one approved OpenAI step without changing runtime state."""
    try:
        _validate_execution_input(execution_input)
    except StepRuntimeExecutionInputError as error:
        return _build_input_failure(
            execution_input.step_request,
            error,
            provider=_approval_provider(execution_input.approval),
        )

    try:
        execution_target = validate_execution_target_for_provider(
            execution_input.approval.execution_target,
            provider=execution_input.approval.provider,
        )
    except (AttributeError, ModelExecutionTargetError, TypeError, ValueError):
        return _build_input_failure(
            execution_input.step_request,
            StepRuntimeExecutionInputError(_INPUT_ERROR_MESSAGE),
            provider=_approval_provider(execution_input.approval),
        )

    invocation_result = execute_openai_model_invocation(
        execution_input.invocation_request,
        execution_input.resolved_tools,
        api_key,
        execution_input.approval,
        transport=transport,
        execution_target=execution_target,
        execution_evidence=execution_input.execution_evidence,
        attempt_claimed=execution_input.attempt_claimed,
    )
    if execution_evidence_of_result(invocation_result) is None and (
        type(invocation_result) is ModelInvocationSuccess
        or invocation_result.category not in {"invalid_request", "approval_required"}
    ):
        raise ExecutionEvidenceError("normalized_result")
    result = _build_runtime_result(execution_input.step_request, invocation_result)
    if not is_valid_step_runtime_execution_result(
        result,
        workflow_id=execution_input.step_request.workflow_id,
        step_id=execution_input.step_request.step_id,
        step_index=execution_input.step_request.step_index,
        employee_id=execution_input.step_request.employee_id,
        run_id=execution_input.step_request.run_id,
        manifest_digest=execution_input.step_request.manifest_digest,
    ):
        raise RuntimeError("runtime step execution result is invalid")
    return result


def _validate_execution_input(execution_input: StepRuntimeExecutionInput) -> None:
    step_request = execution_input.step_request
    invocation_request = execution_input.invocation_request
    if (
        step_request.model != invocation_request.model
        or step_request.employee_instructions != invocation_request.system_instructions
        or step_request.step_instructions != invocation_request.task_instructions
        or step_request.allowed_tools != invocation_request.allowed_tools
        or step_request.run_id != invocation_request.run_id
        or step_request.manifest_digest != invocation_request.manifest_digest
    ):
        raise StepRuntimeExecutionInputError(_INPUT_ERROR_MESSAGE)
    evidence = execution_input.execution_evidence
    if type(evidence) is not ExecutionEvidenceContext or (
        evidence.request != invocation_request
        or evidence.resolved_tools != execution_input.resolved_tools
        or evidence.target != execution_input.approval.execution_target
        or evidence.workflow_id != step_request.workflow_id
        or evidence.step_id != step_request.step_id
        or evidence.step_index != step_request.step_index
        or evidence.employee_id != step_request.employee_id
    ):
        raise StepRuntimeExecutionInputError(_INPUT_ERROR_MESSAGE)
    if execution_input.attempt_claimed is not None and not callable(
        execution_input.attempt_claimed
    ):
        raise StepRuntimeExecutionInputError(_INPUT_ERROR_MESSAGE)


def _build_input_failure(
    step_request: StepExecutionRequest,
    error: StepRuntimeExecutionInputError,
    *,
    provider: str = "openai",
) -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        workflow_id=step_request.workflow_id,
        step_id=step_request.step_id,
        step_index=step_request.step_index,
        employee_id=step_request.employee_id,
        invocation_result=ModelInvocationFailure(
            provider=provider,
            category="invalid_request",
            message=str(error),
            request_id=None,
            status_code=None,
            provider_error_type=None,
            provider_error_code=None,
        ),
        run_id=step_request.run_id,
        manifest_digest=step_request.manifest_digest,
    )


def _approval_provider(approval: object) -> str:
    """Preserve a supported target provider on pre-transport failures."""
    provider = getattr(approval, "provider", "openai")
    return provider if is_supported_execution_provider(provider) else "openai"


def _build_runtime_result(
    step_request: StepExecutionRequest,
    invocation_result: ModelInvocationResult,
) -> StepRuntimeExecutionResult:
    identity = {
        "workflow_id": step_request.workflow_id,
        "step_id": step_request.step_id,
        "step_index": step_request.step_index,
        "employee_id": step_request.employee_id,
        "run_id": step_request.run_id,
        "manifest_digest": step_request.manifest_digest,
    }
    if isinstance(invocation_result, ModelInvocationSuccess):
        return StepRuntimeExecutionSuccess(
            **identity,
            invocation_result=invocation_result,
        )
    return StepRuntimeExecutionFailure(
        **identity,
        invocation_result=invocation_result,
    )


def is_valid_step_runtime_execution_result(
    result: object,
    *,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    run_id: object = _UNSET,
    manifest_digest: object = _UNSET,
) -> bool:
    """Check the exact runtime result, including an optional expected Run."""
    if type(result) not in {
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
    } or not _valid_runtime_identity(
        result, workflow_id, step_id, step_index, employee_id
    ):
        return False
    if run_id is not _UNSET or manifest_digest is not _UNSET:
        if run_id is _UNSET or manifest_digest is _UNSET:
            return False
        if (result.run_id, result.manifest_digest) != (run_id, manifest_digest):
            return False
    if type(result) is StepRuntimeExecutionSuccess:
        return _valid_invocation_success(result.invocation_result)
    return _valid_invocation_failure(result.invocation_result)


def _initialize_runtime_result(
    value: object,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    invocation_result: object,
    *,
    run_id: str | None,
    manifest_digest: str | None,
    binding: WorkflowRunBinding | None,
) -> None:
    selected = select_run_binding(binding, run_id, manifest_digest)
    for name, item in (
        ("workflow_id", workflow_id),
        ("step_id", step_id),
        ("step_index", step_index),
        ("employee_id", employee_id),
        ("invocation_result", invocation_result),
    ):
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_run_binding", selected)


def _valid_runtime_identity(
    result: StepRuntimeExecutionResult,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
) -> bool:
    return (
        all(
            type(value) is str and value != ""
            for value in (
                result.workflow_id,
                result.step_id,
                result.employee_id,
            )
        )
        and type(result.step_index) is int
        and result.workflow_id == workflow_id
        and result.step_id == step_id
        and result.step_index == step_index
        and result.employee_id == employee_id
    )


def _valid_invocation_success(value: object) -> bool:
    return (
        type(value) is ModelInvocationSuccess
        and is_supported_execution_provider(value.provider)
        and all(
            type(item) is str and item != ""
            for item in (
                value.provider,
                value.response_id,
                value.status,
            )
        )
        and _valid_optional_string(value.request_id)
        and type(value.text_parts) is tuple
        and all(type(item) is str for item in value.text_parts)
        and type(value.text) is str
        and value.text == "".join(value.text_parts)
    )


def _valid_invocation_failure(value: object) -> bool:
    diagnostics = (
        value.response_diagnostics if type(value) is ModelInvocationFailure else None
    )
    return (
        type(value) is ModelInvocationFailure
        and is_supported_execution_provider(value.provider)
        and type(value.provider) is str
        and value.category
        in {
            "api_error",
            "transport_error",
            "invalid_response",
            "invalid_output",
            "invalid_request",
            "approval_required",
        }
        and type(value.message) is str
        and value.message != ""
        and _valid_optional_string(value.request_id)
        and _valid_optional_int(value.status_code)
        and _valid_optional_string(value.provider_error_type)
        and _valid_optional_string(value.provider_error_code)
        and _valid_failure_diagnostics(diagnostics, value.category)
        and (
            value.category == "api_error"
            or (
                value.status_code is None
                and value.provider_error_type is None
                and value.provider_error_code is None
            )
        )
    )


def _valid_failure_diagnostics(
    value: object,
    category: ModelInvocationFailureCategory,
) -> bool:
    if value is None:
        return True
    return (
        category == "invalid_response"
        and type(value) is ModelInvocationFailureDiagnostics
        and type(value.status_code) is int
        and (
            value.content_type is None
            or (type(value.content_type) is str and value.content_type != "")
        )
        and type(value.body_length) is int
        and value.body_length >= 0
        and type(value.body_kind) is str
        and value.body_kind
        in {
            "empty",
            "json",
            "sse",
            "html",
            "plaintext",
            "malformed_json",
            "non_utf8",
        }
    )


def _valid_optional_string(value: object) -> bool:
    return value is None or (type(value) is str and value != "")


def _valid_optional_int(value: object) -> bool:
    return value is None or type(value) is int
