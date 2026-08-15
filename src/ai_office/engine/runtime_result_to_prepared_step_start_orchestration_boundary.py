"""Phase 175 post-runtime → prepared-step-start orchestration boundary."""

# ruff: noqa: E501,E701,I001

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase146Error,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationError as Phase138Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
)
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError as Phase173Error,
    route_runtime_result_to_approved_preparation_orchestration_boundary,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "configuration",
    "phase173_contract",
    "phase146_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
Phase173Function = Callable[
    [object, object, object, object, object, object],
    PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
Phase146Function = Callable[
    [object, object, object, object, object],
    PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class RuntimeResultToPreparedStepStartOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 175 orchestration failure."""

    classification: Classification


class RuntimeResultToPreparedStepStartOrchestrationBoundaryError(ValueError):
    """Base error for the Phase 175 boundary."""


class RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPreparedStepStartOrchestrationBoundaryError
):
    """Raised when the Phase-173 → Phase-146 orchestration cannot safely complete."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime prepared-step-start orchestration inputs are incompatible"
        )
        self.detail = (
            RuntimeResultToPreparedStepStartOrchestrationBoundaryFailureDetail(
                classification
            )
        )


def route_runtime_result_to_prepared_step_start_orchestration_boundary(
    result: object,
    workflow: object,
    approval: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase173_function: Phase173Function = (
        route_runtime_result_to_approved_preparation_orchestration_boundary
    ),
    phase146_function: Phase146Function = (
        route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    ),
) -> PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Route one exact Phase-155 result through public Phase 173 then Phase 146 once.

    Phase 173 owns the durable runtime-result persistence/classification/
    progression chain plus explicit approval/employee validation and approved
    next-step preparation; Phase 146 owns the prepared-step-start
    terminal-history validation and the full public prepared-step-start chain.
    Phase 175 must never restore the pre-Phase173 running bytes after a
    Phase-173 stage outcome, because Phase 173 may already have durably
    committed the runtime result.  Phase 175 stops at one exact
    PreparedStepExecutionStart or one exact stop object and must not persist,
    start, or execute the prepared running state.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase173_function,
        phase146_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    # approval/employee are deliberately not prevalidated: a completed runtime
    # result must still be durably persisted/classified/progressed when the
    # next-step approval is absent, stale, or invalid.  Phase 173 validates
    # them after the Phase-173 stage has completed.
    _check_targets(state_path, events_path)

    try:
        progressed = phase173_function(
            result, workflow, approval, employee, state_path, events_path
        )
    except (Phase173Error, Phase172Error, Phase145Error) as error:
        raise error
    except Exception:
        _fail("dependency_error")

    if not _valid_phase173_result(result, workflow, employee, progressed):
        _fail("phase173_contract")

    # Exact post-Phase173 target bytes are the committed continuation snapshot.
    committed = _capture_targets(state_path, events_path)

    try:
        value = phase146_function(
            progressed, workflow, employee, state_path, events_path
        )
    except (Phase146Error, Phase138Error) as error:
        _restore_if_changed(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, committed)
        _fail("dependency_error")

    if not _valid_phase146_result(progressed, workflow, value):
        _restore_if_changed(state_path, events_path, committed)
        _fail("phase146_contract")

    _require_unchanged(state_path, events_path, committed, "committed_mutation")
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase173: object,
    phase146: object,
) -> None:
    # The stop input domain is narrowed to the discriminator before Phase
    # 173: only an exact workflow_complete decision and an exact
    # persisted_failure outcome may reach the Phase-173 stage.  A
    # prepare_next_step decision or persisted_success outcome is not a stop
    # input and is rejected here as result_type.
    if type(result) is WorkflowProgressionDecision:
        if result.decision != "workflow_complete":
            _fail("result_type")
    elif type(result) is PersistedExecutionOutcome:
        if result.outcome != "persisted_failure":
            _fail("result_type")
    elif type(result) not in (
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
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
    if not (callable(phase173) and callable(phase146)):
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


def _valid_phase173_result(
    result: object,
    workflow: WorkflowDefinition,
    employee: object,
    progressed: object,
) -> bool:
    """Thin Phase-173 output contract for one exact Phase-155 input.

    Stop inputs must return the exact supplied stop object by identity; a
    runtime success at a non-final step must yield an exact
    PreparedWorkflowStep whose workflow/next-step/employee fields match the
    runtime result, supplied workflow, and employee; a runtime success at the
    final step must yield an exact workflow_complete decision; a runtime
    failure must yield an exact persisted_failure outcome whose identity and
    failure category match the runtime result.  Phase 173 / Phase 145's full
    approval and terminal-history validators are not duplicated.
    """
    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return progressed is result
    if type(result) is StepRuntimeExecutionSuccess:
        index = result.step_index
        if type(index) is not int or not 1 <= index <= len(workflow.steps):
            return False
        current = workflow.steps[index - 1]
        if index == len(workflow.steps):
            if type(progressed) is not WorkflowProgressionDecision:
                return False
            decision = progressed
            return (
                decision.decision == "workflow_complete"
                and decision.workflow_id == result.workflow_id
                and decision.workflow_id == workflow.id
                and decision.current_step_id == result.step_id
                and decision.current_step_id == current.id
                and decision.current_step_index == index
                and decision.current_employee_id == result.employee_id
                and decision.current_employee_id == current.employee
                and decision.next_step_id is None
                and decision.next_step_index is None
                and decision.next_employee_id is None
                and decision.reason == "last_step_succeeded"
            )
        if type(progressed) is not PreparedWorkflowStep:
            return False
        value = progressed
        next_index = index + 1
        next_step = workflow.steps[index]
        return (
            value.workflow_id == workflow.id
            and value.workflow_id == result.workflow_id
            and value.step_id == next_step.id
            and type(value.step_index) is int
            and value.step_index == next_index
            and type(employee) is EmployeeDefinition
            and value.employee_id == employee.id
            and value.employee_id == next_step.employee
            and value.employee_instructions == employee.instructions
            and value.step_instructions == next_step.instructions
            and value.model == employee.model
            and type(value.allowed_tool_names) is tuple
            and value.allowed_tool_names == tuple(employee.allowed_tools)
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


def _valid_phase146_result(
    progressed: PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    value: object,
) -> bool:
    """Thin Phase-146 output contract branched on the validated Phase-173 result.

    The prepared route requires an exact PreparedStepExecutionStart whose
    ModelInvocationRequest and running WorkflowExecutionState exactly match
    the prepared step and the workflow prefix preceding it; the stop routes
    require the exact Phase-173 object by identity.  Phase 146's full
    terminal-history validation is not duplicated.
    """
    if type(progressed) is WorkflowProgressionDecision:
        return value is progressed
    if type(progressed) is PersistedExecutionOutcome:
        return value is progressed
    if type(value) is not PreparedStepExecutionStart:
        return False
    request, running = value.request, value.running_state
    step_index = progressed.step_index
    if type(step_index) is not int or not 1 <= step_index <= len(workflow.steps):
        return False
    expected_completed = tuple(step.id for step in workflow.steps[: step_index - 1])
    return (
        type(request) is ModelInvocationRequest
        and request.model == progressed.model
        and request.system_instructions == progressed.employee_instructions
        and request.task_instructions == progressed.step_instructions
        and type(request.allowed_tools) is tuple
        and request.allowed_tools == progressed.allowed_tool_names
        and type(running) is WorkflowExecutionState
        and running.workflow_id == progressed.workflow_id
        and running.workflow_id == workflow.id
        and running.status == "running"
        and running.current_step_id == progressed.step_id
        and type(running.current_step_index) is int
        and running.current_step_index == step_index
        and running.current_employee_id == progressed.employee_id
        and type(running.completed_step_ids) is tuple
        and running.completed_step_ids == expected_completed
        and running.last_failure_category is None
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
    raise RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
