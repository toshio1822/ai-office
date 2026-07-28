"""Bridge exact Phase 52 results to the Phase 32 approved-preparation boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.approved_next_step_reentry import (
    ApprovedNextStepReentryError,
    prepare_approved_next_step_reentry,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory

Classification = Literal[
    "result_type",
    "workflow_definition",
    "decision_contract",
    "completion_contract",
    "failure_contract",
    "approval_contract",
    "employee_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "preparation_contract",
    "dependency_error",
    "dependency_rollback",
]
_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_MESSAGE = "approved next-step preparation bridge inputs are incompatible"
PreparationFunction = Callable[..., PreparedWorkflowStep]


@dataclass(frozen=True)
class ApprovedNextStepPreparationBridgeFailureDetail:
    classification: Classification


class ApprovedNextStepPreparationBridgeError(ValueError):
    pass


class ApprovedNextStepPreparationBridgeCompatibilityError(
    ApprovedNextStepPreparationBridgeError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(_MESSAGE)
        self.detail = ApprovedNextStepPreparationBridgeFailureDetail(classification)


def route_approved_next_step_preparation_bridge_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    approval: object | None,
    employee: object | None,
    *,
    preparation_function: PreparationFunction = prepare_approved_next_step_reentry,
) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
    if type(result) not in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(preparation_function):
        _raise("preparation_contract")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
        original = state_path.read_bytes(), events_path.read_bytes()
        state, _ = load_strict_terminal_history(workflow, state_path, events_path)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    if type(result) is PersistedExecutionOutcome:
        if (
            approval is not None
            or employee is not None
            or not (
                result.outcome == "persisted_failure"
                and result.failure_category in _CATEGORIES
            )
        ):
            _raise("failure_contract")
        if (
            state.status != "failed"
            or state.last_failure_category != result.failure_category
        ):
            _raise("terminal_contract")
        return result
    final = workflow.steps[-1]
    if result.decision == "workflow_complete":
        if (
            approval is not None
            or employee is not None
            or not (
                result.workflow_id == workflow.id
                and result.current_step_id == final.id
                and result.current_step_index == len(workflow.steps)
                and result.current_employee_id == final.employee
                and result.next_step_id
                is result.next_step_index
                is result.next_employee_id
                is None
                and result.reason == "last_step_succeeded"
            )
        ):
            _raise("completion_contract")
        if state.status != "succeeded":
            _raise("terminal_contract")
        return result
    if not (
        result.decision == "prepare_next_step"
        and type(approval) is NextStepPreparationApproval
        and type(employee) is EmployeeDefinition
    ):
        _raise("approval_contract")
    if not (
        1 <= result.current_step_index < len(workflow.steps)
        and result.workflow_id == workflow.id
    ):
        _raise("decision_contract")
    next_step = workflow.steps[result.current_step_index]
    if not (
        result.next_step_id == next_step.id
        and result.next_step_index == result.current_step_index + 1
        and result.next_employee_id == next_step.employee
        and result.reason == "next_step_available"
        and employee.id == next_step.employee
    ):
        _raise("employee_contract")
    if state.status != "succeeded":
        _raise("terminal_contract")
    try:
        value = preparation_function(
            workflow, state_path, events_path, result, approval, employee
        )
    except ApprovedNextStepReentryError:
        _restore(state_path, events_path, original)
        raise
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    if _changed(state_path, original[0]) or _changed(events_path, original[1]):
        _restore(state_path, events_path, original)
        _raise("preparation_contract")
    if type(value) is not PreparedWorkflowStep:
        _raise("preparation_contract")
    return value


def _changed(path: Path, value: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != value
    except OSError:
        return True


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    failed = False
    for path, value in ((state, original[0]), (events, original[1])):
        try:
            if _changed(path, value):
                path.write_bytes(value)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _raise(value: Classification) -> None:
    raise ApprovedNextStepPreparationBridgeCompatibilityError(value) from None
