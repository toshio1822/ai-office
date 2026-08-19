"""Phase 179 post-runtime → persisted running progression → approved next-step preparation boundary."""

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
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError as Phase173Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase177CompatibilityError,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase178CompatibilityError,
    route_runtime_result_to_persisted_running_execution_progression_orchestration_boundary,
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
    "phase178_contract",
    "phase145_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
Phase178Function = Callable[
    [object, object, object, object, object, object, object, object, object, object],
    WorkflowProgressionDecision | PersistedExecutionOutcome,
]
Phase145Function = Callable[
    [object, object, object, object, object, object],
    PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())

# The exact safe public errors that public Phase 178 intentionally re-raises
# from its Phase-177 and Phase-172 stages.  Phase 179 preserves their identity.
_SAFE_PHASE178_ERRORS = (
    Phase178CompatibilityError,
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
class RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 179 orchestration failure."""

    classification: Classification


class RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError(
    ValueError
):
    """Base error for the Phase 179 boundary."""


class RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError
):
    """Raised when the Phase-178 → Phase-145 orchestration cannot safely complete."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime persisted running-progression approved-preparation "
            "orchestration inputs are incompatible"
        )
        self.detail = (
            RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryFailureDetail(
                classification
            )
        )


def route_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary(
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
    phase178_function: Phase178Function = (
        route_runtime_result_to_persisted_running_execution_progression_orchestration_boundary
    ),
    phase145_function: Phase145Function = (
        route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    ),
) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Compose public Phase 178 then public Phase 145 exactly once each on the prepare route.

    Phase 178 owns the Phase-177 normalize/prepare/start/persist/execute stage
    and the Phase-172 persist/classify/progression stage; Phase 179 never
    writes targets itself, never rolls back across the Phase-178 durable
    ownership point, and never retries, loops, finalizes, schedules,
    parallelizes, or adds CLI/GUI behavior.

    Phase 145 is invoked **only** when Phase 178 returns an exact
    ``prepare_next_step`` decision, and receives the separate
    ``next_preparation_approval`` / ``next_employee`` pair for the newly
    requested step.  When Phase 178 returns ``workflow_complete`` or
    ``persisted_failure``, Phase 179 returns the exact stop object by identity
    with Phase 145 zero calls.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase178_function,
        phase145_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    # The Phase-178 preparation/execution inputs and the later Phase-145
    # step approval/employee pair are deliberately not prevalidated.  Phase
    # 178 remains authoritative for its own inputs, and a valid Phase-178
    # durable result must not be blocked merely because the later step's
    # approval/employee is absent, stale, or invalid.
    _check_targets(state_path, events_path)
    pre_phase178 = _capture_targets(state_path, events_path)

    try:
        progressed = phase178_function(
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
        )
    except _SAFE_PHASE178_ERRORS as error:
        # Exact identity re-raise; Phase 145 zero calls; no outer rollback.
        raise error
    except Exception:
        _fail("dependency_error")

    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        # Original stop input: exact identity + byte-for-byte unchanged.
        if progressed is not result:
            _fail("phase178_contract")
        _require_unchanged(
            state_path, events_path, pre_phase178, "phase178_contract"
        )
        return progressed

    if type(progressed) not in (
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _fail("phase178_contract")
    if not _valid_phase178_runtime_result(
        result, workflow, progressed
    ) or not _valid_phase178_runtime_snapshot(
        result, workflow, progressed, state_path, events_path
    ):
        # Malformed Phase-178 output; Phase 145 zero calls; no pre-Phase178
        # rollback of Phase-178-owned durable bytes.
        _fail("phase178_contract")

    # Exact post-Phase178 committed target bytes are the continuation snapshot
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
            next_preparation_approval,
            next_employee,
            state_path,
            events_path,
        )
    except Phase145Error as error:
        _restore_if_changed(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, committed)
        _fail("dependency_error")

    if not _valid_phase145_result(
        progressed, workflow, next_preparation_approval, next_employee, value
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
    phase178: object,
    phase145: object,
) -> None:
    # The original input domain is narrowed before Phase 178: only an exact
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
    if not (callable(phase178) and callable(phase145)):
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


def _valid_phase178_runtime_result(
    result: object,
    workflow: WorkflowDefinition,
    progressed: WorkflowProgressionDecision | PersistedExecutionOutcome,
) -> bool:
    """Thin Phase-178 runtime-output contract.

    For a runtime input, the surfaced output refers to the one next step that
    Phase 178 executed (current = that step).  A success-surfaced progression
    must match the supplied workflow and the one-step advancement; a failure
    must be an exact persisted_failure for that executed step.
    """
    index = result.step_index  # type: ignore[union-attr]
    if type(index) is not int or not 1 <= index + 1 <= len(workflow.steps):
        return False
    executed = workflow.steps[index]  # executed step number = index + 1
    if type(progressed) is WorkflowProgressionDecision:
        decision = progressed
        if not (
            decision.workflow_id == result.workflow_id  # type: ignore[union-attr]
            and decision.workflow_id == workflow.id
            and decision.current_step_id == executed.id
            and type(decision.current_step_index) is int
            and decision.current_step_index == index + 1
            and decision.current_employee_id == executed.employee
        ):
            return False
        if index + 1 == len(workflow.steps):
            return (
                decision.decision == "workflow_complete"
                and decision.next_step_id is None
                and decision.next_step_index is None
                and decision.next_employee_id is None
                and decision.reason == "last_step_succeeded"
            )
        following = workflow.steps[index + 1]
        return (
            decision.decision == "prepare_next_step"
            and decision.next_step_id == following.id
            and type(decision.next_step_index) is int
            and decision.next_step_index == index + 2
            and decision.next_employee_id == following.employee
            and decision.reason == "next_step_available"
        )
    return (
        type(progressed) is PersistedExecutionOutcome
        and progressed.outcome == "persisted_failure"
        and progressed.workflow_id == result.workflow_id  # type: ignore[union-attr]
        and progressed.workflow_id == workflow.id
        and progressed.current_step_id == executed.id
        and type(progressed.current_step_index) is int
        and progressed.current_step_index == index + 1
        and progressed.current_employee_id == executed.employee
    )


def _valid_phase178_runtime_snapshot(
    result: object,
    workflow: WorkflowDefinition,
    progressed: WorkflowProgressionDecision | PersistedExecutionOutcome,
    state_path: Path,
    events_path: Path,
) -> bool:
    """Thin post-Phase178 durable snapshot proof.

    The durable state/events must already contain the exact terminal event for
    the one step Phase 178 executed: a succeeded terminal for a decision
    route, a failed terminal for a persisted_failure route, matching the
    surfaced progression's identity.
    """
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
    index = result.step_index  # type: ignore[union-attr]
    if type(index) is not int or not 1 <= index + 1 <= len(workflow.steps):
        return False
    executed = index + 1
    step = workflow.steps[index]
    if type(progressed) is WorkflowProgressionDecision:
        if not (
            loaded.workflow_id == workflow.id
            and loaded.status == "succeeded"
            and loaded.current_step_id == step.id
            and type(loaded.current_step_index) is int
            and loaded.current_step_index == executed
            and loaded.current_employee_id == step.employee
            and type(loaded.completed_step_ids) is tuple
            and loaded.completed_step_ids
            == tuple(s.id for s in workflow.steps[:executed])
            and loaded.last_failure_category is None
        ):
            return False
        if len(lines) != executed:
            return False
        try:
            terminal = RuntimeStepEvent(**json.loads(lines[-1]))
        except Exception:
            return False
        return (
            terminal.workflow_id == workflow.id
            and terminal.step_id == step.id
            and type(terminal.step_index) is int
            and terminal.step_index == executed
            and terminal.employee_id == step.employee
            and terminal.event_type == "step_succeeded"
            and terminal.next_status == "succeeded"
            and terminal.previous_status == "running"
            and terminal.failure_category is None
        )
    if not (
        loaded.workflow_id == workflow.id
        and loaded.status == "failed"
        and loaded.current_step_id == step.id
        and type(loaded.current_step_index) is int
        and loaded.current_step_index == executed
        and loaded.current_employee_id == step.employee
        and loaded.last_failure_category
        == progressed.failure_category  # type: ignore[union-attr]
    ):
        return False
    if len(lines) != executed:
        return False
    try:
        terminal = RuntimeStepEvent(**json.loads(lines[-1]))
    except Exception:
        return False
    return (
        terminal.workflow_id == workflow.id
        and terminal.step_id == step.id
        and type(terminal.step_index) is int
        and terminal.step_index == executed
        and terminal.employee_id == step.employee
        and terminal.event_type == "step_failed"
        and terminal.next_status == "failed"
        and terminal.failure_category == progressed.failure_category  # type: ignore[union-attr]
    )


def _valid_phase145_result(
    progressed: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    approval: object,
    employee: object,
    value: object,
) -> bool:
    """Thin Phase-145 output contract on the exact prepare route only.

    Requires an exact PreparedWorkflowStep matching the workflow, the
    Phase-178 decision, and the supplied next approval/employee.  Phase 145's
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
    raise RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
