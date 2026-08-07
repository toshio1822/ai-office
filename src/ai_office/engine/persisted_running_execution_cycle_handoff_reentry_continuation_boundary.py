"""Phase 119 persisted-running execution cycle handoff reentry continuation boundary."""

# ruff: noqa: E501,E701,I001

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.persisted_execution_outcome_reentry import PersistedExecutionOutcome
from ai_office.engine.persisted_running_execution_cycle_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleReentryContinuationError,
    route_persisted_running_execution_cycle_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.terminal_history_contract import TerminalHistoryContractError, load_strict_terminal_history
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationExecutionApproval, ModelInvocationFailureCategory, ModelInvocationRequest, validate_model_invocation_execution_approval
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import StepRuntimeExecutionFailure, StepRuntimeExecutionSuccess, WorkflowExecutionState, is_valid_step_runtime_execution_result
from ai_office.storage import RunningStatePersistenceResult, load_workflow_execution_history, load_workflow_execution_state
from ai_office.storage.workflow_execution_history import WorkflowExecutionDataError, WorkflowExecutionLoadError
from ai_office.storage.workflow_execution_persistence import WorkflowExecutionPersistenceTargets
from ai_office.tools import ToolDefinition, ToolParameterDefinition

Classification = Literal[
    "result_type", "workflow_definition", "execution_inputs", "persistence_result_contract",
    "start_contract", "employee_contract", "tools_contract", "credential_contract",
    "approval_contract", "completion_contract", "failure_contract", "state_target",
    "event_target", "target_conflict", "terminal_contract", "runtime_contract",
    "dependency_error", "dependency_rollback",
]
Phase112Function = Callable[..., StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure | WorkflowProgressionDecision | PersistedExecutionOutcome]
_PATH_TYPE = type(Path())
_FAILURES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class PersistedRunningExecutionCycleHandoffReentryContinuationFailureDetail:
    classification: Classification


class PersistedRunningExecutionCycleHandoffReentryContinuationError(ValueError):
    """Base error for the Phase 119 boundary."""


class PersistedRunningExecutionCycleHandoffReentryContinuationCompatibilityError(
    PersistedRunningExecutionCycleHandoffReentryContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__("persisted-running execution cycle handoff reentry continuation inputs are incompatible")
        self.detail = PersistedRunningExecutionCycleHandoffReentryContinuationFailureDetail(classification)


def route_persisted_running_execution_cycle_handoff_reentry_continuation_boundary(
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
    phase112_function: Phase112Function = route_persisted_running_execution_cycle_reentry_continuation_boundary,
) -> StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure | WorkflowProgressionDecision | PersistedExecutionOutcome:
    _validate_inputs(result, workflow, state_path, events_path, phase112_function)
    assert type(workflow) is WorkflowDefinition and type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    if type(result) is WorkflowProgressionDecision:
        _completion(result, workflow)
        _none(start, employee, resolved_tools, api_key, approval, transport)
    elif type(result) is PersistedExecutionOutcome:
        _failure(result, workflow)
        _none(start, employee, resolved_tools, api_key, approval, transport)
    else:
        _execution_inputs(result, start, workflow, employee, resolved_tools, api_key, approval, transport)
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
    assert type(result) is RunningStatePersistenceResult and type(start) is PreparedStepExecutionStart
    if result.state_bytes_written != len(original[0]):
        _compatibility_error("persistence_result_contract")
    _predecessor(start, workflow, state_path, events_path)
    try:
        value = phase112_function(result, start, workflow, employee, state_path, events_path, resolved_tools, api_key, approval, transport)
    except PersistedRunningExecutionCycleReentryContinuationError as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _compatibility_error("dependency_error")
    try:
        _unchanged(state_path, events_path, original, "runtime_contract")
        if type(value) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure) or not is_valid_step_runtime_execution_result(value, workflow_id=start.running_state.workflow_id, step_id=start.running_state.current_step_id, step_index=start.running_state.current_step_index, employee_id=start.running_state.current_employee_id):
            _compatibility_error("runtime_contract")
    except PersistedRunningExecutionCycleHandoffReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore_if_changed(state_path, events_path, original)
        raise
    return value


def _validate_inputs(result: object, workflow: object, state: object, events: object, function: object) -> None:
    if type(result) not in (RunningStatePersistenceResult, WorkflowProgressionDecision, PersistedExecutionOutcome): _compatibility_error("result_type")
    if type(workflow) is not WorkflowDefinition or not _workflow(workflow): _compatibility_error("workflow_definition")
    if type(state) is not _PATH_TYPE: _compatibility_error("state_target")
    if type(events) is not _PATH_TYPE: _compatibility_error("event_target")
    if state == events: _compatibility_error("target_conflict")
    if not callable(function): _compatibility_error("execution_inputs")


def _none(*values: object | None) -> None:
    if any(value is not None for value in values): _compatibility_error("execution_inputs")


def _workflow(workflow: WorkflowDefinition) -> bool:
    return type(workflow.id) is str and type(workflow.name) is str and type(workflow.description) is str and type(workflow.steps) is list and bool(workflow.steps) and all(type(step) is WorkflowStepDefinition and type(step.id) is str and type(step.name) is str and type(step.employee) is str and type(step.instructions) is str for step in workflow.steps)


def _employee(value: EmployeeDefinition) -> None:
    if not (type(value.id) is str and type(value.name) is str and type(value.role) is str and type(value.instructions) is str and type(value.model) is str and type(value.allowed_tools) is list and all(type(item) is str for item in value.allowed_tools)): _compatibility_error("employee_contract")


def _tool(value: ToolDefinition) -> bool:
    return type(value.name) is str and type(value.description) is str and type(value.parameters) is tuple and all(type(parameter) is ToolParameterDefinition and type(parameter.name) is str and type(parameter.description) is str and type(parameter.type) is str and type(parameter.required) is bool for parameter in value.parameters)


def _execution_inputs(result: RunningStatePersistenceResult, start: object | None, workflow: WorkflowDefinition, employee: object | None, tools: object | None, key: object | None, approval: object | None, transport: object | None) -> None:
    if type(result.state_bytes_written) is not int or result.state_bytes_written <= 0: _compatibility_error("persistence_result_contract")
    if type(start) is not PreparedStepExecutionStart: _compatibility_error("start_contract")
    if type(employee) is not EmployeeDefinition: _compatibility_error("employee_contract")
    _employee(employee)
    if type(tools) is not tuple or not all(type(tool) is ToolDefinition and _tool(tool) for tool in tools): _compatibility_error("tools_contract")
    if type(key) is not OpenAIApiKey or type(key.value) is not SecretStr or not key.value.get_secret_value(): _compatibility_error("credential_contract")
    if type(approval) is not ModelInvocationExecutionApproval or not (type(approval.approved) is bool and type(approval.provider) is str and type(approval.request_fingerprint) is str and type(approval.approved_by) is str and type(approval.approval_id) is str): _compatibility_error("approval_contract")
    if not callable(transport): _compatibility_error("execution_inputs")
    request, running = start.request, start.running_state
    if type(request) is not ModelInvocationRequest or type(running) is not WorkflowExecutionState: _compatibility_error("start_contract")
    if type(running.current_step_index) is not int or not 2 <= running.current_step_index <= len(workflow.steps): _compatibility_error("start_contract")
    step = workflow.steps[running.current_step_index - 1]
    prefix = tuple(item.id for item in workflow.steps[: running.current_step_index - 1])
    if not (_str(running.status, "running") and running.last_failure_category is None and _str(running.workflow_id, workflow.id) and _str(running.current_step_id, step.id) and _str(running.current_employee_id, step.employee) and _str(employee.id, running.current_employee_id) and _tuple(running.completed_step_ids, prefix) and _str(request.model, employee.model) and _str(request.system_instructions, employee.instructions) and _str(request.task_instructions, step.instructions) and _tuple(request.allowed_tools, tuple(employee.allowed_tools)) and _tuple(tuple(tool.name for tool in tools), request.allowed_tools)): _compatibility_error("start_contract")
    try: validate_model_invocation_execution_approval(request, tools, approval, provider="openai")
    except ValueError: _compatibility_error("approval_contract")


def _completion(value: WorkflowProgressionDecision, workflow: WorkflowDefinition) -> None:
    final = workflow.steps[-1]
    if not (_str(value.decision, "workflow_complete") and _str(value.workflow_id, workflow.id) and _str(value.current_step_id, final.id) and type(value.current_step_index) is int and value.current_step_index == len(workflow.steps) and _str(value.current_employee_id, final.employee) and value.next_step_id is None and value.next_step_index is None and value.next_employee_id is None and _str(value.reason, "last_step_succeeded")): _compatibility_error("completion_contract")


def _failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if not (_str(value.outcome, "persisted_failure") and type(value.current_step_index) is int and 1 <= value.current_step_index <= len(workflow.steps)): _compatibility_error("failure_contract")
    step = workflow.steps[value.current_step_index - 1]
    if not (_str(value.workflow_id, workflow.id) and _str(value.current_step_id, step.id) and _str(value.current_employee_id, step.employee) and type(value.failure_category) is str and value.failure_category in _FAILURES): _compatibility_error("failure_contract")


def _targets(state: Path, events: Path) -> None:
    for path, classification in ((state, "state_target"), (events, "event_target")):
        try:
            if not path.is_file(): _compatibility_error(classification)
        except OSError: _compatibility_error(classification)


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try: first = state.read_bytes()
    except OSError: _compatibility_error("state_target")
    try: second = events.read_bytes()
    except OSError: _compatibility_error("event_target")
    return first, second


def _terminal(value: WorkflowProgressionDecision | PersistedExecutionOutcome, workflow: WorkflowDefinition, state: Path, events: Path, status: str) -> None:
    try: persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError): _compatibility_error("terminal_contract")
    if persisted.status != status or (persisted.workflow_id, persisted.current_step_id, persisted.current_step_index, persisted.current_employee_id) != (value.workflow_id, value.current_step_id, value.current_step_index, value.current_employee_id) or (type(value) is PersistedExecutionOutcome and persisted.last_failure_category != value.failure_category): _compatibility_error("terminal_contract")


def _predecessor(start: PreparedStepExecutionStart, workflow: WorkflowDefinition, state: Path, events: Path) -> None:
    try:
        loaded = load_workflow_execution_state(state)
        history = load_workflow_execution_history(WorkflowExecutionPersistenceTargets(state, events))
    except (OSError, WorkflowExecutionDataError, WorkflowExecutionLoadError): _compatibility_error("persistence_result_contract")
    running = start.running_state
    if loaded != running: _compatibility_error("persistence_result_contract")
    expected = workflow.steps[: running.current_step_index - 1]
    if len(history.events) != len(expected): _compatibility_error("persistence_result_contract")
    for event, step in zip(history.events, expected, strict=True):
        if not (event.event_type == "step_succeeded" and event.workflow_id == workflow.id and event.step_id == step.id and event.step_index == workflow.steps.index(step) + 1 and event.employee_id == step.employee and event.previous_status == "running" and event.next_status == "succeeded" and event.failure_category is None and type(event.response_id) is str and event.response_id and type(event.output_text) is str and event.output_text and event.message is None): _compatibility_error("persistence_result_contract")


def _unchanged(state: Path, events: Path, original: tuple[bytes, bytes], classification: Classification) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _compatibility_error(classification)


def _changed(path: Path, before: bytes) -> bool:
    try: return not path.is_file() or path.read_bytes() != before
    except OSError: return True


def _restore_if_changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    if not (_changed(state, original[0]) or _changed(events, original[1])): return
    failed = False
    for path, data in ((state, original[0]), (events, original[1])):
        try: path.write_bytes(data)
        except OSError: failed = True
    if failed: _compatibility_error("dependency_rollback")


def _compatibility_error(classification: Classification) -> None:
    raise PersistedRunningExecutionCycleHandoffReentryContinuationCompatibilityError(classification) from None


def _str(value: object, expected: str) -> bool: return type(value) is str and value == expected
def _tuple(value: object, expected: tuple[str, ...]) -> bool: return type(value) is tuple and all(type(item) is str for item in value) and value == expected
