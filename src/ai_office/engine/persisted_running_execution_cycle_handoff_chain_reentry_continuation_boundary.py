"""Phase 126 persisted-running execution cycle handoff chain boundary."""

# ruff: noqa: E501,E701,I001

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.persisted_execution_outcome_reentry import PersistedExecutionOutcome
from ai_office.engine.persisted_running_execution_cycle_handoff_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffReentryContinuationError as Phase119Error,
    route_persisted_running_execution_cycle_handoff_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationFailureCategory,
    ModelInvocationRequest,
    validate_model_invocation_execution_approval,
)
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    is_valid_step_runtime_execution_result,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    load_workflow_execution_history,
    load_workflow_execution_state,
)
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionLoadError,
)
from ai_office.storage.workflow_execution_persistence import (
    WorkflowExecutionPersistenceTargets,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition

Classification = Literal[
    "result_type",
    "workflow_definition",
    "execution_inputs",
    "persistence_result_contract",
    "start_contract",
    "employee_contract",
    "tools_contract",
    "credential_contract",
    "approval_contract",
    "completion_contract",
    "failure_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "runtime_contract",
    "dependency_error",
    "dependency_rollback",
]
Phase119Function = Callable[
    [object, object, object, object, object, object, object, object, object, object],
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())
_FAILURES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class PersistedRunningExecutionCycleHandoffChainReentryContinuationFailureDetail:
    """Safe classification for one Phase 126 compatibility failure."""

    classification: Classification


class PersistedRunningExecutionCycleHandoffChainReentryContinuationError(ValueError):
    """Base error for the Phase 126 boundary."""


class PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError(
    PersistedRunningExecutionCycleHandoffChainReentryContinuationError
):
    """Raised when one Phase 125 result cannot safely cross Phase 126."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted-running execution cycle handoff chain reentry continuation inputs are incompatible"
        )
        self.detail = (
            PersistedRunningExecutionCycleHandoffChainReentryContinuationFailureDetail(
                classification
            )
        )


def route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(
    result: object,
    start: object,
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    resolved_tools: object,
    api_key: object,
    approval: object,
    transport: object,
    *,
    phase119_function: Phase119Function = (
        route_persisted_running_execution_cycle_handoff_reentry_continuation_boundary
    ),
) -> (
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    """Route one exact Phase 125 result through public Phase 119 once."""
    _validate_inputs(result, workflow, state_path, events_path, phase119_function)
    assert (
        type(workflow) is WorkflowDefinition
        and type(state_path) is _PATH_TYPE
        and type(events_path) is _PATH_TYPE
    )

    if type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow)
        _require_none(start, employee, resolved_tools, api_key, approval, transport)
    elif type(result) is PersistedExecutionOutcome:
        _validate_failure(result, workflow)
        _require_none(start, employee, resolved_tools, api_key, approval, transport)
    else:
        _validate_execution_inputs(
            result,
            start,
            workflow,
            employee,
            resolved_tools,
            api_key,
            approval,
            transport,
        )

    _validate_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)

    if type(result) is WorkflowProgressionDecision:
        _validate_terminal(result, workflow, state_path, events_path, "succeeded")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _validate_terminal(result, workflow, state_path, events_path, "failed")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is RunningStatePersistenceResult
    assert type(start) is PreparedStepExecutionStart
    if type(result.state_bytes_written) is not int or result.state_bytes_written <= 0:
        _compatibility_error("persistence_result_contract")
    if result.state_bytes_written != len(original[0]):
        _compatibility_error("persistence_result_contract")
    _validate_predecessor(start, workflow, state_path, events_path)

    try:
        value = phase119_function(
            result,
            start,
            workflow,
            employee,
            state_path,
            events_path,
            resolved_tools,
            api_key,
            approval,
            transport,
        )
    except Phase119Error as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _compatibility_error("dependency_error")

    try:
        _require_unchanged(state_path, events_path, original, "runtime_contract")
        if type(value) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
            _compatibility_error("runtime_contract")
        assert type(start) is PreparedStepExecutionStart
        try:
            valid_runtime_result = is_valid_step_runtime_execution_result(
                value,
                workflow_id=start.running_state.workflow_id,
                step_id=start.running_state.current_step_id,
                step_index=start.running_state.current_step_index,
                employee_id=start.running_state.current_employee_id,
            )
        except Exception:
            valid_runtime_result = False
        if not valid_runtime_result:
            _compatibility_error("runtime_contract")
    except PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore_if_changed(state_path, events_path, original)
        raise
    return value


def _validate_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    function: object,
) -> None:
    if type(result) not in (
        RunningStatePersistenceResult,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _compatibility_error("result_type")
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _compatibility_error("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _compatibility_error("state_target")
    if type(events) is not _PATH_TYPE:
        _compatibility_error("event_target")
    if state == events:
        _compatibility_error("target_conflict")
    if not callable(function):
        _compatibility_error("execution_inputs")


def _valid_workflow(workflow: WorkflowDefinition) -> bool:
    return (
        _nonempty_string(workflow.id)
        and _nonempty_string(workflow.name)
        and _nonempty_string(workflow.description)
        and type(workflow.steps) is list
        and bool(workflow.steps)
        and all(
            type(step) is WorkflowStepDefinition
            and _nonempty_string(step.id)
            and _nonempty_string(step.name)
            and _nonempty_string(step.employee)
            and _nonempty_string(step.instructions)
            for step in workflow.steps
        )
    )


def _require_none(*values: object | None) -> None:
    if any(value is not None for value in values):
        _compatibility_error("execution_inputs")


def _valid_employee(value: EmployeeDefinition) -> bool:
    return (
        _nonempty_string(value.id)
        and _nonempty_string(value.name)
        and _nonempty_string(value.role)
        and _nonempty_string(value.instructions)
        and _nonempty_string(value.model)
        and type(value.allowed_tools) is list
        and all(_nonempty_string(item) for item in value.allowed_tools)
        and len(value.allowed_tools) == len(set(value.allowed_tools))
    )


def _valid_tool(value: ToolDefinition) -> bool:
    return (
        type(value.name) is str
        and bool(value.name)
        and type(value.description) is str
        and type(value.parameters) is tuple
        and all(
            type(parameter) is ToolParameterDefinition
            and type(parameter.name) is str
            and bool(parameter.name)
            and type(parameter.description) is str
            and type(parameter.type) is str
            and type(parameter.required) is bool
            for parameter in value.parameters
        )
    )


def _validate_execution_inputs(
    result: RunningStatePersistenceResult,
    start: object,
    workflow: WorkflowDefinition,
    employee: object,
    tools: object,
    key: object,
    approval: object,
    transport: object,
) -> None:
    if type(start) is not PreparedStepExecutionStart:
        _compatibility_error("start_contract")
    if type(employee) is not EmployeeDefinition or not _valid_employee(employee):
        _compatibility_error("employee_contract")
    if type(tools) is not tuple or not all(
        type(tool) is ToolDefinition and _valid_tool(tool) for tool in tools
    ):
        _compatibility_error("tools_contract")
    if (
        type(key) is not OpenAIApiKey
        or type(key.value) is not SecretStr
        or not key.value.get_secret_value()
    ):
        _compatibility_error("credential_contract")
    if (
        type(approval) is not ModelInvocationExecutionApproval
        or type(approval.approved) is not bool
        or type(approval.provider) is not str
        or type(approval.request_fingerprint) is not str
        or type(approval.approved_by) is not str
        or type(approval.approval_id) is not str
    ):
        _compatibility_error("approval_contract")
    if not callable(transport):
        _compatibility_error("execution_inputs")

    request = start.request
    running = start.running_state
    if type(request) is not ModelInvocationRequest:
        _compatibility_error("start_contract")
    if type(running) is not WorkflowExecutionState:
        _compatibility_error("start_contract")
    if (
        type(running.current_step_index) is not int
        or not 3 <= running.current_step_index <= len(workflow.steps)
    ):
        _compatibility_error("start_contract")
    step = workflow.steps[running.current_step_index - 1]
    expected_prefix = tuple(item.id for item in workflow.steps[: running.current_step_index - 1])
    if not (
        _nonempty_string(running.workflow_id)
        and running.workflow_id == workflow.id
        and type(running.status) is str
        and running.status == "running"
        and type(running.current_step_id) is str
        and running.current_step_id == step.id
        and type(running.current_employee_id) is str
        and running.current_employee_id == step.employee
        and running.current_employee_id == employee.id
        and type(running.completed_step_ids) is tuple
        and all(type(item) is str for item in running.completed_step_ids)
        and running.completed_step_ids == expected_prefix
        and running.last_failure_category is None
        and _nonempty_string(request.model)
        and request.model == employee.model
        and _nonempty_string(request.system_instructions)
        and request.system_instructions == employee.instructions
        and _nonempty_string(request.task_instructions)
        and request.task_instructions == step.instructions
        and type(request.allowed_tools) is tuple
        and all(_nonempty_string(item) for item in request.allowed_tools)
        and request.allowed_tools == tuple(employee.allowed_tools)
        and tuple(tool.name for tool in tools) == request.allowed_tools
    ):
        _compatibility_error("start_contract")
    try:
        validate_model_invocation_execution_approval(
            request, tools, approval, provider="openai"
        )
    except (TypeError, ValueError):
        _compatibility_error("approval_contract")
    if type(result) is not RunningStatePersistenceResult:
        _compatibility_error("persistence_result_contract")


def _validate_completion(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        type(value.decision) is str
        and value.decision == "workflow_complete"
        and _exact_string(value.workflow_id, workflow.id)
        and _exact_string(value.current_step_id, final.id)
        and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps)
        and _exact_string(value.current_employee_id, final.employee)
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and _exact_string(value.reason, "last_step_succeeded")
    ):
        _compatibility_error("completion_contract")


def _validate_failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    index = value.current_step_index
    if not (
        type(value.outcome) is str
        and value.outcome == "persisted_failure"
        and _exact_string(value.workflow_id, workflow.id)
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
        and _exact_string(value.current_step_id, workflow.steps[index - 1].id)
        and _exact_string(value.current_employee_id, workflow.steps[index - 1].employee)
        and type(value.failure_category) is str
        and value.failure_category in _FAILURES
    ):
        _compatibility_error("failure_contract")


def _validate_targets(state: Path, events: Path) -> None:
    for path, classification in ((state, "state_target"), (events, "event_target")):
        try:
            if not path.is_file():
                _compatibility_error(classification)
        except OSError:
            _compatibility_error(classification)


def _capture_targets(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state.read_bytes()
    except OSError:
        _compatibility_error("state_target")
    try:
        event_bytes = events.read_bytes()
    except OSError:
        _compatibility_error("event_target")
    return state_bytes, event_bytes


def _validate_terminal(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    status: Literal["succeeded", "failed"],
) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _compatibility_error("terminal_contract")
    if not (
        type(persisted) is WorkflowExecutionState
        and persisted.status == status
        and (
            persisted.workflow_id,
            persisted.current_step_id,
            persisted.current_step_index,
            persisted.current_employee_id,
        )
        == (
            value.workflow_id,
            value.current_step_id,
            value.current_step_index,
            value.current_employee_id,
        )
    ):
        _compatibility_error("terminal_contract")
    if (
        type(value) is PersistedExecutionOutcome
        and persisted.last_failure_category != value.failure_category
    ):
        _compatibility_error("terminal_contract")


def _validate_predecessor(
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> None:
    try:
        loaded = load_workflow_execution_state(state)
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
    except (OSError, WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _compatibility_error("persistence_result_contract")
    running = start.running_state
    if (
        type(loaded) is not WorkflowExecutionState
        or loaded != running
        or type(history.state) is not WorkflowExecutionState
        or history.state != running
    ):
        _compatibility_error("persistence_result_contract")
    expected_steps = workflow.steps[: running.current_step_index - 1]
    if len(history.events) != len(expected_steps):
        _compatibility_error("persistence_result_contract")
    for event, step in zip(history.events, expected_steps, strict=True):
        if not (
            type(event) is RuntimeStepEvent
            and _exact_string(event.event_type, "step_succeeded")
            and _exact_string(event.workflow_id, workflow.id)
            and _exact_string(event.step_id, step.id)
            and type(event.step_index) is int
            and event.step_index == workflow.steps.index(step) + 1
            and _exact_string(event.employee_id, step.employee)
            and _exact_string(event.previous_status, "running")
            and _exact_string(event.next_status, "succeeded")
            and _nonempty_string(event.provider)
            and event.failure_category is None
            and type(event.response_id) is str
            and bool(event.response_id)
            and type(event.output_text) is str
            and event.message is None
        ):
            _compatibility_error("persistence_result_contract")


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _compatibility_error(classification)


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_if_changed(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    if not (_changed(state, original[0]) or _changed(events, original[1])):
        return
    failed = False
    for path, data in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(data)
        except OSError:
            failed = True
    if failed or _changed(state, original[0]) or _changed(events, original[1]):
        _compatibility_error("dependency_rollback")


def _compatibility_error(classification: Classification) -> None:
    raise PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError(
        classification
    ) from None


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected
