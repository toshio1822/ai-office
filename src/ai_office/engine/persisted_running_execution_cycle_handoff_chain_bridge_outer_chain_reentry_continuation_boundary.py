"""Phase 155 persisted-running execution cycle handoff chain bridge outer-chain boundary."""

# ruff: noqa: E501,E701,I001

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationError as Phase141Error,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
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
    WorkflowExecutionDataError,
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_workflow_execution_state_json,
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
Phase141Function = Callable[
    [object, object, object, object, object, object, object, object, object, object],
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationFailureDetail:
    """Safe classification for one Phase 155 compatibility failure."""

    classification: Classification


class PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError(
    ValueError
):
    """Raised when one Phase 155 handoff cannot continue safely."""


class PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError(
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError
):
    """Raised for a detail-safe Phase 155 compatibility rejection."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted-running execution cycle handoff chain bridge outer-chain inputs are incompatible"
        )
        self.detail = PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationFailureDetail(
            classification
        )


def route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
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
    phase141_function: Phase141Function = (
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    ),
) -> (
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    """Route one exact Phase 147 persisted-running result through public Phase 141 once."""
    _check_inputs(result, workflow, state_path, events_path, phase141_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is WorkflowProgressionDecision:
        _check_completion(result, workflow)
        _require_none(start, employee, resolved_tools, api_key, approval, transport)
    elif type(result) is PersistedExecutionOutcome:
        _check_failure(result, workflow)
        _require_none(start, employee, resolved_tools, api_key, approval, transport)
    else:
        _check_execution_inputs(
            result,
            start,
            workflow,
            employee,
            resolved_tools,
            api_key,
            approval,
            transport,
        )

    _check_targets(state_path, events_path)
    original = _snapshot(state_path, events_path)

    if type(result) is WorkflowProgressionDecision:
        _check_terminal(result, workflow, state_path, events_path, "succeeded", None)
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _check_terminal(
            result, workflow, state_path, events_path, "failed", result.failure_category
        )
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is RunningStatePersistenceResult
    assert type(start) is PreparedStepExecutionStart
    _check_persistence_result(result, start, original)
    _check_predecessor(start, workflow, state_path, events_path)

    try:
        value = phase141_function(
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
    except Phase141Error as error:
        _compensate_dependency_error(state_path, events_path, original, error)
    except Exception:
        _compensate_dependency_error(state_path, events_path, original, None)

    try:
        _require_unchanged(state_path, events_path, original, "runtime_contract")
        try:
            valid = (
                start.running_state.current_step_index >= 2
                and is_valid_step_runtime_execution_result(
                    value,
                    workflow_id=start.running_state.workflow_id,
                    step_id=start.running_state.current_step_id,
                    step_index=start.running_state.current_step_index,
                    employee_id=start.running_state.current_employee_id,
                )
            )
        except Exception:
            valid = False
        if not valid:
            _fail("runtime_contract")
    except PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore_if_changed(state_path, events_path, original)
        raise
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    dependency: object,
) -> None:
    if type(result) not in (
        RunningStatePersistenceResult,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _fail("workflow_definition")
    if type(state_path) is not _PATH_TYPE:
        _fail("state_target")
    if type(events_path) is not _PATH_TYPE:
        _fail("event_target")
    if state_path == events_path:
        _fail("target_conflict")
    if not callable(dependency):
        _fail("execution_inputs")


def _valid_workflow(workflow: WorkflowDefinition) -> bool:
    if not (
        _nonempty_string(workflow.id)
        and _nonempty_string(workflow.name)
        and _nonempty_string(workflow.description)
        and type(workflow.steps) is list
        and bool(workflow.steps)
    ):
        return False
    if any(
        type(step) is not WorkflowStepDefinition
        or not _nonempty_string(step.id)
        or not _nonempty_string(step.name)
        or not _nonempty_string(step.employee)
        or not _nonempty_string(step.instructions)
        for step in workflow.steps
    ):
        return False
    step_ids = tuple(step.id for step in workflow.steps)
    return len(step_ids) == len(set(step_ids))


def _valid_employee(employee: EmployeeDefinition) -> bool:
    return (
        _nonempty_string(employee.id)
        and _nonempty_string(employee.name)
        and _nonempty_string(employee.role)
        and _nonempty_string(employee.instructions)
        and _nonempty_string(employee.model)
        and type(employee.allowed_tools) is list
        and all(_nonempty_string(tool) for tool in employee.allowed_tools)
        and len(employee.allowed_tools) == len(set(employee.allowed_tools))
    )


def _valid_tool(tool: ToolDefinition) -> bool:
    return (
        type(tool.name) is str
        and bool(tool.name)
        and type(tool.description) is str
        and type(tool.parameters) is tuple
        and all(
            type(parameter) is ToolParameterDefinition
            and _nonempty_string(parameter.name)
            and type(parameter.description) is str
            and type(parameter.type) is str
            and type(parameter.required) is bool
            for parameter in tool.parameters
        )
    )


def _check_execution_inputs(
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
        _fail("start_contract")
    if type(employee) is not EmployeeDefinition or not _valid_employee(employee):
        _fail("employee_contract")
    if type(tools) is not tuple or not all(
        type(tool) is ToolDefinition and _valid_tool(tool) for tool in tools
    ):
        _fail("tools_contract")
    if (
        type(key) is not OpenAIApiKey
        or type(key.value) is not SecretStr
        or type(key.value.get_secret_value()) is not str
        or not key.value.get_secret_value()
    ):
        _fail("credential_contract")
    if (
        type(approval) is not ModelInvocationExecutionApproval
        or type(approval.approved) is not bool
        or approval.approved is not True
        or type(approval.provider) is not str
        or approval.provider != "openai"
        or type(approval.request_fingerprint) is not str
        or type(approval.approved_by) is not str
        or type(approval.approval_id) is not str
        or not _nonempty_string(approval.approved_by)
        or not _nonempty_string(approval.approval_id)
    ):
        _fail("approval_contract")
    if not callable(transport):
        _fail("execution_inputs")

    request = start.request
    running = start.running_state
    if type(request) is not ModelInvocationRequest or type(running) is not WorkflowExecutionState:
        _fail("start_contract")
    if (
        type(running.current_step_index) is not int
        or not 2 <= running.current_step_index <= len(workflow.steps)
    ):
        _fail("start_contract")
    step = workflow.steps[running.current_step_index - 1]
    expected_prefix = tuple(
        item.id for item in workflow.steps[: running.current_step_index - 1]
    )
    if not (
        _exact_string(running.workflow_id, workflow.id)
        and _exact_string(running.status, "running")
        and _exact_string(running.current_step_id, step.id)
        and _exact_string(running.current_employee_id, step.employee)
        and _exact_string(running.current_employee_id, employee.id)
        and type(running.completed_step_ids) is tuple
        and all(_nonempty_string(item) for item in running.completed_step_ids)
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
        _fail("start_contract")
    try:
        validate_model_invocation_execution_approval(
            request, tools, approval, provider="openai"
        )
    except (TypeError, ValueError):
        _fail("approval_contract")
    if type(result) is not RunningStatePersistenceResult:
        _fail("persistence_result_contract")


def _check_persistence_result(
    result: RunningStatePersistenceResult,
    start: PreparedStepExecutionStart,
    original: tuple[bytes, bytes],
) -> None:
    if (
        type(result.state_bytes_written) is not int
        or result.state_bytes_written <= 0
        or result.state_bytes_written != len(original[0])
    ):
        _fail("persistence_result_contract")
    expected = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    if original[0] != expected:
        _fail("persistence_result_contract")


def _check_completion(value: WorkflowProgressionDecision, workflow: WorkflowDefinition) -> None:
    final = workflow.steps[-1]
    if not (
        _exact_string(value.decision, "workflow_complete")
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
        _fail("completion_contract")


def _check_failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    index = value.current_step_index
    if not (
        _exact_string(value.outcome, "persisted_failure")
        and _exact_string(value.workflow_id, workflow.id)
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
        and _exact_string(value.current_step_id, workflow.steps[index - 1].id)
        and _exact_string(value.current_employee_id, workflow.steps[index - 1].employee)
        and type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
    ):
        _fail("failure_contract")


def _require_none(*values: object | None) -> None:
    if any(value is not None for value in values):
        _fail("execution_inputs")


def _check_targets(state: Path, events: Path) -> None:
    for path, classification in ((state, "state_target"), (events, "event_target")):
        try:
            if not path.is_file():
                _fail(classification)
        except OSError:
            _fail(classification)


def _snapshot(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state.read_bytes()
    except OSError:
        _fail("state_target")
    try:
        event_bytes = events.read_bytes()
    except OSError:
        _fail("event_target")
    return state_bytes, event_bytes


def _load_history(
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    try:
        loaded = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
    except Exception:
        _fail("terminal_contract")
    if type(loaded.state) is not WorkflowExecutionState or type(loaded.events) is not tuple:
        _fail("terminal_contract")
    return loaded.state, loaded.events


def _check_terminal(
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    expected_status: Literal["succeeded", "failed"],
    expected_failure: object,
) -> None:
    persisted, history = _load_history(workflow, state, events)
    if not _valid_history(
        workflow,
        persisted,
        history,
        expected_status,
        result,
        expected_failure,
        require_immediate_openai=False,
        allow_empty_success_output=False,
        allow_empty_predecessor_output=True,
    ):
        _fail("terminal_contract")


def _valid_history(
    workflow: WorkflowDefinition,
    state: object,
    history: object,
    expected_status: Literal["succeeded", "failed"],
    result: object | None,
    expected_failure: object,
    *,
    require_immediate_openai: bool,
    allow_empty_success_output: bool,
    allow_empty_predecessor_output: bool,
) -> bool:
    if type(state) is not WorkflowExecutionState or type(history) is not tuple or not history:
        return False
    index = state.current_step_index
    if not (
        type(index) is int
        and _valid_state(state, workflow)
        and state.status == expected_status
        and state.last_failure_category == expected_failure
    ):
        return False
    if result is not None and not (
        _exact_string(result.workflow_id, state.workflow_id)
        and _exact_string(result.current_step_id, state.current_step_id)
        and type(result.current_step_index) is int
        and result.current_step_index == index
        and _exact_string(result.current_employee_id, state.current_employee_id)
    ):
        return False
    expected_completed = (
        tuple(step.id for step in workflow.steps[:index])
        if state.status == "succeeded"
        else tuple(step.id for step in workflow.steps[: index - 1])
    )
    if state.completed_step_ids != expected_completed:
        return False
    prior_steps = workflow.steps[: index - 1]
    if len(history) != len(prior_steps) + 1:
        return False
    if any(type(event) is not RuntimeStepEvent for event in history):
        return False
    for position, (event, step) in enumerate(zip(history[:-1], prior_steps, strict=True), 1):
        if not _valid_predecessor(
            event,
            step,
            position,
            state,
            require_openai=require_immediate_openai and position == len(prior_steps),
            allow_empty_output=allow_empty_predecessor_output,
        ):
            return False
    return _valid_terminal_event(
        history[-1],
        state,
        expected_failure,
        require_openai=require_immediate_openai,
        allow_empty_success_output=allow_empty_success_output,
    )


def _valid_state(state: WorkflowExecutionState, workflow: WorkflowDefinition) -> bool:
    index = state.current_step_index
    if type(index) is not int or not 1 <= index <= len(workflow.steps):
        return False
    current = workflow.steps[index - 1]
    expected_completed = (
        tuple(step.id for step in workflow.steps[:index])
        if state.status == "succeeded"
        else tuple(step.id for step in workflow.steps[: index - 1])
    )
    return (
        _exact_string(state.workflow_id, workflow.id)
        and type(state.status) is str
        and state.status in {"succeeded", "failed"}
        and _exact_string(state.current_step_id, current.id)
        and _exact_string(state.current_employee_id, current.employee)
        and type(state.completed_step_ids) is tuple
        and all(_nonempty_string(item) for item in state.completed_step_ids)
        and state.completed_step_ids == expected_completed
        and (
            state.last_failure_category is None
            or (
                type(state.last_failure_category) is str
                and state.last_failure_category in _FAILURE_CATEGORIES
            )
        )
    )


def _valid_predecessor(
    event: RuntimeStepEvent,
    step: WorkflowStepDefinition,
    position: int,
    state: WorkflowExecutionState,
    *,
    require_openai: bool,
    allow_empty_output: bool = False,
) -> bool:
    return (
        type(event) is RuntimeStepEvent
        and _exact_string(event.event_type, "step_succeeded")
        and _exact_string(event.workflow_id, state.workflow_id)
        and _exact_string(event.step_id, step.id)
        and type(event.step_index) is int
        and event.step_index == position
        and _exact_string(event.employee_id, step.employee)
        and _exact_string(event.previous_status, "running")
        and _exact_string(event.next_status, "succeeded")
        and _nonempty_string(event.provider)
        and (not require_openai or event.provider == "openai")
        and event.failure_category is None
        and _nonempty_string(event.response_id)
        and _nonempty_string(event.request_id)
        and type(event.output_text) is str
        and (allow_empty_output or bool(event.output_text))
        and event.message is None
    )


def _valid_terminal_event(
    event: RuntimeStepEvent,
    state: WorkflowExecutionState,
    expected_failure: object,
    *,
    require_openai: bool,
    allow_empty_success_output: bool,
) -> bool:
    base = (
        type(event) is RuntimeStepEvent
        and _exact_string(event.workflow_id, state.workflow_id)
        and _exact_string(event.step_id, state.current_step_id)
        and type(event.step_index) is int
        and event.step_index == state.current_step_index
        and _exact_string(event.employee_id, state.current_employee_id)
        and _exact_string(event.previous_status, "running")
        and _nonempty_string(event.provider)
        and (not require_openai or event.provider == "openai")
        and (event.request_id is None or _nonempty_string(event.request_id))
    )
    if state.status == "succeeded":
        return (
            base
            and _exact_string(event.event_type, "step_succeeded")
            and _exact_string(event.next_status, "succeeded")
            and expected_failure is None
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and type(event.output_text) is str
            and (allow_empty_success_output or bool(event.output_text))
            and event.message is None
        )
    return (
        base
        and _exact_string(event.event_type, "step_failed")
        and _exact_string(event.next_status, "failed")
        and type(expected_failure) is str
        and expected_failure in _FAILURE_CATEGORIES
        and event.failure_category == expected_failure
        and event.response_id is None
        and event.output_text is None
        and _nonempty_string(event.message)
    )


def _check_predecessor(
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
    except (OSError, UnicodeError, WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _fail("persistence_result_contract")
    running = start.running_state
    if (
        type(loaded) is not WorkflowExecutionState
        or loaded != running
        or type(history.state) is not WorkflowExecutionState
        or history.state != running
        or type(history.events) is not tuple
    ):
        _fail("persistence_result_contract")
    expected_steps = workflow.steps[: running.current_step_index - 1]
    if len(history.events) != len(expected_steps):
        _fail("persistence_result_contract")
    for position, (event, step) in enumerate(
        zip(history.events, expected_steps, strict=True), 1
    ):
        if type(event) is not RuntimeStepEvent or not _valid_predecessor_event(
            event,
            step,
            position,
            running,
            require_openai=position == len(expected_steps),
            allow_empty_output=True,
            allow_none_request_id=(position >= 5) or (position == len(expected_steps)),
        ):
            _fail("persistence_result_contract")


def _valid_predecessor_event(
    event: RuntimeStepEvent,
    step: WorkflowStepDefinition,
    position: int,
    state: WorkflowExecutionState,
    require_openai: bool = False,
    allow_empty_output: bool = False,
    allow_none_request_id: bool = False,
) -> bool:
    none_request_id = event.request_id is None
    provider_valid = _nonempty_string(event.provider) and (
        (not require_openai and not none_request_id) or event.provider == "openai"
    )
    request_id_valid = (none_request_id and allow_none_request_id) or (
        _nonempty_string(event.request_id)
    )
    return (
        _exact_string(event.event_type, "step_succeeded")
        and _exact_string(event.workflow_id, state.workflow_id)
        and _exact_string(event.step_id, step.id)
        and type(event.step_index) is int
        and event.step_index == position
        and _exact_string(event.employee_id, step.employee)
        and _exact_string(event.previous_status, "running")
        and _exact_string(event.next_status, "succeeded")
        and provider_valid
        and event.failure_category is None
        and _nonempty_string(event.response_id)
        and request_id_valid
        and type(event.output_text) is str
        and (allow_empty_output or bool(event.output_text))
        and event.message is None
    )


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_if_changed(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    changed = _changed(state, original[0]) or _changed(events, original[1])
    if not changed:
        return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed or _changed(state, original[0]) or _changed(events, original[1]):
        _fail("dependency_rollback")


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _fail(classification)


def _compensate_dependency_error(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    safe_error: Phase141Error | None,
) -> None:
    _restore_if_changed(state, events, original)
    if safe_error is not None:
        raise safe_error
    _fail("dependency_error")


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _fail(classification: Classification) -> None:
    raise PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError(
        classification
    ) from None
