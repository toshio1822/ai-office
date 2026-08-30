"""Phase 129 classified persisted-outcome progression handoff boundary."""

# ruff: noqa: E501,E701

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationError as Phase122Error,
)
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary import (
    route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.terminal_history_contract import (
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "success_contract",
    "failure_contract",
    "completion_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "progression_contract",
    "dependency_error",
    "dependency_rollback",
]
Phase122Function = Callable[
    [object, object, object, object], WorkflowProgressionDecision | PersistedExecutionOutcome
]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationFailureDetail:
    """Safe classification for one Phase 129 compatibility failure."""

    classification: Classification


class ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationError(
    ValueError
):
    """Raised when one classified persisted outcome cannot cross Phase 129 safely."""


class ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationCompatibilityError(
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationError
):
    """Raised for a detail-safe Phase 129 compatibility rejection."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "classified persisted-outcome progression cycle handoff chain reentry continuation inputs are incompatible"
        )
        self.detail = ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationFailureDetail(
            classification
        )


def route_classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase122_function: Phase122Function = (
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary
    ),
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Route one persisted success through public Phase 122 exactly once."""
    _check_inputs(result, workflow, state_path, events_path, phase122_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is WorkflowProgressionDecision:
        _check_completion(result, workflow)
        terminal_status = "succeeded"
        stop = True
    else:
        assert type(result) is PersistedExecutionOutcome
        if result.outcome == "persisted_success":
            _check_success(result, workflow)
            terminal_status = "succeeded"
            stop = False
        elif result.outcome == "persisted_failure":
            _check_failure(result, workflow)
            terminal_status = "failed"
            stop = True
        else:
            _fail("result_type")

    _check_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)
    _check_terminal(result, workflow, state_path, events_path, terminal_status)
    _require_unchanged(state_path, events_path, original, "terminal_contract")

    if stop:
        return result

    try:
        progressed = phase122_function(result, workflow, state_path, events_path)
    except Phase122Error as error:
        _compensate_dependency_error(state_path, events_path, original, error)
    except Exception:
        _compensate_dependency_error(state_path, events_path, original, None)

    if _changed(state_path, events_path, original):
        _restore_or_fail(state_path, events_path, original)
        _fail("progression_contract")
    _check_progression(progressed, result, workflow)
    return progressed


def _check_inputs(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    dependency: object,
) -> None:
    if type(result) not in {PersistedExecutionOutcome, WorkflowProgressionDecision}:
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
    if (
        type(workflow.id) is not str
        or not workflow.id
        or type(workflow.name) is not str
        or not workflow.name
        or type(workflow.description) is not str
        or not workflow.description
        or type(workflow.steps) is not list
        or not workflow.steps
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


def _valid_common_identity(value: object, workflow: WorkflowDefinition) -> bool:
    index = value.current_step_index
    return (
        _nonempty_string(value.workflow_id)
        and _nonempty_string(value.current_step_id)
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
        and _nonempty_string(value.current_employee_id)
        and value.workflow_id == workflow.id
        and value.current_step_id == workflow.steps[index - 1].id
        and value.current_employee_id == workflow.steps[index - 1].employee
    )


def _check_success(
    result: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    if (
        not _exact_string(result.outcome, "persisted_success")
        or result.failure_category is not None
        or not _valid_common_identity(result, workflow)
    ):
        _fail("success_contract")


def _check_failure(
    result: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    if (
        not _exact_string(result.outcome, "persisted_failure")
        or not _valid_common_identity(result, workflow)
        or type(result.failure_category) is not str
        or result.failure_category not in _FAILURE_CATEGORIES
    ):
        _fail("failure_contract")


def _check_completion(
    result: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if (
        not _exact_string(result.decision, "workflow_complete")
        or not _valid_common_identity(result, workflow)
        or result.current_step_index != len(workflow.steps)
        or result.current_step_id != final.id
        or result.current_employee_id != final.employee
        or result.next_step_id is not None
        or result.next_step_index is not None
        or result.next_employee_id is not None
        or not _exact_string(result.reason, "last_step_succeeded")
    ):
        _fail("completion_contract")


def _check_targets(state_path: Path, events_path: Path) -> None:
    try:
        if not state_path.is_file():
            _fail("state_target")
    except OSError:
        _fail("state_target")
    try:
        if not events_path.is_file():
            _fail("event_target")
    except OSError:
        _fail("event_target")


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


def _check_terminal(
    result: PersistedExecutionOutcome | WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    expected_status: Literal["succeeded", "failed"],
) -> None:
    allow_empty_success_output = False
    allow_empty_predecessor_output: bool | None = None
    phase155_compatible = (
        type(result) is PersistedExecutionOutcome
        and type(result.current_step_index) is int
        and result.current_step_index >= 6
    )
    try:
        state, history = load_strict_terminal_history(workflow, state_path, events_path)
    except Exception:
        if not (
            type(result) is PersistedExecutionOutcome
            and result.outcome == "persisted_success"
        ) and not phase155_compatible:
            _fail("terminal_contract")
        try:
            loaded = load_workflow_execution_history(
                WorkflowExecutionPersistenceTargets(state_path, events_path)
            )
        except (OSError, WorkflowExecutionLoadError):
            _fail("terminal_contract")
        state, history = loaded.state, loaded.events
        if not (
            history
            and type(history[-1].output_text) is str
            and history[-1].output_text == ""
        ):
            if not phase155_compatible:
                _fail("terminal_contract")
            allow_empty_predecessor_output = True
        else:
            allow_empty_success_output = True
    if (
        not allow_empty_success_output
        and type(result) is PersistedExecutionOutcome
        and result.outcome == "persisted_success"
        and type(state) is WorkflowExecutionState
        and state.status == "succeeded"
        and type(state.current_step_index) is int
        and 1 <= state.current_step_index < len(workflow.steps)
    ):
        allow_empty_success_output = True
    expected_failure = (
        result.failure_category if type(result) is PersistedExecutionOutcome else None
    )
    if not _valid_terminal_history(
        state,
        history,
        workflow,
        result,
        expected_status,
        expected_failure,
        require_openai_provider=(
            type(result) is PersistedExecutionOutcome
            and result.outcome == "persisted_success"
        ),
        allow_empty_success_output=allow_empty_success_output,
        allow_empty_predecessor_output=allow_empty_predecessor_output,
    ):
        _fail("terminal_contract")


def _valid_terminal_history(
    state: object,
    history: object,
    workflow: WorkflowDefinition,
    result: PersistedExecutionOutcome | WorkflowProgressionDecision,
    expected_status: Literal["succeeded", "failed"],
    expected_failure: object,
    require_openai_provider: bool,
    allow_empty_success_output: bool,
    allow_empty_predecessor_output: bool | None = None,
) -> bool:
    if type(state) is not WorkflowExecutionState or type(history) is not tuple or not history:
        return False
    if not _valid_state_shape(state):
        return False
    index = state.current_step_index
    if type(index) is not int or not 1 <= index <= len(workflow.steps):
        return False
    current = workflow.steps[index - 1]
    if (
        type(state.current_step_id) is not str
        or state.current_step_id != current.id
        or type(state.current_employee_id) is not str
        or state.current_employee_id != current.employee
    ):
        return False
    if (
        state.status != expected_status
        or state.workflow_id != result.workflow_id
        or state.current_step_id != result.current_step_id
        or state.current_step_index != result.current_step_index
        or state.current_employee_id != result.current_employee_id
        or state.last_failure_category != expected_failure
    ):
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
    if allow_empty_predecessor_output is None:
        allow_empty_predecessor_output = (
            allow_empty_success_output
            and state.status == "succeeded"
            and state.current_step_index < len(workflow.steps)
        )
    for position, (event, step) in enumerate(zip(history[:-1], prior_steps, strict=True), 1):
        if not _valid_event_shape(event) or not _valid_predecessor(
            event,
            step,
            position,
            state,
            allow_empty_output=allow_empty_predecessor_output,
        ):
            return False
    return _valid_event_shape(history[-1]) and _valid_terminal_event(
        history[-1],
        state,
        expected_failure,
        require_openai_provider,
        allow_empty_success_output,
    )


def _valid_state_shape(state: WorkflowExecutionState) -> bool:
    return (
        _nonempty_string(state.workflow_id)
        and _exact_string(state.status, state.status)
        and state.status in {"succeeded", "failed"}
        and _nonempty_string(state.current_step_id)
        and type(state.current_step_index) is int
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
        _exact_string(event.event_type, event.event_type)
        and event.event_type in {"step_succeeded", "step_failed"}
        and _nonempty_string(event.workflow_id)
        and _nonempty_string(event.step_id)
        and type(event.step_index) is int
        and _nonempty_string(event.employee_id)
        and _exact_string(event.previous_status, event.previous_status)
        and event.previous_status in {"running", "ready", "succeeded", "failed"}
        and _exact_string(event.next_status, event.next_status)
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
    *,
    allow_empty_output: bool,
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
        and type(event.output_text) is str
        and (allow_empty_output or bool(event.output_text))
        and event.message is None
    )


def _valid_terminal_event(
    event: RuntimeStepEvent,
    state: WorkflowExecutionState,
    expected_failure: object,
    require_openai_provider: bool,
    allow_empty_success_output: bool,
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
            and (not require_openai_provider or event.provider == "openai")
            and expected_failure is None
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and type(event.output_text) is str
            and (allow_empty_success_output or bool(event.output_text))
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


def _check_progression(
    decision: object,
    result: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
) -> None:
    if type(decision) is not WorkflowProgressionDecision or not _valid_common_identity(
        decision, workflow
    ):
        _fail("progression_contract")
    if (
        decision.workflow_id != result.workflow_id
        or decision.current_step_id != result.current_step_id
        or decision.current_step_index != result.current_step_index
        or decision.current_employee_id != result.current_employee_id
        or not _exact_optional_string(decision.next_step_id)
        or not _exact_optional_index(decision.next_step_index)
        or not _exact_optional_string(decision.next_employee_id)
        or not _exact_string(decision.reason, decision.reason)
    ):
        _fail("progression_contract")
    final = result.current_step_index == len(workflow.steps)
    if final:
        valid = (
            _exact_string(decision.decision, "workflow_complete")
            and decision.next_step_id is None
            and decision.next_step_index is None
            and decision.next_employee_id is None
            and _exact_string(decision.reason, "last_step_succeeded")
        )
    else:
        next_step = workflow.steps[result.current_step_index]
        valid = (
            _exact_string(decision.decision, "prepare_next_step")
            and decision.next_step_id == next_step.id
            and decision.next_step_index == result.current_step_index + 1
            and decision.next_employee_id == next_step.employee
            and _exact_string(decision.reason, "next_step_available")
        )
    if not valid:
        _fail("progression_contract")


def _changed(state_path: Path, events_path: Path, original: tuple[bytes, bytes]) -> bool:
    try:
        return (
            state_path.read_bytes() != original[0]
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
    if not failed and _changed(state_path, events_path, original):
        failed = True
    if failed:
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
    safe_error: Phase122Error | None,
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


def _exact_optional_string(value: object) -> bool:
    return value is None or type(value) is str


def _exact_optional_index(value: object) -> bool:
    return value is None or type(value) is int


def _fail(classification: Classification) -> None:
    raise ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationCompatibilityError(
        classification
    ) from None
