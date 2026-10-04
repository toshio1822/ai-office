"""Pure workflow state transitions from completed runtime step results."""

from dataclasses import dataclass
from typing import Literal

from ai_office.execution_evidence import execution_evidence_of_result
from ai_office.invocation import (
    ModelInvocationFailureCategory,
    ModelInvocationFailureDiagnostics,
)
from ai_office.planning import StepExecutionRequest
from ai_office.runtime.run_binding import (
    WorkflowRunBinding,
    binding_of,
    select_run_binding,
)
from ai_office.runtime.step_runtime_execution import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionResult,
    StepRuntimeExecutionSuccess,
)

WorkflowExecutionStatus = Literal["ready", "running", "succeeded", "failed"]
RuntimeStepEventType = Literal["step_succeeded", "step_failed"]

_INPUT_ERROR_MESSAGE = "workflow execution transition inputs are inconsistent"


@dataclass(frozen=True, init=False)
class WorkflowExecutionState:
    """Immutable state for one explicitly selected workflow step.

    The seven historical dataclass fields remain the semantic state shape.  A
    Run binding is attached directly to the value, so old unbound data remains
    distinguishable and cannot be mistaken for new Run-owned data.
    """

    workflow_id: str
    status: WorkflowExecutionStatus
    current_step_id: str
    current_step_index: int
    current_employee_id: str
    completed_step_ids: tuple[str, ...]
    last_failure_category: ModelInvocationFailureCategory | None

    def __init__(
        self,
        workflow_id: str,
        status: WorkflowExecutionStatus,
        current_step_id: str,
        current_step_index: int,
        current_employee_id: str,
        completed_step_ids: tuple[str, ...],
        last_failure_category: ModelInvocationFailureCategory | None,
        *,
        run_id: str | None = None,
        manifest_digest: str | None = None,
        binding: WorkflowRunBinding | None = None,
    ) -> None:
        selected = select_run_binding(binding, run_id, manifest_digest)
        object.__setattr__(self, "workflow_id", workflow_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "current_step_id", current_step_id)
        object.__setattr__(self, "current_step_index", current_step_index)
        object.__setattr__(self, "current_employee_id", current_employee_id)
        object.__setattr__(self, "completed_step_ids", completed_step_ids)
        object.__setattr__(self, "last_failure_category", last_failure_category)
        object.__setattr__(self, "_run_binding", selected)

    @property
    def run_id(self) -> str | None:
        binding = binding_of(self)
        return None if binding is None else binding.run_id

    @property
    def manifest_digest(self) -> str | None:
        binding = binding_of(self)
        return None if binding is None else binding.manifest_digest

    def __eq__(self, other: object) -> bool:
        """Compare state meaning and the complete direct Run binding."""
        if type(other) is not WorkflowExecutionState:
            return NotImplemented
        assert isinstance(other, WorkflowExecutionState)
        return (
            self.workflow_id,
            self.status,
            self.current_step_id,
            self.current_step_index,
            self.current_employee_id,
            self.completed_step_ids,
            self.last_failure_category,
            binding_of(self),
        ) == (
            other.workflow_id,
            other.status,
            other.current_step_id,
            other.current_step_index,
            other.current_employee_id,
            other.completed_step_ids,
            other.last_failure_category,
            binding_of(other),
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.workflow_id,
                self.status,
                self.current_step_id,
                self.current_step_index,
                self.current_employee_id,
                self.completed_step_ids,
                self.last_failure_category,
                binding_of(self),
            )
        )


@dataclass(frozen=True, init=False)
class RuntimeStepEvent:
    """Immutable safe event data for one completed runtime step."""

    event_type: RuntimeStepEventType
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    previous_status: WorkflowExecutionStatus
    next_status: WorkflowExecutionStatus
    provider: str
    failure_category: ModelInvocationFailureCategory | None
    response_id: str | None
    request_id: str | None
    output_text: str | None
    message: str | None
    response_diagnostics: ModelInvocationFailureDiagnostics | None

    def __init__(
        self,
        event_type: RuntimeStepEventType,
        workflow_id: str,
        step_id: str,
        step_index: int,
        employee_id: str,
        previous_status: WorkflowExecutionStatus,
        next_status: WorkflowExecutionStatus,
        provider: str,
        failure_category: ModelInvocationFailureCategory | None,
        response_id: str | None,
        request_id: str | None,
        output_text: str | None,
        message: str | None,
        response_diagnostics: ModelInvocationFailureDiagnostics | None = None,
        *,
        run_id: str | None = None,
        manifest_digest: str | None = None,
        binding: WorkflowRunBinding | None = None,
        execution_attempt_id: str | None = None,
        execution_attempt_evidence_sha256: str | None = None,
        normalized_result_evidence_sha256: str | None = None,
        raw_response_evidence_sha256: str | None = None,
        raw_response_body_sha256: str | None = None,
    ) -> None:
        selected = select_run_binding(binding, run_id, manifest_digest)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "workflow_id", workflow_id)
        object.__setattr__(self, "step_id", step_id)
        object.__setattr__(self, "step_index", step_index)
        object.__setattr__(self, "employee_id", employee_id)
        object.__setattr__(self, "previous_status", previous_status)
        object.__setattr__(self, "next_status", next_status)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "failure_category", failure_category)
        object.__setattr__(self, "response_id", response_id)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "output_text", output_text)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "response_diagnostics", response_diagnostics)
        object.__setattr__(self, "_run_binding", selected)
        _set_execution_evidence_linkage(
            self,
            execution_attempt_id=execution_attempt_id,
            execution_attempt_evidence_sha256=execution_attempt_evidence_sha256,
            normalized_result_evidence_sha256=normalized_result_evidence_sha256,
            raw_response_evidence_sha256=raw_response_evidence_sha256,
            raw_response_body_sha256=raw_response_body_sha256,
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
        """Compare event meaning and the complete direct Run binding."""
        if type(other) is not RuntimeStepEvent:
            return NotImplemented
        assert isinstance(other, RuntimeStepEvent)
        return (
            self.event_type,
            self.workflow_id,
            self.step_id,
            self.step_index,
            self.employee_id,
            self.previous_status,
            self.next_status,
            self.provider,
            self.failure_category,
            self.response_id,
            self.request_id,
            self.output_text,
            self.message,
            self.response_diagnostics,
            _execution_evidence_linkage(self),
            binding_of(self),
        ) == (
            other.event_type,
            other.workflow_id,
            other.step_id,
            other.step_index,
            other.employee_id,
            other.previous_status,
            other.next_status,
            other.provider,
            other.failure_category,
            other.response_id,
            other.request_id,
            other.output_text,
            other.message,
            other.response_diagnostics,
            _execution_evidence_linkage(other),
            binding_of(other),
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.event_type,
                self.workflow_id,
                self.step_id,
                self.step_index,
                self.employee_id,
                self.previous_status,
                self.next_status,
                self.provider,
                self.failure_category,
                self.response_id,
                self.request_id,
                self.output_text,
                self.message,
                self.response_diagnostics,
                _execution_evidence_linkage(self),
                binding_of(self),
            )
        )


@dataclass(frozen=True)
class WorkflowExecutionTransition:
    """One pure state change and its corresponding runtime event."""

    previous_state: WorkflowExecutionState
    next_state: WorkflowExecutionState
    event: RuntimeStepEvent


class WorkflowExecutionTransitionInputError(ValueError):
    """Raised when a state and a completed step result are inconsistent."""


def build_running_workflow_execution_state(
    step_request: StepExecutionRequest,
    *,
    completed_step_ids: tuple[str, ...] = (),
) -> WorkflowExecutionState:
    """Build the explicit running state immediately before one step executes."""
    return WorkflowExecutionState(
        workflow_id=step_request.workflow_id,
        status="running",
        current_step_id=step_request.step_id,
        current_step_index=step_request.step_index,
        current_employee_id=step_request.employee_id,
        completed_step_ids=completed_step_ids,
        last_failure_category=None,
        run_id=step_request.run_id,
        manifest_digest=step_request.manifest_digest,
    )


def transition_workflow_execution_from_step_result(
    current_state: WorkflowExecutionState,
    result: StepRuntimeExecutionResult,
) -> WorkflowExecutionTransition:
    """Return the deterministic state and event for one completed runtime step."""
    _validate_transition_input(current_state, result)
    if isinstance(result, StepRuntimeExecutionSuccess):
        return _build_success_transition(current_state, result)
    return _build_failure_transition(current_state, result)


def _validate_transition_input(
    current_state: WorkflowExecutionState,
    result: StepRuntimeExecutionResult,
) -> None:
    state_binding = binding_of(current_state)
    result_binding = binding_of(result)
    bindings_valid = (
        state_binding is None and result_binding is None
    ) or (state_binding is not None and state_binding == result_binding)
    if (
        current_state.workflow_id != result.workflow_id
        or current_state.current_step_id != result.step_id
        or current_state.current_step_index != result.step_index
        or current_state.current_employee_id != result.employee_id
        or current_state.status != "running"
        or not bindings_valid
    ):
        raise WorkflowExecutionTransitionInputError(_INPUT_ERROR_MESSAGE) from None


def _build_success_transition(
    current_state: WorkflowExecutionState,
    result: StepRuntimeExecutionSuccess,
) -> WorkflowExecutionTransition:
    invocation_result = result.invocation_result
    binding = binding_of(current_state)
    next_state = WorkflowExecutionState(
        workflow_id=current_state.workflow_id,
        status="succeeded",
        current_step_id=current_state.current_step_id,
        current_step_index=current_state.current_step_index,
        current_employee_id=current_state.current_employee_id,
        completed_step_ids=current_state.completed_step_ids
        + (current_state.current_step_id,),
        last_failure_category=None,
        binding=binding,
    )
    event = RuntimeStepEvent(
        event_type="step_succeeded",
        workflow_id=result.workflow_id,
        step_id=result.step_id,
        step_index=result.step_index,
        employee_id=result.employee_id,
        previous_status=current_state.status,
        next_status="succeeded",
        provider=invocation_result.provider,
        failure_category=None,
        response_id=invocation_result.response_id,
        request_id=invocation_result.request_id,
        output_text=invocation_result.text,
        message=None,
        **_execution_evidence_kwargs(invocation_result),
        binding=binding,
    )
    return WorkflowExecutionTransition(current_state, next_state, event)


def _build_failure_transition(
    current_state: WorkflowExecutionState,
    result: StepRuntimeExecutionFailure,
) -> WorkflowExecutionTransition:
    invocation_result = result.invocation_result
    binding = binding_of(current_state)
    next_state = WorkflowExecutionState(
        workflow_id=current_state.workflow_id,
        status="failed",
        current_step_id=current_state.current_step_id,
        current_step_index=current_state.current_step_index,
        current_employee_id=current_state.current_employee_id,
        completed_step_ids=current_state.completed_step_ids,
        last_failure_category=invocation_result.category,
        binding=binding,
    )
    event = RuntimeStepEvent(
        event_type="step_failed",
        workflow_id=result.workflow_id,
        step_id=result.step_id,
        step_index=result.step_index,
        employee_id=result.employee_id,
        previous_status=current_state.status,
        next_status="failed",
        provider=invocation_result.provider,
        failure_category=invocation_result.category,
        response_id=None,
        request_id=invocation_result.request_id,
        output_text=None,
        message=invocation_result.message,
        response_diagnostics=invocation_result.response_diagnostics,
        **_execution_evidence_kwargs(invocation_result),
        binding=binding,
    )
    return WorkflowExecutionTransition(current_state, next_state, event)


def _execution_evidence_kwargs(value: object) -> dict[str, str | None]:
    evidence = execution_evidence_of_result(value)
    if evidence is None:
        return {}
    return {
        "execution_attempt_id": evidence[0],
        "execution_attempt_evidence_sha256": evidence[1],
        "normalized_result_evidence_sha256": evidence[2],
        "raw_response_evidence_sha256": evidence[3],
        "raw_response_body_sha256": evidence[4],
    }


def _set_execution_evidence_linkage(
    value: object,
    *,
    execution_attempt_id: str | None,
    execution_attempt_evidence_sha256: str | None,
    normalized_result_evidence_sha256: str | None,
    raw_response_evidence_sha256: str | None,
    raw_response_body_sha256: str | None,
) -> None:
    linkage = (
        execution_attempt_id,
        execution_attempt_evidence_sha256,
        normalized_result_evidence_sha256,
        raw_response_evidence_sha256,
        raw_response_body_sha256,
    )
    if all(item is None for item in linkage):
        return
    if (
        execution_attempt_id is None
        or execution_attempt_evidence_sha256 is None
        or normalized_result_evidence_sha256 is None
        or (raw_response_evidence_sha256 is None) != (raw_response_body_sha256 is None)
        or any(
            type(item) is not str or len(item) != 64 or not _is_lower_hex(item)
            for item in linkage[:3]
        )
        or any(
            item is not None
            and (type(item) is not str or len(item) != 64 or not _is_lower_hex(item))
            for item in linkage[3:]
        )
    ):
        raise ValueError(_INPUT_ERROR_MESSAGE) from None
    for name, item in zip(
        (
            "execution_attempt_id",
            "execution_attempt_evidence_sha256",
            "normalized_result_evidence_sha256",
            "raw_response_evidence_sha256",
            "raw_response_body_sha256",
        ),
        linkage,
        strict=True,
    ):
        object.__setattr__(value, name, item)


def _execution_evidence_linkage(value: object) -> tuple[object, ...] | None:
    attempt_id = getattr(value, "execution_attempt_id", None)
    if attempt_id is None:
        return None
    return tuple(
        getattr(value, name, None)
        for name in (
            "execution_attempt_id",
            "execution_attempt_evidence_sha256",
            "normalized_result_evidence_sha256",
            "raw_response_evidence_sha256",
            "raw_response_body_sha256",
        )
    )


def _is_lower_hex(value: str) -> bool:
    return all(character in "0123456789abcdef" for character in value)
