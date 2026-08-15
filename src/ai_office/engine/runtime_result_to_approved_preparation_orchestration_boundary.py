"""Phase 173 post-runtime → approved-preparation orchestration boundary."""

# ruff: noqa: E501,E701,I001

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "configuration",
    "phase172_contract",
    "phase145_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
Phase172Function = Callable[
    [object, object, object, object],
    WorkflowProgressionDecision | PersistedExecutionOutcome,
]
Phase145Function = Callable[
    [object, object, object, object, object, object],
    PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class RuntimeResultToApprovedPreparationOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 173 orchestration failure."""

    classification: Classification


class RuntimeResultToApprovedPreparationOrchestrationBoundaryError(ValueError):
    """Base error for the Phase 173 boundary."""


class RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError(
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError
):
    """Raised when the Phase-172 → Phase-145 orchestration cannot safely complete."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime approved-preparation orchestration inputs are incompatible"
        )
        self.detail = (
            RuntimeResultToApprovedPreparationOrchestrationBoundaryFailureDetail(
                classification
            )
        )


def route_runtime_result_to_approved_preparation_orchestration_boundary(
    result: object,
    workflow: object,
    approval: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase172_function: Phase172Function = (
        route_runtime_result_to_progression_orchestration_boundary
    ),
    phase145_function: Phase145Function = (
        route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    ),
) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Route one exact Phase-155 result through public Phase 172 then Phase 145 once.

    Phase 172 owns the durable runtime-result persistence/classification/
    progression chain; Phase 145 owns explicit approval/employee validation
    and approved next-step preparation.  Phase 173 must never restore the
    pre-Phase172 running bytes after a Phase-172 stage outcome, because Phase
    172 may already have durably committed the runtime result.  Phase 173
    stops at one exact PreparedWorkflowStep or one exact stop object and must
    not start, persist, or execute the prepared next step.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase172_function,
        phase145_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    # approval/employee are deliberately not prevalidated: a completed runtime
    # result must still be durably persisted/classified/progressed when the
    # next-step approval is absent, stale, or invalid.  Phase 145 validates
    # them after the Phase-172 stage has completed.
    _check_targets(state_path, events_path)

    try:
        progressed = phase172_function(result, workflow, state_path, events_path)
    except Phase172Error as error:
        raise error
    except Exception:
        _fail("dependency_error")

    if not _valid_phase172_result(result, workflow, progressed):
        _fail("phase172_contract")

    # Exact post-Phase172 target bytes are the committed continuation snapshot.
    committed = _capture_targets(state_path, events_path)

    try:
        value = phase145_function(
            progressed, workflow, approval, employee, state_path, events_path
        )
    except Phase145Error as error:
        _restore_if_changed(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, committed)
        _fail("dependency_error")

    if not _valid_phase145_result(progressed, workflow, approval, employee, value):
        _restore_if_changed(state_path, events_path, committed)
        _fail("phase145_contract")

    _require_unchanged(state_path, events_path, committed, "committed_mutation")
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase172: object,
    phase145: object,
) -> None:
    if type(result) not in (
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition:
        _fail("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _fail("state_target")
    if type(events) is not _PATH_TYPE:
        _fail("event_target")
    if state == events:
        _fail("target_conflict")
    if not (callable(phase172) and callable(phase145)):
        _fail("configuration")


def _check_targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _fail("state_target")
    except OSError:
        _fail("state_target")
    try:
        if not events.is_file():
            _fail("event_target")
    except OSError:
        _fail("event_target")


def _valid_phase172_result(
    result: object,
    workflow: WorkflowDefinition,
    progressed: object,
) -> bool:
    """Thin Phase-172 output contract for one exact Phase-155 input.

    Stop inputs must return the exact supplied stop object by identity; a
    runtime success must yield an exact WorkflowProgressionDecision whose
    decision/identity/next fields match the runtime result and supplied
    workflow; a runtime failure must yield an exact persisted_failure outcome
    whose identity and failure category match the runtime result.
    """
    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return progressed is result
    if type(result) is StepRuntimeExecutionSuccess:
        if type(progressed) is not WorkflowProgressionDecision:
            return False
        decision = progressed
        index = result.step_index
        if type(index) is not int or not 1 <= index <= len(workflow.steps):
            return False
        current = workflow.steps[index - 1]
        if not (
            decision.workflow_id == result.workflow_id
            and decision.workflow_id == workflow.id
            and decision.current_step_id == result.step_id
            and decision.current_step_id == current.id
            and decision.current_step_index == index
            and decision.current_employee_id == result.employee_id
            and decision.current_employee_id == current.employee
        ):
            return False
        if index == len(workflow.steps):
            return (
                decision.decision == "workflow_complete"
                and decision.next_step_id is None
                and decision.next_step_index is None
                and decision.next_employee_id is None
                and decision.reason == "last_step_succeeded"
            )
        next_step = workflow.steps[index]
        return (
            decision.decision == "prepare_next_step"
            and decision.next_step_id == next_step.id
            and decision.next_step_index == index + 1
            and decision.next_employee_id == next_step.employee
            and decision.reason == "next_step_available"
        )
    if type(progressed) is not PersistedExecutionOutcome:
        return False
    index = result.step_index
    if type(index) is not int or not 1 <= index <= len(workflow.steps):
        return False
    current = workflow.steps[index - 1]
    return (
        progressed.outcome == "persisted_failure"
        and progressed.workflow_id == result.workflow_id
        and progressed.workflow_id == workflow.id
        and progressed.current_step_id == result.step_id
        and progressed.current_step_id == current.id
        and progressed.current_step_index == index
        and progressed.current_employee_id == result.employee_id
        and progressed.current_employee_id == current.employee
        and progressed.failure_category == result.invocation_result.category
    )


def _valid_phase145_result(
    progressed: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    approval: object,
    employee: object,
    value: object,
) -> bool:
    """Thin Phase-145 output contract branched on the validated Phase-172 result.

    prepare_next_step requires an exact PreparedWorkflowStep matching the
    supplied workflow, Phase-172 decision, approval, and employee;
    workflow_complete / persisted_failure require the exact Phase-172 object
    by identity.  Phase 145's terminal-history validation is not duplicated.
    """
    if type(progressed) is WorkflowProgressionDecision:
        if progressed.decision == "workflow_complete":
            return value is progressed
        if progressed.decision != "prepare_next_step":
            return False
        if (
            type(value) is not PreparedWorkflowStep
            or type(approval) is not NextStepPreparationApproval
            or type(employee) is not EmployeeDefinition
        ):
            return False
        next_index = progressed.next_step_index
        if type(next_index) is not int or not 1 <= next_index <= len(workflow.steps):
            return False
        step = workflow.steps[next_index - 1]
        return (
            value.workflow_id == workflow.id
            and value.workflow_id == progressed.workflow_id
            and value.step_id == step.id
            and value.step_id == progressed.next_step_id
            and type(value.step_index) is int
            and value.step_index == next_index
            and value.employee_id == employee.id
            and value.employee_id == progressed.next_employee_id
            and value.employee_instructions == employee.instructions
            and value.step_instructions == step.instructions
            and value.model == employee.model
            and type(value.allowed_tool_names) is tuple
            and value.allowed_tool_names == tuple(employee.allowed_tools)
        )
    return (
        type(progressed) is PersistedExecutionOutcome
        and progressed.outcome == "persisted_failure"
        and value is progressed
    )


def _capture_targets(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _fail("dependency_error")


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _fail(classification)


def _restore_if_changed(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    if not (_changed(state, original[0]) or _changed(events, original[1])):
        return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed or _changed(state, original[0]) or _changed(events, original[1]):
        _fail("rollback_failure")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _fail(classification: Classification) -> None:
    raise RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
