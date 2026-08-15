"""Phase 138 outer prepared-step start bridge."""

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
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeReentryContinuationError as Phase131Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory, ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
)

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
Phase131Function = Callable[
    [object, object, object, object, object],
    PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationFailureDetail:
    """Safe classification for one Phase 138 compatibility failure."""

    classification: Classification


class PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationError(ValueError):
    """Raised when one Phase 137 result cannot cross the outer bridge."""


class PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError(
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationError
):
    """Raised for a detail-safe Phase 138 compatibility rejection."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "prepared-step start cycle handoff chain bridge outer inputs are incompatible"
        )
        self.detail = PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationFailureDetail(
            classification
        )


def route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
    result: object,
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase131_function: Phase131Function = (
        route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary
    ),
) -> PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Route one exact Phase 137 result through public Phase 131 once."""
    _check_inputs(result, workflow, state_path, events_path, phase131_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is PreparedWorkflowStep:
        _check_prepared_index(result, workflow)
        _check_employee(employee, result)
        assert type(employee) is EmployeeDefinition
        _check_prepared(result, workflow, employee)
        stop = False
    elif type(result) is WorkflowProgressionDecision:
        _check_completion(result, workflow, employee)
        stop = True
    else:
        assert type(result) is PersistedExecutionOutcome
        _check_failure(result, workflow, employee)
        stop = True

    _check_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)

    if stop:
        assert type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome)
        expected_status: Literal["succeeded", "failed"] = (
            "succeeded" if type(result) is WorkflowProgressionDecision else "failed"
        )
        expected_failure = (
            None if type(result) is WorkflowProgressionDecision else result.failure_category
        )
        _check_terminal(
            result,
            workflow,
            state_path,
            events_path,
            expected_status,
            expected_failure,
            require_immediate_openai=False,
            allow_empty_success_output=False,
            allow_empty_predecessor_output=False,
        )
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is PreparedWorkflowStep
    assert type(employee) is EmployeeDefinition
    predecessor = _check_predecessor(result, workflow, state_path, events_path)
    try:
        value = phase131_function(result, workflow, employee, state_path, events_path)
    except Phase131Error as error:
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
        _fail("dependency_error")


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
    ids = tuple(step.id for step in workflow.steps)
    return len(ids) == len(set(ids))


def _check_prepared_index(value: PreparedWorkflowStep, workflow: WorkflowDefinition) -> None:
    if (
        type(value.step_index) is not int
        or value.step_index < 5
        or value.step_index > len(workflow.steps)
    ):
        _fail("prepared_step_contract")


def _check_employee(value: object, prepared: PreparedWorkflowStep) -> None:
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
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
) -> None:
    step = workflow.steps[prepared.step_index - 1]
    if not (
        _exact_string(prepared.workflow_id, workflow.id)
        and _exact_string(prepared.step_id, step.id)
        and _exact_string(prepared.employee_id, step.employee)
        and _exact_string(prepared.employee_id, employee.id)
        and _exact_string(prepared.employee_instructions, employee.instructions)
        and _exact_string(prepared.step_instructions, step.instructions)
        and _exact_string(prepared.model, employee.model)
        and type(prepared.allowed_tool_names) is tuple
        and all(_nonempty_string(tool) for tool in prepared.allowed_tool_names)
        and prepared.allowed_tool_names == tuple(employee.allowed_tools)
    ):
        _fail("prepared_step_contract")


def _check_completion(
    result: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object,
) -> None:
    final = workflow.steps[-1]
    if not (
        employee is None
        and _exact_string(result.decision, "workflow_complete")
        and _exact_string(result.workflow_id, workflow.id)
        and _exact_string(result.current_step_id, final.id)
        and type(result.current_step_index) is int
        and result.current_step_index == len(workflow.steps)
        and _exact_string(result.current_employee_id, final.employee)
        and result.next_step_id is None
        and result.next_step_index is None
        and result.next_employee_id is None
        and _exact_string(result.reason, "last_step_succeeded")
    ):
        _fail("completion_contract")


def _check_failure(
    result: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    employee: object,
) -> None:
    index = result.current_step_index
    if not (
        employee is None
        and _exact_string(result.outcome, "persisted_failure")
        and _exact_string(result.workflow_id, workflow.id)
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
        and _exact_string(result.current_step_id, workflow.steps[index - 1].id)
        and _exact_string(result.current_employee_id, workflow.steps[index - 1].employee)
        and type(result.failure_category) is str
        and result.failure_category in _FAILURE_CATEGORIES
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


def _capture_targets(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state_path.read_bytes()
    except OSError:
        _fail("state_target")
    try:
        event_bytes = events_path.read_bytes()
    except OSError:
        _fail("event_target")
    return state_bytes, event_bytes


def _load_history(
    workflow: WorkflowDefinition, state_path: Path, events_path: Path
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    try:
        loaded = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except Exception:
        _fail("terminal_contract")
    if type(loaded.state) is not WorkflowExecutionState or type(loaded.events) is not tuple:
        _fail("terminal_contract")
    return loaded.state, loaded.events


def _check_predecessor(
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> WorkflowExecutionState:
    state, history = _load_history(workflow, state_path, events_path)
    if not _valid_history(
        workflow,
        state,
        history,
        "succeeded",
        None,
        None,
        require_immediate_openai=True,
        allow_empty_success_output=True,
        allow_empty_predecessor_output=True,
        allow_missing_immediate_request_id=prepared.step_index - 1 >= 6,
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


def _check_terminal(
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    expected_status: Literal["succeeded", "failed"],
    expected_failure: object,
    *,
    require_immediate_openai: bool,
    allow_empty_success_output: bool,
    allow_empty_predecessor_output: bool,
) -> None:
    state, history = _load_history(workflow, state_path, events_path)
    if not _valid_history(
        workflow,
        state,
        history,
        expected_status,
        result,
        expected_failure,
        require_immediate_openai=require_immediate_openai,
        allow_empty_success_output=allow_empty_success_output,
        allow_empty_predecessor_output=allow_empty_predecessor_output,
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
    allow_missing_immediate_request_id: bool = False,
) -> bool:
    if type(state) is not WorkflowExecutionState or type(history) is not tuple or not history:
        return False
    index = state.current_step_index
    if not (
        _valid_state(state, workflow)
        and state.status == expected_status
        and state.last_failure_category == expected_failure
        and type(index) is int
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
    for position, (event, step) in enumerate(
        zip(history[:-1], prior_steps, strict=True), 1
    ):
        if not _valid_predecessor(
            event,
            step,
            position,
            state,
            require_openai=require_immediate_openai and position == len(prior_steps),
            allow_empty_output=allow_empty_predecessor_output,
            allow_missing_request_id=(
                allow_missing_immediate_request_id and position == len(prior_steps)
            ),
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
    return (
        _exact_string(state.workflow_id, workflow.id)
        and type(state.status) is str
        and state.status in {"succeeded", "failed"}
        and _exact_string(state.current_step_id, current.id)
        and _exact_string(state.current_employee_id, current.employee)
        and type(state.completed_step_ids) is tuple
        and all(_nonempty_string(item) for item in state.completed_step_ids)
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
    allow_missing_request_id: bool = False,
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
        and (
            (allow_missing_request_id and event.request_id is None)
            or _nonempty_string(event.request_id)
        )
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
        and running.current_step_index >= 5
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
    if failed or _changed(state_path, events_path, original):
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
    safe_error: Phase131Error | None,
) -> None:
    if _changed(state_path, events_path, original):
        _restore_or_fail(state_path, events_path, original)
    if safe_error is not None:
        raise safe_error
    _fail("dependency_error")


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _fail(classification: Classification) -> None:
    raise PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError(
        classification
    ) from None
