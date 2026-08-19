"""Phase 180 post-runtime progression -> prepared-step-start boundary."""

# ruff: noqa: E501,E701,I001

import json
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
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase147Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase139Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationError as Phase137Error,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase155Error,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationError as Phase141Error,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationError as Phase143Error,
)
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationError as Phase144Error,
)
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError as Phase173Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase177CompatibilityError,
)
from ai_office.engine.runtime_result_to_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryError as Phase176Error,
)
from ai_office.engine.runtime_result_to_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPreparedStepStartOrchestrationBoundaryError as Phase175Error,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError as Phase172CompatibilityError,
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase161Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase179CompatibilityError,
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError as Phase179Error,
    route_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import load_workflow_execution_state

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "configuration",
    "phase179_contract",
    "phase146_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
Phase179Function = Callable[
    [
        object,
        object,
        object,
        object,
        object,
        object,
        object,
        object,
        object,
        object,
        object,
        object,
    ],
    PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
Phase146Function = Callable[
    [object, object, object, object, object],
    PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())

# The exact safe public errors that public Phase 179 intentionally exposes /
# re-raises from its Phase-178 and Phase-145 stages.  Phase 180 preserves their
# exact identity and never rolls Phase 179's durable stage back.
_SAFE_PHASE179_ERRORS = (
    Phase179Error,
    Phase179CompatibilityError,
    Phase176Error,
    Phase175Error,
    Phase173Error,
    Phase172Error,
    Phase172CompatibilityError,
    Phase145Error,
    Phase137Error,
    Phase146Error,
    Phase138Error,
    Phase147Error,
    Phase139Error,
    Phase155Error,
    Phase141Error,
    Phase177CompatibilityError,
    Phase161Error,
    Phase143Error,
    Phase144Error,
)


@dataclass(frozen=True)
class RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 180 orchestration failure."""

    classification: Classification


class RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryError(
    ValueError
):
    """Base error for the Phase 180 boundary."""


class RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryError
):
    """Raised when the Phase-179 -> Phase-146 orchestration cannot safely complete."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime persisted running-progression prepared-step-start "
            "orchestration inputs are incompatible"
        )
        self.detail = RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryFailureDetail(
            classification
        )


def route_runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary(
    result: object,
    workflow: object,
    preparation_approval: object,
    employee: object,
    state_path: object,
    events_path: object,
    resolved_tools: object,
    api_key: object,
    execution_approval: object,
    transport: object,
    next_preparation_approval: object,
    next_employee: object,
    *,
    phase179_function: Phase179Function = (
        route_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary
    ),
    phase146_function: Phase146Function = (
        route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    ),
) -> PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Compose public Phase 179 then public Phase 146 exactly once.

    Phase 179 owns the durable runtime-result persistence/classification/
    progression and approved-preparation chain.  Phase 146 is entered only for
    the exact ``PreparedWorkflowStep`` branch, and receives the same
    ``next_employee`` that owned the Phase-179 prepared step.
    ``workflow_complete`` and ``persisted_failure`` outputs return directly with
    Phase 146 zero-call.  Phase 180 never persists the proposed running state,
    never executes the prepared step, and never retries, loops, finalizes,
    schedules, or parallelizes.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase179_function,
        phase146_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    # Operational/approval/employee inputs are deliberately not prevalidated:
    # Phase 179 remains authoritative for its own inputs, and a valid earlier
    # durable Phase-179 stage must not be blocked merely because the later
    # step's approval/employee is absent, stale, or invalid.
    _check_targets(state_path, events_path)
    pre_phase179 = _capture_targets(state_path, events_path)

    try:
        progressed = phase179_function(
            result,
            workflow,
            preparation_approval,
            employee,
            state_path,
            events_path,
            resolved_tools,
            api_key,
            execution_approval,
            transport,
            next_preparation_approval,
            next_employee,
        )
    except _SAFE_PHASE179_ERRORS as error:
        # Exact identity re-raise; Phase 146 zero-call; no pre-Phase179 rollback.
        raise error
    except Exception:
        _fail("dependency_error")

    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        # Original stop input: exact identity + byte-for-byte unchanged.
        if progressed is not result:
            _fail("phase179_contract")
        if _changed(state_path, pre_phase179[0]) or _changed(
            events_path, pre_phase179[1]
        ):
            # Phase 179 owns the stop path; a mutation is a contract violation
            # but never a Phase-179 rollback by Phase 180.
            _fail("phase179_contract")
        return progressed

    if type(progressed) is not PreparedWorkflowStep:
        # Runtime input -> workflow_complete or persisted_failure stop route.
        if not _valid_phase179_stop(
            result, workflow, progressed, state_path, events_path
        ):
            _fail("phase179_contract")
        return progressed

    # Runtime input -> exact PreparedWorkflowStep prepare route.
    if not _valid_phase179_prepared(
        result, workflow, next_employee, progressed, state_path, events_path
    ):
        # Malformed/substitute Phase-179 output or a mismatching durable
        # snapshot; Phase 146 zero-call; no rollback of Phase-179-owned bytes.
        _fail("phase179_contract")

    # Exact post-Phase179 committed target bytes are the continuation snapshot
    # that Phase 146 may validate and that any Phase-146 compensation restores.
    committed = _capture_targets(state_path, events_path)

    try:
        value = phase146_function(
            progressed,
            workflow,
            next_employee,
            state_path,
            events_path,
        )
    except (Phase146Error, Phase138Error) as error:
        # Phase 146 and the safe Phase 138 errors it surfaces are safe public
        # errors: exact identity re-raise after restoring only the post-Phase179
        # committed snapshot (never pre-Phase179 bytes).
        _restore_or_fail(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, committed)
        _fail("dependency_error")

    if not _valid_phase146_shape(progressed, workflow, value):
        # Malformed/substitute Phase-146 return; restore to committed bytes then
        # classify phase146_contract.
        _restore_or_fail(state_path, events_path, committed)
        _fail("phase146_contract")

    if _changed(state_path, committed[0]) or _changed(events_path, committed[1]):
        # Otherwise valid return but the real targets changed: compensate to the
        # post-Phase179 committed bytes, then classify committed_mutation.
        _restore_or_fail(state_path, events_path, committed)
        _fail("committed_mutation")
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase179: object,
    phase146: object,
) -> None:
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
    if not (callable(phase179) and callable(phase146)):
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


def _valid_phase179_stop(
    result: object,
    workflow: WorkflowDefinition,
    progressed: object,
    state_path: Path,
    events_path: Path,
) -> bool:
    """Thin Phase-179 stop-output contract for a runtime input.

    Either exact runtime input type (success or failure) may surface either
    stop output, depending on what Phase 179's one executed step produced: a
    ``workflow_complete`` decision when the executed step advanced past the
    final step, or an exact ``persisted_failure`` when that step failed.  The
    durable terminal must already match whatever the surfaced output claims.
    """
    try:
        if type(result) not in (
            StepRuntimeExecutionSuccess,
            StepRuntimeExecutionFailure,
        ):
            return False
        index = result.step_index
        if type(index) is not int or not 1 <= index + 1 <= len(workflow.steps):
            return False
        executed = index + 1
        executed_step = workflow.steps[index]
        if type(progressed) is WorkflowProgressionDecision:
            decision = progressed
            return (
                decision.decision == "workflow_complete"
                and decision.workflow_id == workflow.id == result.workflow_id
                and decision.current_step_id == executed_step.id
                and decision.current_step_index == executed
                and decision.current_employee_id == executed_step.employee
                and decision.next_step_id is None
                and decision.next_step_index is None
                and decision.next_employee_id is None
                and decision.reason == "last_step_succeeded"
                and _valid_stop_snapshot(
                    workflow, executed, "succeeded", state_path, events_path
                )
            )
        if type(progressed) is PersistedExecutionOutcome:
            outcome = progressed
            if not (
                outcome.outcome == "persisted_failure"
                and outcome.workflow_id == workflow.id == result.workflow_id
                and outcome.current_step_id == executed_step.id
                and outcome.current_step_index == executed
                and outcome.current_employee_id == executed_step.employee
            ):
                return False
            return _valid_stop_snapshot(
                workflow, executed, "failed", state_path, events_path
            )
    except Exception:
        return False
    return False


def _valid_stop_snapshot(
    workflow: WorkflowDefinition,
    executed: int,
    status: Literal["succeeded", "failed"],
    state_path: Path,
    events_path: Path,
) -> bool:
    """Thin proof that the durable snapshot already holds the Phase-179-owned
    terminal for the one step Phase 179 executed."""
    try:
        loaded = load_workflow_execution_state(state_path)
        lines = [
            line
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except Exception:
        return False
    if type(loaded) is not WorkflowExecutionState:
        return False
    step = workflow.steps[executed - 1]
    if not (
        loaded.workflow_id == workflow.id
        and loaded.status == status
        and loaded.current_step_id == step.id
        and loaded.current_step_index == executed
        and loaded.current_employee_id == step.employee
    ):
        return False
    if status == "succeeded":
        if loaded.last_failure_category is not None:
            return False
        if type(loaded.completed_step_ids) is not tuple:
            return False
        if loaded.completed_step_ids != tuple(s.id for s in workflow.steps[:executed]):
            return False
    else:
        if loaded.last_failure_category is None:
            return False
        if type(loaded.completed_step_ids) is not tuple:
            return False
        if loaded.completed_step_ids != tuple(
            s.id for s in workflow.steps[: executed - 1]
        ):
            return False
    if len(lines) != executed:
        return False
    try:
        terminal = RuntimeStepEvent(**json.loads(lines[-1]))
    except Exception:
        return False
    if not (
        terminal.workflow_id == workflow.id
        and terminal.step_id == step.id
        and terminal.step_index == executed
        and terminal.employee_id == step.employee
        and terminal.next_status == status
    ):
        return False
    if status == "succeeded":
        return (
            terminal.event_type == "step_succeeded"
            and terminal.failure_category is None
        )
    return terminal.event_type == "step_failed" and terminal.failure_category is not None


def _valid_phase179_prepared(
    result: object,
    workflow: WorkflowDefinition,
    next_employee: object,
    prepared: PreparedWorkflowStep,
    state_path: Path,
    events_path: Path,
) -> bool:
    """Thin Phase-179 prepared-output contract.

    The exact ``PreparedWorkflowStep`` refers to the one next step after the
    step Phase 179 executed, and must match the supplied workflow, the exact
    ``next_employee``, and the post-Phase179 committed terminal predecessor
    snapshot.
    """
    try:
        if type(result) is not StepRuntimeExecutionSuccess:
            return False
        index = result.step_index
        if type(index) is not int or not 1 <= index + 2 <= len(workflow.steps):
            return False
        executed = index + 1
        next_step = workflow.steps[index + 1]
        if type(next_employee) is not EmployeeDefinition:
            return False
        return (
            prepared.workflow_id == workflow.id == result.workflow_id
            and prepared.step_id == next_step.id
            and type(prepared.step_index) is int
            and prepared.step_index == index + 2
            and prepared.employee_id == next_employee.id
            and prepared.employee_id == next_step.employee
            and prepared.employee_instructions == next_employee.instructions
            and prepared.step_instructions == next_step.instructions
            and prepared.model == next_employee.model
            and type(prepared.allowed_tool_names) is tuple
            and prepared.allowed_tool_names == tuple(next_employee.allowed_tools)
            and _valid_stop_snapshot(
                workflow, executed, "succeeded", state_path, events_path
            )
        )
    except Exception:
        return False


def _valid_phase146_shape(
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    value: object,
) -> bool:
    """Thin Phase-146 output shape contract on the prepared route.

    Requires an exact ``PreparedStepExecutionStart`` whose request and running
    state exactly match the prepared step and the workflow prefix preceding it.
    The durable target equality to the committed snapshot is handled separately
    as committed_mutation.
    """
    if type(value) is not PreparedStepExecutionStart:
        return False
    request, running = value.request, value.running_state
    step_index = prepared.step_index
    if type(step_index) is not int or not 1 <= step_index <= len(workflow.steps):
        return False
    expected_completed = tuple(step.id for step in workflow.steps[: step_index - 1])
    return (
        type(request) is ModelInvocationRequest
        and request.model == prepared.model
        and request.system_instructions == prepared.employee_instructions
        and request.task_instructions == prepared.step_instructions
        and type(request.allowed_tools) is tuple
        and request.allowed_tools == prepared.allowed_tool_names
        and type(running) is WorkflowExecutionState
        and running.workflow_id == prepared.workflow_id
        and running.workflow_id == workflow.id
        and running.status == "running"
        and running.current_step_id == prepared.step_id
        and running.current_step_index == step_index
        and running.current_employee_id == prepared.employee_id
        and type(running.completed_step_ids) is tuple
        and running.completed_step_ids == expected_completed
        and running.last_failure_category is None
    )


def _capture_targets(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _fail("dependency_error")


def _restore_or_fail(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
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
    raise RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
