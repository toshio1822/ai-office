"""Pure transformation from prepared data to an execution request and running state."""

from dataclasses import dataclass
from typing import Literal

from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage.workflow_execution_history import LoadedWorkflowExecutionHistory

PreparedStepExecutionStartClassification = Literal[
    "history_status", "workflow_identity", "step_index", "request_data"
]
_ERROR_MESSAGE = "prepared-step execution start inputs are incompatible"


@dataclass(frozen=True)
class PreparedStepExecutionRequest:
    """Provider-independent request data for one already prepared step."""

    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    employee_instructions: str
    step_instructions: str
    model: str
    allowed_tool_names: tuple[str, ...]


@dataclass(frozen=True)
class PreparedStepExecutionStart:
    """Exact execution request and proposed state before a later explicit execution."""

    request: PreparedStepExecutionRequest
    running_state: WorkflowExecutionState


@dataclass(frozen=True)
class PreparedStepExecutionStartFailureDetail:
    classification: PreparedStepExecutionStartClassification


class PreparedStepExecutionStartError(ValueError):
    """Raised when prepared data cannot safely start execution."""


class PreparedStepExecutionStartCompatibilityError(PreparedStepExecutionStartError):
    """Raised for stale or incompatible prepared step data."""

    def __init__(
        self, classification: PreparedStepExecutionStartClassification
    ) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PreparedStepExecutionStartFailureDetail(classification)


def prepare_prepared_step_execution_start(
    prepared_step: PreparedWorkflowStep,
    history: LoadedWorkflowExecutionHistory,
) -> PreparedStepExecutionStart:
    """Return immutable request data and a proposed running state without I/O."""
    state = history.state
    if state.status != "succeeded":
        _raise("history_status")
    if prepared_step.workflow_id != state.workflow_id:
        _raise("workflow_identity")
    if not _positive_int(state.current_step_index) or not _positive_int(
        prepared_step.step_index
    ):
        _raise("step_index")
    if prepared_step.step_index != state.current_step_index + 1:
        _raise("step_index")
    if not _valid_request_data(prepared_step):
        _raise("request_data")
    request = PreparedStepExecutionRequest(
        prepared_step.workflow_id,
        prepared_step.step_id,
        prepared_step.step_index,
        prepared_step.employee_id,
        prepared_step.employee_instructions,
        prepared_step.step_instructions,
        prepared_step.model,
        tuple(prepared_step.allowed_tool_names),
    )
    return PreparedStepExecutionStart(
        request,
        WorkflowExecutionState(
            prepared_step.workflow_id,
            "running",
            prepared_step.step_id,
            prepared_step.step_index,
            prepared_step.employee_id,
            state.completed_step_ids,
            None,
        ),
    )


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_request_data(value: PreparedWorkflowStep) -> bool:
    return (
        all(
            isinstance(item, str) and bool(item)
            for item in (
                value.step_id,
                value.employee_id,
                value.employee_instructions,
                value.step_instructions,
                value.model,
            )
        )
        and isinstance(value.allowed_tool_names, tuple)
        and all(
            isinstance(tool, str) and bool(tool) for tool in value.allowed_tool_names
        )
    )


def _raise(classification: PreparedStepExecutionStartClassification) -> None:
    raise PreparedStepExecutionStartCompatibilityError(classification) from None
