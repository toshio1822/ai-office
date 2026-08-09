"""Phase 131 prepared-step start cycle handoff chain bridge boundary."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainReentryContinuationError as Phase124Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary import (
    route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary,
)
from ai_office.engine.terminal_history_contract import load_strict_terminal_history
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory, ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState

Classification = Literal[
    "result_type",
    "workflow_definition",
    "prepared_step_contract",
    "employee_contract",
    "completion_contract",
    "failure_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "start_contract",
    "dependency_error",
    "dependency_rollback",
]
Phase124Function = Callable[
    [object, object, object, object, object],
    PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PreparedStepStartCycleHandoffChainBridgeReentryContinuationFailureDetail:
    """Safe classification for one Phase 131 compatibility failure."""

    classification: Classification


class PreparedStepStartCycleHandoffChainBridgeReentryContinuationError(ValueError):
    """Raised when one prepared-step start bridge cannot safely continue."""


class PreparedStepStartCycleHandoffChainBridgeReentryContinuationCompatibilityError(
    PreparedStepStartCycleHandoffChainBridgeReentryContinuationError
):
    """Raised for a detail-safe Phase 131 compatibility rejection."""

    def __init__(self, classification: Classification) -> None:
        super().__init__("prepared-step start cycle handoff chain bridge inputs are incompatible")
        self.detail = PreparedStepStartCycleHandoffChainBridgeReentryContinuationFailureDetail(
            classification
        )


def route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary(
    result: object,
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase124_function: Phase124Function = (
        route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary
    ),
) -> PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Bridge one exact Phase 130 result through public Phase 124 once."""
    _check_inputs(result, workflow, state_path, events_path, phase124_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is PreparedWorkflowStep:
        _check_prepared_index(result, workflow)
        _check_employee(employee, result)
        assert type(employee) is EmployeeDefinition
        _check_prepared(result, workflow, employee)
    elif type(result) is WorkflowProgressionDecision:
        _check_completion(result, workflow, employee)
    else:
        _check_failure(result, workflow, employee)

    _check_targets(state_path, events_path)
    original = _snapshot(state_path, events_path)

    if type(result) is WorkflowProgressionDecision:
        _check_terminal(result, workflow, state_path, events_path, "succeeded", False)
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _check_terminal(result, workflow, state_path, events_path, "failed", False)
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is PreparedWorkflowStep and type(employee) is EmployeeDefinition
    predecessor = _check_predecessor(result, workflow, state_path, events_path)
    try:
        value = phase124_function(result, workflow, employee, state_path, events_path)
    except Phase124Error as error:
        _compensate_dependency_error(state_path, events_path, original, error)
    except Exception:
        _compensate_dependency_error(state_path, events_path, original, None)

    _require_unchanged(state_path, events_path, original, "start_contract")
    _check_start(value, result, predecessor)
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    dependency: object,
) -> None:
    if type(result) not in (
        PreparedWorkflowStep,
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
        _fail("start_contract")


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


def _check_prepared_index(
    value: PreparedWorkflowStep, workflow: WorkflowDefinition
) -> None:
    if (
        type(value.step_index) is not int
        or value.step_index < 4
        or value.step_index > len(workflow.steps)
    ):
        _fail("prepared_step_contract")


def _check_employee(value: object | None, prepared: PreparedWorkflowStep) -> None:
    if type(value) is not EmployeeDefinition:
        _fail("employee_contract")
    assert type(value) is EmployeeDefinition
    if not _valid_employee(value) or value.id != prepared.employee_id:
        _fail("employee_contract")


def _valid_employee(employee: EmployeeDefinition) -> bool:
    return (
        _nonempty_string(employee.id)
        and _nonempty_string(employee.name)
        and _nonempty_string(employee.role)
        and _nonempty_string(employee.instructions)
        and _nonempty_string(employee.model)
        and type(employee.allowed_tools) is list
        and all(_nonempty_string(tool) for tool in employee.allowed_tools)
    )


def _check_prepared(
    value: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
) -> None:
    _check_prepared_index(value, workflow)
    step = workflow.steps[value.step_index - 1]
    if not (
        _exact_string(value.workflow_id, workflow.id)
        and _exact_string(value.step_id, step.id)
        and _exact_string(value.employee_id, step.employee)
        and _exact_string(value.employee_id, employee.id)
        and _exact_string(value.employee_instructions, employee.instructions)
        and _exact_string(value.step_instructions, step.instructions)
        and _exact_string(value.model, employee.model)
        and type(value.allowed_tool_names) is tuple
        and all(_nonempty_string(tool) for tool in value.allowed_tool_names)
        and value.allowed_tool_names == tuple(employee.allowed_tools)
    ):
        _fail("prepared_step_contract")


def _check_completion(
    value: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object | None,
) -> None:
    final = workflow.steps[-1]
    if not (
        employee is None
        and _exact_string(value.decision, "workflow_complete")
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


def _check_failure(
    value: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    employee: object | None,
) -> None:
    index = value.current_step_index
    if not (
        employee is None
        and _exact_string(value.outcome, "persisted_failure")
        and _exact_string(value.workflow_id, workflow.id)
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
        and _exact_string(value.current_step_id, workflow.steps[index - 1].id)
        and _exact_string(value.current_employee_id, workflow.steps[index - 1].employee)
        and type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
    ):
        _fail("failure_contract")


def _check_targets(state_path: Path, events_path: Path) -> None:
    for path, classification in (
        (state_path, "state_target"),
        (events_path, "event_target"),
    ):
        try:
            if not path.is_file():
                _fail(classification)
        except OSError:
            _fail(classification)


def _snapshot(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state_path.read_bytes()
    except OSError:
        _fail("state_target")
    try:
        event_bytes = events_path.read_bytes()
    except OSError:
        _fail("event_target")
    return state_bytes, event_bytes


def _check_terminal(
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    expected_status: Literal["succeeded", "failed"],
    require_openai: bool,
) -> None:
    try:
        state, history = load_strict_terminal_history(workflow, state_path, events_path)
    except Exception:
        _fail("terminal_contract")
    expected_failure = (
        result.failure_category if type(result) is PersistedExecutionOutcome else None
    )
    if not _valid_terminal_history(
        state,
        history,
        workflow,
        expected_status,
        expected_failure,
        require_openai,
    ):
        _fail("terminal_contract")
    if (
        state.workflow_id,
        state.current_step_id,
        state.current_step_index,
        state.current_employee_id,
    ) != (
        result.workflow_id,
        result.current_step_id,
        result.current_step_index,
        result.current_employee_id,
    ):
        _fail("terminal_contract")


def _check_predecessor(
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> WorkflowExecutionState:
    try:
        state, history = load_strict_terminal_history(workflow, state_path, events_path)
    except Exception:
        _fail("terminal_contract")
    if not _valid_terminal_history(
        state, history, workflow, "succeeded", None, True
    ):
        _fail("terminal_contract")
    previous_index = prepared.step_index - 1
    previous = workflow.steps[previous_index - 1]
    expected_completed = tuple(step.id for step in workflow.steps[:previous_index])
    if not (
        state.workflow_id == workflow.id
        and state.status == "succeeded"
        and state.current_step_id == previous.id
        and state.current_step_index == previous_index
        and state.current_employee_id == previous.employee
        and state.completed_step_ids == expected_completed
        and state.last_failure_category is None
    ):
        _fail("terminal_contract")
    return state


def _valid_terminal_history(
    state: object,
    history: object,
    workflow: WorkflowDefinition,
    expected_status: Literal["succeeded", "failed"],
    expected_failure: object,
    require_openai: bool,
) -> bool:
    if type(state) is not WorkflowExecutionState or type(history) is not tuple:
        return False
    if not history or not _valid_state_shape(state, workflow):
        return False
    if state.status != expected_status or state.last_failure_category != expected_failure:
        return False
    if state.status == "succeeded":
        expected_completed = tuple(
            step.id for step in workflow.steps[: state.current_step_index]
        )
    else:
        expected_completed = tuple(
            step.id for step in workflow.steps[: state.current_step_index - 1]
        )
    if state.completed_step_ids != expected_completed:
        return False
    prior_steps = workflow.steps[: state.current_step_index - 1]
    if len(history) != len(prior_steps) + 1:
        return False
    if any(type(event) is not RuntimeStepEvent for event in history):
        return False
    for position, (event, step) in enumerate(
        zip(history[:-1], prior_steps, strict=True), 1
    ):
        if not _valid_event_shape(event) or not _valid_predecessor(
            event, step, position, state
        ):
            return False
    return _valid_event_shape(history[-1]) and _valid_terminal_event(
        history[-1], state, expected_failure, require_openai
    )


def _valid_state_shape(state: WorkflowExecutionState, workflow: WorkflowDefinition) -> bool:
    return (
        _nonempty_string(state.workflow_id)
        and state.workflow_id == workflow.id
        and type(state.status) is str
        and state.status in {"succeeded", "failed"}
        and _nonempty_string(state.current_step_id)
        and type(state.current_step_index) is int
        and 1 <= state.current_step_index <= len(workflow.steps)
        and _nonempty_string(state.current_employee_id)
        and type(state.completed_step_ids) is tuple
        and all(_nonempty_string(step_id) for step_id in state.completed_step_ids)
        and (
            state.last_failure_category is None
            or (
                type(state.last_failure_category) is str
                and state.last_failure_category in _FAILURE_CATEGORIES
            )
        )
    )


def _valid_event_shape(event: RuntimeStepEvent) -> bool:
    return (
        type(event.event_type) is str
        and event.event_type in {"step_succeeded", "step_failed"}
        and _nonempty_string(event.workflow_id)
        and _nonempty_string(event.step_id)
        and type(event.step_index) is int
        and _nonempty_string(event.employee_id)
        and type(event.previous_status) is str
        and event.previous_status in {"running", "ready", "succeeded", "failed"}
        and type(event.next_status) is str
        and event.next_status in {"running", "succeeded", "failed"}
        and _nonempty_string(event.provider)
        and (
            event.failure_category is None
            or (
                type(event.failure_category) is str
                and event.failure_category in _FAILURE_CATEGORIES
            )
        )
        and _optional_text(event.response_id)
        and _optional_nonempty_text(event.request_id)
        and _optional_text(event.output_text)
        and _optional_text(event.message)
    )


def _valid_predecessor(
    event: RuntimeStepEvent,
    step: WorkflowStepDefinition,
    position: int,
    state: WorkflowExecutionState,
) -> bool:
    return (
        event.event_type == "step_succeeded"
        and event.workflow_id == state.workflow_id
        and event.step_id == step.id
        and event.step_index == position
        and event.employee_id == step.employee
        and event.previous_status == "running"
        and event.next_status == "succeeded"
        and event.failure_category is None
        and _nonempty_string(event.response_id)
        and _nonempty_string(event.output_text)
        and event.message is None
    )


def _valid_terminal_event(
    event: RuntimeStepEvent,
    state: WorkflowExecutionState,
    expected_failure: object,
    require_openai: bool,
) -> bool:
    base = (
        event.workflow_id == state.workflow_id
        and event.step_id == state.current_step_id
        and event.step_index == state.current_step_index
        and event.employee_id == state.current_employee_id
        and event.previous_status == "running"
    )
    if state.status == "succeeded":
        return (
            base
            and event.event_type == "step_succeeded"
            and event.next_status == "succeeded"
            and (not require_openai or (type(event.provider) is str and event.provider == "openai"))
            and expected_failure is None
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and _nonempty_string(event.output_text)
            and event.message is None
        )
    return (
        base
        and event.event_type == "step_failed"
        and event.next_status == "failed"
        and event.failure_category == expected_failure
        and event.response_id is None
        and event.output_text is None
        and _nonempty_string(event.message)
    )


def _check_start(
    value: object,
    prepared: PreparedWorkflowStep,
    predecessor: WorkflowExecutionState,
) -> None:
    if type(value) is not PreparedStepExecutionStart:
        _fail("start_contract")
    request, running = value.request, value.running_state
    if not (
        type(request) is ModelInvocationRequest
        and type(running) is WorkflowExecutionState
        and _exact_string(request.model, prepared.model)
        and _exact_string(request.system_instructions, prepared.employee_instructions)
        and _exact_string(request.task_instructions, prepared.step_instructions)
        and type(request.allowed_tools) is tuple
        and all(_nonempty_string(item) for item in request.allowed_tools)
        and request.allowed_tools == prepared.allowed_tool_names
        and _exact_string(running.workflow_id, prepared.workflow_id)
        and _exact_string(running.status, "running")
        and _exact_string(running.current_step_id, prepared.step_id)
        and type(running.current_step_index) is int
        and running.current_step_index == prepared.step_index
        and _exact_string(running.current_employee_id, prepared.employee_id)
        and type(running.completed_step_ids) is tuple
        and all(_nonempty_string(item) for item in running.completed_step_ids)
        and running.completed_step_ids == predecessor.completed_step_ids
        and running.last_failure_category is None
    ):
        _fail("start_contract")


def _changed(state_path: Path, events_path: Path, original: tuple[bytes, bytes]) -> bool:
    try:
        return (
            not state_path.is_file()
            or not events_path.is_file()
            or state_path.read_bytes() != original[0]
            or events_path.read_bytes() != original[1]
        )
    except OSError:
        return True


def _restore_or_fail(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    failed = False
    for path, contents in ((state_path, original[0]), (events_path, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed:
        _fail("dependency_rollback")
    try:
        restored = (
            state_path.is_file()
            and events_path.is_file()
            and state_path.read_bytes() == original[0]
            and events_path.read_bytes() == original[1]
        )
    except OSError:
        restored = False
    if not restored:
        _fail("dependency_rollback")


def _require_unchanged(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state_path, events_path, original):
        _restore_or_fail(state_path, events_path, original)
        _fail(classification)


def _compensate_dependency_error(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
    safe_error: Phase124Error | None,
) -> None:
    if _changed(state_path, events_path, original):
        _restore_or_fail(state_path, events_path, original)
    if safe_error is not None:
        raise safe_error
    _fail("dependency_error")


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _optional_text(value: object) -> bool:
    return value is None or type(value) is str


def _optional_nonempty_text(value: object) -> bool:
    return value is None or _nonempty_string(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _fail(classification: Classification) -> None:
    raise PreparedStepStartCycleHandoffChainBridgeReentryContinuationCompatibilityError(
        classification
    ) from None
