"""Phase 186 post-runtime progression -> prepared-start persistence boundary."""

# ruff: noqa: E501,E701,I001

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase147Error,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase139Error,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase146Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationError as Phase138Error,
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
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase183Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase182Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase178Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase181Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryError as Phase180Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError as Phase179Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase184CompatibilityError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError as Phase184Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError as Phase185Error,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_step_start_orchestration_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory, ModelInvocationRequest
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    load_workflow_execution_state,
    serialize_workflow_execution_state_json,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "configuration",
    "phase185_contract",
    "phase147_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
Phase185Function = Callable[...,
    PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
Phase147Function = Callable[
    [object, object, object, object, object],
    RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))

# Phase 185 deliberately exposes this exact safe public family from its own
# Phase-184 / Phase-146 chain.  Phase 186 preserves identity and does not
# restore bytes owned by that earlier stage.
_SAFE_PHASE185_ERRORS = (
    Phase185Error,
    Phase184Error,
    Phase184CompatibilityError,
    Phase183Error,
    Phase182Error,
    Phase181Error,
    Phase180Error,
    Phase179Error,
    Phase178Error,
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
class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 186 orchestration failure."""

    classification: Classification


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryError(
    ValueError
):
    """Base error for the Phase 186 boundary."""


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryError
):
    """Raised when the Phase-185 -> Phase-147 boundary cannot continue safely."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime persisted running-progression prepared-start persistence "
            "orchestration inputs are incompatible"
        )
        self.detail = RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryFailureDetail(
            classification
        )


def route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary(
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
    next_resolved_tools: object,
    next_api_key: object,
    next_execution_approval: object,
    next_transport: object,
    following_preparation_approval: object,
    following_employee: object,
    *,
    phase185_function: Phase185Function = (
        route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_step_start_orchestration_boundary
    ),
    phase147_function: Phase147Function = (
        route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    ),
) -> RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Compose public Phase 185 then public Phase 147 exactly once.

    Phase 185 owns the prior post-runtime durable progression and produces an
    exact prepared start or an exact stop.  Only the prepared-start branch
    enters Phase 147, with the unchanged ``following_employee``.  Phase 186 stops
    after one durable running-state result and never executes that step.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase185_function,
        phase147_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    # The Phase-185-owned operational, approval, employee, tool, credential,
    # and transport inputs are deliberately not prevalidated here.
    _check_targets(state_path, events_path)
    pre_phase185 = _capture_targets(state_path, events_path)

    try:
        progressed = phase185_function(
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
            next_resolved_tools,
            next_api_key,
            next_execution_approval,
            next_transport,
            following_preparation_approval,
            following_employee,
        )
    except _SAFE_PHASE185_ERRORS as error:
        # Exact identity re-raise; Phase 147 zero-call; no pre-Phase-185
        # rollback even if Phase 185 crossed its durable ownership boundary.
        raise error
    except Exception:
        _fail("dependency_error")

    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        # Original stop input: Phase 185 must pass through the exact object and
        # leave both targets byte-for-byte unchanged.
        if progressed is not result or _changed(state_path, pre_phase185[0]) or _changed(
            events_path, pre_phase185[1]
        ):
            _fail("phase185_contract")
        return progressed

    if type(progressed) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        if not _valid_phase185_stop(
            result, workflow, progressed, state_path, events_path
        ):
            _fail("phase185_contract")
        return progressed

    if type(progressed) is not PreparedStepExecutionStart or not _valid_phase185_start(
        result, workflow, following_employee, progressed, state_path, events_path
    ):
        # Phase 185 owns any valid bytes it may have committed; never roll them
        # back merely because its output contract is malformed here.
        _fail("phase185_contract")

    committed = _capture_targets(state_path, events_path)
    try:
        value = phase147_function(
            progressed,
            workflow,
            following_employee,
            state_path,
            events_path,
        )
    except (Phase147Error, Phase139Error) as error:
        _restore_or_fail(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, committed)
        _fail("dependency_error")

    if not _valid_phase147_shape(value):
        _restore_or_fail(state_path, events_path, committed)
        _fail("phase147_contract")
    if not _valid_phase147_bytes(progressed, value, state_path, events_path, committed):
        _restore_or_fail(state_path, events_path, committed)
        _fail("committed_mutation")
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase185: object,
    phase147: object,
) -> None:
    if type(result) is WorkflowProgressionDecision:
        if result.decision != "workflow_complete":
            _fail("result_type")
    elif type(result) is PersistedExecutionOutcome:
        if result.outcome != "persisted_failure":
            _fail("result_type")
    elif type(result) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition:
        _fail("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _fail("state_target")
    if type(events) is not _PATH_TYPE:
        _fail("event_target")
    if state == events:
        _fail("target_conflict")
    if not (callable(phase185) and callable(phase147)):
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


def _valid_phase185_start(
    result: object,
    workflow: WorkflowDefinition,
    following_employee: object,
    start: PreparedStepExecutionStart,
    state_path: Path,
    events_path: Path,
) -> bool:
    """Thin proof of the exact Phase-185 prepared-start result."""
    try:
        if type(result) is not StepRuntimeExecutionSuccess:
            return False
        index = result.step_index
        if type(index) is not int or not 1 <= index + 2 <= len(workflow.steps):
            return False
        if type(following_employee) is not EmployeeDefinition:
            return False
        executed = index + 2
        next_step = workflow.steps[index + 2]
        request, running = start.request, start.running_state
        return (
            type(request) is ModelInvocationRequest
            and type(running) is WorkflowExecutionState
            and request.model == following_employee.model
            and request.system_instructions == following_employee.instructions
            and request.task_instructions == next_step.instructions
            and type(request.allowed_tools) is tuple
            and request.allowed_tools == tuple(following_employee.allowed_tools)
            and running.workflow_id == workflow.id == result.workflow_id
            and running.status == "running"
            and running.current_step_id == next_step.id
            and type(running.current_step_index) is int
            and running.current_step_index == index + 3
            and running.current_employee_id == following_employee.id
            and running.current_employee_id == next_step.employee
            and type(running.completed_step_ids) is tuple
            and running.completed_step_ids
            == tuple(step.id for step in workflow.steps[: index + 2])
            and running.last_failure_category is None
            and _valid_terminal_snapshot(
                workflow, executed, "succeeded", state_path, events_path
            )
        )
    except Exception:
        return False


def _valid_phase185_stop(
    result: object,
    workflow: WorkflowDefinition,
    value: object,
    state_path: Path,
    events_path: Path,
) -> bool:
    """Thin proof of a Phase-185-owned terminal stop output."""
    try:
        if type(result) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
            return False
        original = result.step_index
        current = value.current_step_index
        if (
            type(original) is not int
            or type(current) is not int
            or current not in (original + 1, original + 2)
            or not 1 <= current <= len(workflow.steps)
        ):
            return False
        step = workflow.steps[current - 1]
        if type(value) is WorkflowProgressionDecision:
            return (
                value.decision == "workflow_complete"
                and type(result) is StepRuntimeExecutionSuccess
                and current == len(workflow.steps)
                and value.workflow_id == workflow.id == result.workflow_id
                and value.current_step_id == step.id
                and value.current_step_index == current
                and value.current_employee_id == step.employee
                and value.next_step_id is None
                and value.next_step_index is None
                and value.next_employee_id is None
                and value.reason == "last_step_succeeded"
                and _valid_terminal_snapshot(
                    workflow, current, "succeeded", state_path, events_path
                )
            )
        return (
            type(value) is PersistedExecutionOutcome
            and value.outcome == "persisted_failure"
            and value.workflow_id == workflow.id == result.workflow_id
            and value.current_step_id == step.id
            and value.current_step_index == current
            and value.current_employee_id == step.employee
            and type(value.failure_category) is str
            and value.failure_category in _FAILURE_CATEGORIES
            and _valid_terminal_snapshot(
                workflow,
                current,
                "failed",
                state_path,
                events_path,
                failure_category=value.failure_category,
            )
        )
    except Exception:
        return False


def _valid_terminal_snapshot(
    workflow: WorkflowDefinition,
    executed: int,
    status: Literal["succeeded", "failed"],
    state_path: Path,
    events_path: Path,
    *,
    failure_category: object = None,
) -> bool:
    try:
        loaded = load_workflow_execution_state(state_path)
        lines = [
            line
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if type(loaded) is not WorkflowExecutionState or len(lines) != executed:
            return False
        step = workflow.steps[executed - 1]
        if not (
            loaded.workflow_id == workflow.id
            and loaded.status == status
            and loaded.current_step_id == step.id
            and loaded.current_step_index == executed
            and loaded.current_employee_id == step.employee
            and type(loaded.completed_step_ids) is tuple
        ):
            return False
        expected_completed = (
            tuple(item.id for item in workflow.steps[:executed])
            if status == "succeeded"
            else tuple(item.id for item in workflow.steps[: executed - 1])
        )
        if loaded.completed_step_ids != expected_completed:
            return False
        if status == "succeeded" and loaded.last_failure_category is not None:
            return False
        if status == "failed" and loaded.last_failure_category != failure_category:
            return False
        terminal = RuntimeStepEvent(**json.loads(lines[-1]))
        if not (
            terminal.workflow_id == workflow.id
            and terminal.step_id == step.id
            and terminal.step_index == executed
            and terminal.employee_id == step.employee
            and terminal.next_status == status
        ):
            return False
        if status == "succeeded":
            return terminal.event_type == "step_succeeded" and terminal.failure_category is None
        return terminal.event_type == "step_failed" and terminal.failure_category == failure_category
    except Exception:
        return False


def _valid_phase147_shape(value: object) -> bool:
    return (
        type(value) is RunningStatePersistenceResult
        and type(value.state_bytes_written) is int
        and value.state_bytes_written > 0
    )


def _valid_phase147_bytes(
    start: PreparedStepExecutionStart,
    value: RunningStatePersistenceResult,
    state_path: Path,
    events_path: Path,
    committed: tuple[bytes, bytes],
) -> bool:
    try:
        state_bytes = state_path.read_bytes()
        event_bytes = events_path.read_bytes()
        loaded = load_workflow_execution_state(state_path)
        expected = serialize_workflow_execution_state_json(start.running_state).encode(
            "utf-8"
        )
        return (
            state_bytes == expected
            and value.state_bytes_written == len(state_bytes)
            and event_bytes == committed[1]
            and type(loaded) is WorkflowExecutionState
            and loaded == start.running_state
            and loaded.status == "running"
            and loaded.current_step_id == start.running_state.current_step_id
            and loaded.current_step_index == start.running_state.current_step_index
            and loaded.current_employee_id == start.running_state.current_employee_id
            and loaded.completed_step_ids == start.running_state.completed_step_ids
            and loaded.last_failure_category is None
        )
    except Exception:
        return False


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
    raise RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
