"""Phase 184 post-runtime → persisted running progression → approved next-step preparation boundary."""

# ruff: noqa: E501,E701,I001

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationError as Phase144Error,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
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
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase147Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase139Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase146Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationError as Phase138Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationError as Phase137Error,
)
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError as Phase173Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase177CompatibilityError,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase183Error,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_orchestration_boundary as route_phase183,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase182Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase181Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError as Phase180Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase179Error,
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
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase161Error,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import load_workflow_execution_state
from typing import get_args

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "configuration",
    "phase183_contract",
    "phase145_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
Phase183Function = Callable[
    [object, object, object, object, object, object, object, object, object, object,
     object, object, object, object, object, object],
    WorkflowProgressionDecision | PersistedExecutionOutcome,
]
Phase145Function = Callable[
    [object, object, object, object, object, object],
    PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))

# The exact safe public errors that public Phase 183 intentionally re-raises
# from its Phase-177 and Phase-172 stages.  Phase 184 preserves their identity.
_SAFE_PHASE183_ERRORS = (
    Phase183Error,
    Phase182Error,
    Phase181Error,
    Phase180Error,
    Phase179Error,
    Phase176Error,
    Phase175Error,
    Phase173Error,
    Phase172Error,
    Phase145Error,
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
    Phase172CompatibilityError,
)


@dataclass(frozen=True)
class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 184 orchestration failure."""

    classification: Classification


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError(
    ValueError
):
    """Base error for the Phase 184 boundary."""


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError
):
    """Raised when the Phase-183 → Phase-145 orchestration cannot safely complete."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime persisted running-progression approved-preparation "
            "orchestration inputs are incompatible"
        )
        self.detail = (
            RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryFailureDetail(
                classification
            )
        )


def route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_approved_preparation_orchestration_boundary(
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
    phase183_function: Phase183Function = route_phase183,
    phase145_function: Phase145Function = (
        route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    ),
) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Compose public Phase 183 then public Phase 145 exactly once each on the prepare route.

    Phase 183 owns the Phase-177 normalize/prepare/start/persist/execute stage
    and the Phase-172 persist/classify/progression stage; Phase 184 never
    writes targets itself, never rolls back across the Phase-183 durable
    ownership point, and never retries, loops, finalizes, schedules,
    parallelizes, or adds CLI/GUI behavior.

    Phase 145 is invoked **only** when Phase 183 returns an exact
    ``prepare_next_step`` decision, and receives the separate
    ``following_preparation_approval`` / ``following_employee`` pair for the newly
    requested step.  When Phase 183 returns ``workflow_complete`` or
    ``persisted_failure``, Phase 184 returns the exact stop object by identity
    with Phase 145 zero calls.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase183_function,
        phase145_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    # The Phase-183 preparation/execution inputs and the later Phase-145
    # step approval/employee pair are deliberately not prevalidated.  Phase
    # 178 remains authoritative for its own inputs, and a valid Phase-183
    # durable result must not be blocked merely because the later step's
    # approval/employee is absent, stale, or invalid.
    _check_targets(state_path, events_path)
    pre_phase183 = _capture_targets(state_path, events_path)

    try:
        progressed = phase183_function(
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
        )
    except _SAFE_PHASE183_ERRORS as error:
        # Exact identity re-raise; Phase 145 zero calls; no outer rollback.
        raise error
    except Exception:
        _fail("dependency_error")

    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        # Original stop input: exact identity + byte-for-byte unchanged.
        if progressed is not result:
            _fail("phase183_contract")
        _require_unchanged(
            state_path, events_path, pre_phase183, "phase183_contract"
        )
        return progressed

    if type(progressed) not in (
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _fail("phase183_contract")
    if not _valid_phase183_runtime_result(
        result, workflow, progressed
    ) or not _valid_phase183_runtime_snapshot(
        result, workflow, progressed, state_path, events_path
    ):
        # Malformed Phase-183 output; Phase 145 zero calls; no pre-Phase183
        # rollback of Phase-183-owned durable bytes.
        _fail("phase183_contract")

    # Exact post-Phase183 committed target bytes are the continuation snapshot
    # that Phase 145 may validate and that any Phase-145 compensation restores.
    committed = _capture_targets(state_path, events_path)

    if type(progressed) is PersistedExecutionOutcome:
        return progressed
    if progressed.decision != "prepare_next_step":
        return progressed  # workflow_complete: exact stop return, Phase 145 zero-call.

    try:
        value = phase145_function(
            progressed,
            workflow,
            following_preparation_approval,
            following_employee,
            state_path,
            events_path,
        )
    except (Phase145Error, Phase137Error) as error:
        # Phase 145 and the safe Phase 137 errors it surfaces are both safe
        # public errors: exact identity re-raise after restoring only the
        # post-Phase183 committed snapshot (never pre-Phase183 bytes).
        _restore_if_changed(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, committed)
        _fail("dependency_error")

    if not _valid_phase145_result(
        progressed, workflow, following_preparation_approval, following_employee, value
    ):
        _restore_if_changed(state_path, events_path, committed)
        _fail("phase145_contract")

    _require_unchanged(state_path, events_path, committed, "committed_mutation")
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase183: object,
    phase145: object,
) -> None:
    # The original input domain is narrowed before Phase 183: only an exact
    # workflow_complete decision and an exact persisted_failure outcome are
    # stop inputs; a prepare_next_step decision or a persisted_success outcome
    # is rejected here as result_type.
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
    if not (callable(phase183) and callable(phase145)):
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


def _valid_phase183_runtime_result(
    result: object,
    workflow: WorkflowDefinition,
    progressed: WorkflowProgressionDecision | PersistedExecutionOutcome,
) -> bool:
    """Thinly verify Phase 183's output linkage without reimplementing it."""
    if type(result) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        return False
    original_index = result.step_index
    current_index = progressed.current_step_index
    if (
        type(original_index) is not int
        or type(current_index) is not int
        or current_index not in (original_index + 1, original_index + 2)
        or not 1 <= original_index < current_index <= len(workflow.steps)
        or progressed.workflow_id != workflow.id
        or progressed.workflow_id != result.workflow_id
    ):
        return False
    current = workflow.steps[current_index - 1]
    if (
        progressed.current_step_id != current.id
        or progressed.current_employee_id != current.employee
    ):
        return False
    if type(progressed) is WorkflowProgressionDecision:
        if progressed.decision == "prepare_next_step":
            if type(result) is not StepRuntimeExecutionSuccess or current_index != original_index + 2:
                return False
            next_index = current_index + 1
            if next_index > len(workflow.steps):
                return False
            following = workflow.steps[next_index - 1]
            return (
                progressed.next_step_id == following.id
                and progressed.next_step_index == next_index
                and progressed.next_employee_id == following.employee
                and progressed.reason == "next_step_available"
            )
        if progressed.decision == "workflow_complete":
            return (
                type(result) is StepRuntimeExecutionSuccess
                and current_index == len(workflow.steps)
                and progressed.next_step_id is None
                and progressed.next_step_index is None
                and progressed.next_employee_id is None
                and progressed.reason == "last_step_succeeded"
            )
        return False
    return (
        type(progressed) is PersistedExecutionOutcome
        and progressed.outcome == "persisted_failure"
        and type(progressed.failure_category) is str
        and progressed.failure_category in _FAILURE_CATEGORIES
    )


def _valid_phase183_runtime_snapshot(
    result: object,
    workflow: WorkflowDefinition,
    progressed: WorkflowProgressionDecision | PersistedExecutionOutcome,
    state_path: Path,
    events_path: Path,
) -> bool:
    """Check only Phase 183's terminal snapshot shape; provenance stays below."""
    try:
        loaded = load_workflow_execution_state(state_path)
        lines = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        index = progressed.current_step_index
        if type(loaded) is not WorkflowExecutionState or type(index) is not int:
            return False
        if len(lines) != index or not 1 <= index <= len(workflow.steps):
            return False
        step = workflow.steps[index - 1]
        failed = type(progressed) is PersistedExecutionOutcome
        expected_status = "failed" if failed else "succeeded"
        expected_completed = tuple(item.id for item in workflow.steps[: index - 1]) if failed else tuple(item.id for item in workflow.steps[:index])
        if (
            loaded.workflow_id != workflow.id
            or loaded.status != expected_status
            or loaded.current_step_id != step.id
            or loaded.current_step_index != index
            or loaded.current_employee_id != step.employee
            or loaded.completed_step_ids != expected_completed
            or loaded.last_failure_category != (progressed.failure_category if failed else None)
        ):
            return False
        for position, line in enumerate(lines, start=1):
            event = RuntimeStepEvent(**json.loads(line))
            if type(event) is not RuntimeStepEvent or event.step_index != position:
                return False
        terminal = RuntimeStepEvent(**json.loads(lines[-1]))
        base = (
            terminal.workflow_id == workflow.id
            and terminal.step_id == step.id
            and terminal.step_index == index
            and terminal.employee_id == step.employee
            and terminal.previous_status == "running"
            and terminal.provider == "openai"
            and (terminal.request_id is None or (type(terminal.request_id) is str and terminal.request_id != ""))
        )
        if failed:
            return base and terminal.event_type == "step_failed" and terminal.next_status == "failed" and terminal.failure_category == progressed.failure_category and terminal.response_id is None and terminal.output_text is None and type(terminal.message) is str and terminal.message != ""
        return base and terminal.event_type == "step_succeeded" and terminal.next_status == "succeeded" and terminal.failure_category is None and type(terminal.response_id) is str and terminal.response_id != "" and type(terminal.output_text) is str and terminal.message is None
    except Exception:
        return False


def _valid_phase145_result(
    progressed: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    approval: object,
    employee: object,
    value: object,
) -> bool:
    """Thin Phase-145 output contract on the exact prepare route only.

    Requires an exact PreparedWorkflowStep matching the workflow, the
    Phase-183 decision, and the supplied next approval/employee.  Phase 145's
    full terminal-history / accumulated-None validation is not duplicated.
    """
    if (
        type(progressed) is not WorkflowProgressionDecision
        or progressed.decision != "prepare_next_step"
    ):
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
    raise RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
