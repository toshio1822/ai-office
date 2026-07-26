"""Route an exact persisted running result to one Phase 35 execution."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_running_execution_reentry import (
    PersistedRunningExecutionReentryError,
    execute_persisted_running_openai_step,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationRequest,
    validate_model_invocation_execution_approval,
)
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import (
    StepRuntimeExecutionResult,
    WorkflowExecutionState,
    is_valid_step_runtime_execution_result,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    load_workflow_execution_state,
)
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionLoadError,
)
from ai_office.tools import ToolDefinition

RoutingClassification = Literal[
    "result_type",
    "persistence_contract",
    "completion_contract",
    "start_contract",
    "workflow_definition",
    "employee_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "execution_contract",
    "dependency_error",
    "dependency_rollback",
]
_ERROR_MESSAGE = "persisted-running execution routing inputs are incompatible"
ExecutionReentryFunction = Callable[..., StepRuntimeExecutionResult]


@dataclass(frozen=True)
class PersistedRunningExecutionRoutingFailureDetail:
    classification: RoutingClassification


class PersistedRunningExecutionRoutingError(ValueError):
    pass


class PersistedRunningExecutionRoutingCompatibilityError(
    PersistedRunningExecutionRoutingError
):
    def __init__(self, classification: RoutingClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PersistedRunningExecutionRoutingFailureDetail(classification)


def route_persisted_running_execution_reentry(
    result: object,
    start: object | None,
    workflow: object,
    employee: object | None,
    state_path: object,
    events_path: object,
    resolved_tools: object | None,
    api_key: object | None,
    approval: object | None,
    transport: object | None,
    *,
    execution_reentry_function: ExecutionReentryFunction = (
        execute_persisted_running_openai_step
    ),
) -> StepRuntimeExecutionResult | WorkflowProgressionDecision:
    """Execute one verified persisted start, or stop an exact completion decision."""
    _validate_targets(workflow, state_path, events_path, execution_reentry_function)
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _validate_completion(
            result,
            workflow,
            start,
            employee,
            resolved_tools,
            api_key,
            approval,
            transport,
        )
        _require_unchanged(state_path, events_path, original)
        return result
    if type(result) is not RunningStatePersistenceResult:
        _raise("result_type")
    _validate_execution_inputs(
        start, workflow, employee, resolved_tools, api_key, approval, transport
    )
    assert type(start) is PreparedStepExecutionStart
    assert type(employee) is EmployeeDefinition
    assert type(resolved_tools) is tuple
    assert type(api_key) is OpenAIApiKey
    assert type(approval) is ModelInvocationExecutionApproval
    assert callable(transport)
    persisted = _validate_persistence(result, start, state_path)
    try:
        value = execution_reentry_function(
            start,
            state_path,
            workflow,
            employee,
            resolved_tools,
            api_key,
            approval,
            transport=transport,
        )
    except PersistedRunningExecutionReentryError:
        _restore(state_path, events_path, original)
        raise
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    if not is_valid_step_runtime_execution_result(
        value,
        workflow_id=persisted.workflow_id,
        step_id=persisted.current_step_id,
        step_index=persisted.current_step_index,
        employee_id=persisted.current_employee_id,
    ):
        _restore(state_path, events_path, original)
        _raise("execution_contract")
    if _changed(state_path, original[0]) or _changed(events_path, original[1]):
        _restore(state_path, events_path, original)
        _raise("execution_contract")
    return value


def _validate_targets(
    workflow: object, state: object, events: object, function: object
) -> None:
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if not isinstance(state, Path):
        _raise("state_target")
    if not isinstance(events, Path):
        _raise("event_target")
    if state == events:
        _raise("target_conflict")
    if not callable(function):
        _raise("execution_contract")
    try:
        if not state.is_file():
            _raise("state_target")
        if not events.is_file():
            _raise("event_target")
    except OSError:
        _raise("dependency_error")


def _validate_completion(
    decision: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    start: object | None,
    employee: object | None,
    tools: object | None,
    key: object | None,
    approval: object | None,
    transport: object | None,
) -> None:
    final = workflow.steps[-1]
    if not (
        start is None
        and employee is None
        and tools is None
        and key is None
        and approval is None
        and transport is None
        and decision.decision == "workflow_complete"
        and decision.workflow_id == workflow.id
        and decision.current_step_id == final.id
        and decision.current_step_index == len(workflow.steps)
        and decision.current_employee_id == final.employee
        and decision.next_step_id is None
        and decision.next_step_index is None
        and decision.next_employee_id is None
        and decision.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _validate_execution_inputs(
    start: object | None,
    workflow: WorkflowDefinition,
    employee: object | None,
    tools: object | None,
    key: object | None,
    approval: object | None,
    transport: object | None,
) -> None:
    if type(start) is not PreparedStepExecutionStart:
        _raise("start_contract")
    if type(employee) is not EmployeeDefinition:
        _raise("employee_contract")
    if (
        type(start.request) is not ModelInvocationRequest
        or type(start.running_state) is not WorkflowExecutionState
    ):
        _raise("start_contract")
    running = start.running_state
    if type(
        running.current_step_index
    ) is not int or not 1 <= running.current_step_index <= len(workflow.steps):
        _raise("start_contract")
    step = workflow.steps[running.current_step_index - 1]
    if not (
        running.status == "running"
        and running.last_failure_category is None
        and running.workflow_id == workflow.id
        and running.current_step_id == step.id
        and running.current_employee_id == step.employee == employee.id
        and _valid_completed(running, workflow)
        and start.request.model == employee.model
        and start.request.system_instructions == employee.instructions
        and start.request.task_instructions == step.instructions
        and start.request.allowed_tools == tuple(employee.allowed_tools)
    ):
        _raise("start_contract")
    if not (
        type(tools) is tuple
        and all(type(tool) is ToolDefinition for tool in tools)
        and tuple(tool.name for tool in tools) == start.request.allowed_tools
        and type(key) is OpenAIApiKey
        and type(approval) is ModelInvocationExecutionApproval
        and callable(transport)
    ):
        _raise("execution_contract")
    try:
        validate_model_invocation_execution_approval(
            start.request, tools, approval, provider="openai"
        )
    except ValueError:
        _raise("execution_contract")


def _valid_completed(
    state: WorkflowExecutionState, workflow: WorkflowDefinition
) -> bool:
    if type(state.completed_step_ids) is not tuple or not all(
        type(x) is str and x for x in state.completed_step_ids
    ):
        return False
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    try:
        values = [positions[item] for item in state.completed_step_ids]
    except KeyError:
        return False
    if any(value >= state.current_step_index for value in values):
        return False
    compressed = tuple(
        value
        for index, value in enumerate(values)
        if index == 0 or values[index - 1] != value
    )
    return compressed == tuple(range(1, state.current_step_index))


def _validate_persistence(
    value: RunningStatePersistenceResult, start: PreparedStepExecutionStart, state: Path
) -> WorkflowExecutionState:
    try:
        bytes_value = state.read_bytes()
        persisted = load_workflow_execution_state(state)
    except (OSError, WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _raise("persistence_contract")
    if not (
        type(value.state_bytes_written) is int
        and value.state_bytes_written > 0
        and value.state_bytes_written == len(bytes_value)
        and persisted == start.running_state
    ):
        _raise("persistence_contract")
    return persisted


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _raise("dependency_error")


def _require_unchanged(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        unchanged = (
            state.read_bytes() == original[0] and events.read_bytes() == original[1]
        )
    except OSError:
        _raise("dependency_error")
    if not unchanged:
        _raise("dependency_error")


def _changed(path: Path, original: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != original
    except OSError:
        return True


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    failed = False
    for path, before in ((state, original[0]), (events, original[1])):
        try:
            if _changed(path, before):
                path.write_bytes(before)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _raise(classification: RoutingClassification) -> None:
    raise PersistedRunningExecutionRoutingCompatibilityError(classification) from None
