"""Bridge exact Phase 55 results to the existing Phase 49 execution boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_running_execution_bridge_reentry import (
    PersistedRunningExecutionBridgeError,
    route_persisted_running_execution_bridge_reentry,
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
from ai_office.tools import ToolDefinition

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
    "execution_result_contract",
    "dependency_error",
    "dependency_rollback",
]
_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
Phase49Function = Callable[
    ...,
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]


@dataclass(frozen=True)
class PersistedRunningExecutionPhaseBridgeFailureDetail:
    classification: Classification


class PersistedRunningExecutionPhaseBridgeError(ValueError):
    """Raised when Phase 56 cannot safely route its supplied result."""


class PersistedRunningExecutionPhaseBridgeCompatibilityError(
    PersistedRunningExecutionPhaseBridgeError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted-running execution phase bridge inputs are incompatible"
        )
        self.detail = PersistedRunningExecutionPhaseBridgeFailureDetail(classification)


def route_persisted_running_execution_phase_bridge_reentry(
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
    phase49_function: Phase49Function = (
        route_persisted_running_execution_bridge_reentry
    ),
) -> (
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    _top_level(result, workflow, state_path, events_path, phase49_function)
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    if type(result) is WorkflowProgressionDecision:
        _completion(result, workflow)
        _none(start, employee, resolved_tools, api_key, approval, transport)
    elif type(result) is PersistedExecutionOutcome:
        _failure(result, workflow)
        _none(start, employee, resolved_tools, api_key, approval, transport)
    else:
        _execution_inputs(
            result,
            start,
            workflow,
            employee,
            resolved_tools,
            api_key,
            approval,
            transport,
        )
    _targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _terminal(result, workflow, state_path, events_path, "succeeded")
        _unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _terminal(result, workflow, state_path, events_path, "failed")
        _unchanged(state_path, events_path, original, "terminal_contract")
        return result
    assert type(start) is PreparedStepExecutionStart
    if result.state_bytes_written != len(original[0]):
        _raise("persistence_result_contract")
    _prior_success_contract(start, workflow, state_path, events_path)
    try:
        value = phase49_function(
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
    except PersistedRunningExecutionBridgeError as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("dependency_error")
    if _changed(state_path, original[0]) or _changed(events_path, original[1]):
        _restore(state_path, events_path, original)
        _raise("execution_result_contract")
    if type(value) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        _raise("execution_result_contract")
    if not is_valid_step_runtime_execution_result(
        value,
        workflow_id=start.running_state.workflow_id,
        step_id=start.running_state.current_step_id,
        step_index=start.running_state.current_step_index,
        employee_id=start.running_state.current_employee_id,
    ):
        _raise("execution_result_contract")
    return value


def _top_level(
    result: object, workflow: object, state: object, events: object, function: object
) -> None:
    if type(result) not in (
        RunningStatePersistenceResult,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if not isinstance(state, Path):
        _raise("state_target")
    if not isinstance(events, Path):
        _raise("event_target")
    if state == events:
        _raise("target_conflict")
    if not callable(function):
        _raise("execution_inputs")


def _none(*values: object | None) -> None:
    if any(value is not None for value in values):
        _raise("execution_inputs")


def _execution_inputs(
    result: RunningStatePersistenceResult,
    start: object | None,
    workflow: WorkflowDefinition,
    employee: object | None,
    tools: object | None,
    key: object | None,
    approval: object | None,
    transport: object | None,
) -> None:
    if type(result.state_bytes_written) is not int or result.state_bytes_written <= 0:
        _raise("persistence_result_contract")
    if type(start) is not PreparedStepExecutionStart:
        _raise("start_contract")
    if type(employee) is not EmployeeDefinition:
        _raise("employee_contract")
    if type(tools) is not tuple:
        _raise("tools_contract")
    if not all(type(tool) is ToolDefinition for tool in tools):
        _raise("tools_contract")
    if type(key) is not OpenAIApiKey:
        _raise("credential_contract")
    if type(approval) is not ModelInvocationExecutionApproval:
        _raise("approval_contract")
    if not callable(transport):
        _raise("execution_inputs")
    request, running = start.request, start.running_state
    if type(request) is not ModelInvocationRequest:
        _raise("start_contract")
    if type(running) is not WorkflowExecutionState:
        _raise("start_contract")
    if type(running.current_step_index) is not int or not (
        1 <= running.current_step_index <= len(workflow.steps)
    ):
        _raise("start_contract")
    step = workflow.steps[running.current_step_index - 1]
    prefix = tuple(item.id for item in workflow.steps[: running.current_step_index - 1])
    if not (
        _exact_str(running.status, "running")
        and running.last_failure_category is None
        and _exact_str(running.workflow_id, workflow.id)
        and _exact_str(running.current_step_id, step.id)
        and _exact_str(running.current_employee_id, step.employee)
        and _exact_str(running.current_employee_id, employee.id)
        and _exact_tuple(running.completed_step_ids, prefix)
        and _exact_str(request.model, employee.model)
        and _exact_str(request.system_instructions, employee.instructions)
        and _exact_str(request.task_instructions, step.instructions)
        and _exact_tuple(request.allowed_tools, tuple(employee.allowed_tools))
        and _exact_tuple(tuple(tool.name for tool in tools), request.allowed_tools)
    ):
        _raise("start_contract")
    try:
        validate_model_invocation_execution_approval(
            request, tools, approval, provider="openai"
        )
    except ValueError:
        _raise("approval_contract")


def _completion(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        _exact_str(value.decision, "workflow_complete")
        and _exact_str(value.workflow_id, workflow.id)
        and _exact_str(value.current_step_id, final.id)
        and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps)
        and _exact_str(value.current_employee_id, final.employee)
        and value.next_step_id
        is value.next_step_index
        is value.next_employee_id
        is None
        and _exact_str(value.reason, "last_step_succeeded")
    ):
        _raise("completion_contract")


def _failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if (
        not _exact_str(value.outcome, "persisted_failure")
        or type(value.current_step_index) is not int
        or not 1 <= value.current_step_index <= len(workflow.steps)
    ):
        _raise("failure_contract")
    step = workflow.steps[value.current_step_index - 1]
    if not (
        _exact_str(value.workflow_id, workflow.id)
        and _exact_str(value.current_step_id, step.id)
        and _exact_str(value.current_employee_id, step.employee)
        and type(value.failure_category) is str
        and value.failure_category in _CATEGORIES
    ):
        _raise("failure_contract")


def _targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _raise("state_target")
    except OSError:
        _raise("state_target")
    try:
        if not events.is_file():
            _raise("event_target")
    except OSError:
        _raise("event_target")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state.read_bytes()
    except OSError:
        _raise("state_target")
    try:
        return state_bytes, events.read_bytes()
    except OSError:
        _raise("event_target")


def _terminal(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    status: Literal["succeeded", "failed"],
) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except TerminalHistoryContractError:
        _raise("terminal_contract")
    if persisted.status != status or (
        persisted.workflow_id,
        persisted.current_step_id,
        persisted.current_step_index,
        persisted.current_employee_id,
    ) != (
        value.workflow_id,
        value.current_step_id,
        value.current_step_index,
        value.current_employee_id,
    ):
        _raise("terminal_contract")
    if (
        type(value) is PersistedExecutionOutcome
        and persisted.last_failure_category != value.failure_category
    ):
        _raise("terminal_contract")


def _prior_success_contract(
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
        _raise("persistence_result_contract")
    if loaded != start.running_state:
        _raise("persistence_result_contract")
    expected = workflow.steps[: start.running_state.current_step_index - 1]
    if len(history.events) != len(expected):
        _raise("persistence_result_contract")
    for event, step in zip(history.events, expected, strict=True):
        if not (
            event.event_type == "step_succeeded"
            and event.workflow_id == workflow.id
            and event.step_id == step.id
            and event.step_index == workflow.steps.index(step) + 1
            and event.employee_id == step.employee
            and event.previous_status == "running"
            and event.next_status == "succeeded"
            and event.failure_category is None
            and isinstance(event.response_id, str)
            and bool(event.response_id)
            and isinstance(event.output_text, str)
            and bool(event.output_text)
            and event.message is None
        ):
            _raise("persistence_result_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore(state, events, original)
        _raise(classification)


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    failed = False
    for path, before in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(before)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _restore_if_changed(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        changed = _changed(state, original[0]) or _changed(events, original[1])
    except OSError:
        changed = True
    if changed:
        _restore(state, events, original)


def _raise(classification: Classification) -> None:
    raise PersistedRunningExecutionPhaseBridgeCompatibilityError(
        classification
    ) from None


def _exact_str(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _exact_tuple(value: object, expected: tuple[str, ...]) -> bool:
    return (
        type(value) is tuple
        and all(type(item) is str for item in value)
        and value == expected
    )
