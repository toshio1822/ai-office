"""One explicit approved-workflow next-step continuation cycle (Phase 190).

This module is deliberately a thin orchestration boundary.  It owns the
boundary between the already persisted terminal decision and the next durable
terminal result, while each phase remains the owner of its own lower-level
contract and persistence semantics.
"""

# ruff: noqa: E501,E701,I001

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationError as Phase144Error,
)
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase155BoundaryError,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationError as Phase141Error,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationError as Phase143Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase147BoundaryError,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase139Error,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase146BoundaryError,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationError as Phase138Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145BoundaryError,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationError as Phase137Error,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError as Phase172CompatibilityError,
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172BoundaryError,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase161ChainError,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase161Error,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory, ModelInvocationRequest
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    is_valid_step_runtime_execution_result,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    load_workflow_execution_state,
    parse_runtime_step_event,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "configuration",
    "phase145_contract",
    "phase146_contract",
    "phase147_contract",
    "phase155_contract",
    "phase172_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]

Phase145Function = Callable[..., object]
Phase146Function = Callable[..., object]
Phase147Function = Callable[..., object]
Phase155Function = Callable[..., object]
Phase172Function = Callable[..., object]

_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class ApprovedWorkflowContinuationCycleFailureDetail:
    """Detail-safe classification for one Phase-190 boundary failure."""

    classification: Classification


class ApprovedWorkflowContinuationCycleError(ValueError):
    """Base error for the Phase-190 continuation-cycle boundary."""


class ApprovedWorkflowContinuationCycleCompatibilityError(
    ApprovedWorkflowContinuationCycleError
):
    """Raised when the supplied cycle cannot safely cross a public seam."""

    def __init__(self, classification: Classification) -> None:
        super().__init__("approved workflow continuation cycle inputs are incompatible")
        self.detail = ApprovedWorkflowContinuationCycleFailureDetail(classification)


# A few callers use the explicit "failure detail" spelling used by older
# boundaries.  They are aliases, not another error family.
ApprovedWorkflowContinuationCycleFailure = ApprovedWorkflowContinuationCycleError

# Each immediate public boundary has an intentionally independent safe-error
# family. An error from another stage must be sanitized as a Phase-190
# dependency error rather than preserved by identity.
_SAFE_PHASE145_ERRORS = (Phase145BoundaryError, Phase137Error)
_SAFE_PHASE146_ERRORS = (Phase146BoundaryError, Phase138Error)
_SAFE_PHASE147_ERRORS = (Phase147BoundaryError, Phase139Error)
_SAFE_PHASE155_ERRORS = (Phase155BoundaryError, Phase141Error)
# This is the Phase-188 precedent for the real Phase-172 public surface. The
# Phase-161 outer-chain error is required because it is the default first seam.
_SAFE_PHASE172_ERRORS = (
    Phase172BoundaryError,
    Phase172CompatibilityError,
    Phase161ChainError,
    Phase161Error,
    Phase143Error,
    Phase144Error,
)


def route_approved_workflow_continuation_cycle(
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
    *,
    phase145_function=route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    phase146_function=route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    phase147_function=route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    phase155_function=route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    phase172_function=route_runtime_result_to_progression_orchestration_boundary,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Execute exactly one approved next workflow step and then stop.

    Terminal decisions are authoritative stop values and are returned before
    operational context is inspected.  A prepare decision crosses the five
    public boundaries once, in order 145 → 146 → 147 → 155 → 172.
    """
    _check_result_and_workflow(result, workflow)
    assert type(workflow) is WorkflowDefinition

    # A terminal value has already been produced by an authoritative
    # progression stage.  It is intentionally an identity-preserving
    # short-circuit: no target, approval, employee, tool, credential,
    # transport, or injected dependency is consulted.
    if type(result) is WorkflowProgressionDecision:
        if result.decision == "workflow_complete":
            _check_terminal_decision(result, workflow)
            return result
        _check_prepare_decision(result, workflow)
    else:
        assert type(result) is PersistedExecutionOutcome
        if result.outcome == "persisted_failure":
            _check_terminal_failure(result, workflow)
            return result
        _fail("result_type")

    _check_prepare_configuration(
        workflow,
        state_path,
        events_path,
        phase145_function,
        phase146_function,
        phase147_function,
        phase155_function,
        phase172_function,
    )
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    original = _capture_targets(state_path, events_path)

    try:
        prepared = phase145_function(
            result,
            workflow,
            preparation_approval,
            employee,
            state_path,
            events_path,
        )
    except _SAFE_PHASE145_ERRORS as error:
        _restore_or_fail(state_path, events_path, original)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, original)
        _fail("dependency_error")
    prepared_valid = _valid_prepared(prepared, result, workflow, employee)
    if _changed(state_path, events_path, original):
        _restore_or_fail(state_path, events_path, original)
        _fail("committed_mutation" if prepared_valid else "phase145_contract")
    if not prepared_valid:
        _fail("phase145_contract")
    assert type(prepared) is PreparedWorkflowStep

    try:
        prepared_start = phase146_function(
            prepared,
            workflow,
            employee,
            state_path,
            events_path,
        )
    except _SAFE_PHASE146_ERRORS as error:
        _restore_or_fail(state_path, events_path, original)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, original)
        _fail("dependency_error")
    prepared_start_valid = _valid_prepared_start(
        prepared_start, prepared, workflow, employee
    )
    if _changed(state_path, events_path, original):
        _restore_or_fail(state_path, events_path, original)
        _fail(
            "committed_mutation"
            if prepared_start_valid
            else "phase146_contract"
        )
    if not prepared_start_valid:
        _fail("phase146_contract")
    assert type(prepared_start) is PreparedStepExecutionStart

    pre_persistence = _capture_targets(state_path, events_path)
    try:
        persisted_running = phase147_function(
            prepared_start,
            workflow,
            employee,
            state_path,
            events_path,
        )
    except _SAFE_PHASE147_ERRORS as error:
        _restore_or_fail(state_path, events_path, original)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, original)
        _fail("dependency_error")
    _check_persisted_running(
        persisted_running,
        prepared_start,
        state_path,
        events_path,
        original,
        pre_persistence,
    )
    assert type(persisted_running) is RunningStatePersistenceResult
    running_snapshot = _capture_targets(state_path, events_path)

    try:
        runtime_result = phase155_function(
            persisted_running,
            prepared_start,
            workflow,
            employee,
            state_path,
            events_path,
            resolved_tools,
            api_key,
            execution_approval,
            transport,
        )
    except _SAFE_PHASE155_ERRORS as error:
        _restore_or_fail(state_path, events_path, running_snapshot)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, running_snapshot)
        _fail("dependency_error")
    runtime_result_valid = _valid_runtime_result(
        runtime_result, prepared_start, workflow
    )
    if _changed(state_path, events_path, running_snapshot):
        _restore_or_fail(state_path, events_path, running_snapshot)
        _fail(
            "committed_mutation" if runtime_result_valid else "phase155_contract"
        )
    if not runtime_result_valid:
        _fail("phase155_contract")
    assert type(runtime_result) in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)

    # Phase 172 owns the next durable terminal commit.  In particular, no
    # outer restoration is permitted from this point onward, even if its
    # result is malformed or the dependency raises.
    try:
        progressed = phase172_function(
            runtime_result,
            workflow,
            state_path,
            events_path,
        )
    except _SAFE_PHASE172_ERRORS as error:
        raise error
    except Exception:
        _fail("dependency_error")
    if not _valid_phase172_result(
        progressed,
        runtime_result,
        workflow,
        state_path,
        events_path,
        running_snapshot,
    ):
        _fail("phase172_contract")
    return progressed


def _check_result_and_workflow(result: object, workflow: object) -> None:
    if type(result) not in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _fail("workflow_definition")


def _check_prepare_configuration(
    workflow: WorkflowDefinition,
    state_path: object,
    events_path: object,
    phase145: object,
    phase146: object,
    phase147: object,
    phase155: object,
    phase172: object,
) -> None:
    del workflow
    if type(state_path) is not _PATH_TYPE:
        _fail("state_target")
    if type(events_path) is not _PATH_TYPE:
        _fail("event_target")
    if state_path == events_path:
        _fail("target_conflict")
    if not (
        callable(phase145)
        and callable(phase146)
        and callable(phase147)
        and callable(phase155)
        and callable(phase172)
    ):
        _fail("configuration")
    _check_regular_file(state_path, "state_target")
    _check_regular_file(events_path, "event_target")


def _check_regular_file(path: Path, classification: Classification) -> None:
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


def _check_terminal_decision(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        _exact(value.decision, "workflow_complete")
        and _exact(value.workflow_id, workflow.id)
        and _exact(value.current_step_id, final.id)
        and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps)
        and _exact(value.current_employee_id, final.employee)
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and _exact(value.reason, "last_step_succeeded")
    ):
        _fail("result_type")


def _check_terminal_failure(
    value: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    index = value.current_step_index
    if not (
        _exact(value.outcome, "persisted_failure")
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
        and _exact(value.workflow_id, workflow.id)
        and _exact(value.current_step_id, workflow.steps[index - 1].id)
        and _exact(value.current_employee_id, workflow.steps[index - 1].employee)
        and type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
    ):
        _fail("result_type")


def _check_prepare_decision(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    current_index = value.current_step_index
    next_index = value.next_step_index
    if not (
        _exact(value.decision, "prepare_next_step")
        and type(current_index) is int
        and 1 <= current_index < len(workflow.steps)
        and type(next_index) is int
        and next_index == current_index + 1
    ):
        _fail("result_type")
    current = workflow.steps[current_index - 1]
    next_step = workflow.steps[next_index - 1]
    if not (
        _exact(value.workflow_id, workflow.id)
        and _exact(value.current_step_id, current.id)
        and _exact(value.current_employee_id, current.employee)
        and _exact(value.next_step_id, next_step.id)
        and _exact(value.next_employee_id, next_step.employee)
        and _exact(value.reason, "next_step_available")
    ):
        _fail("result_type")


def _valid_prepared(
    value: object,
    decision: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object,
) -> bool:
    try:
        _check_prepared(value, decision, workflow, employee)
    except ApprovedWorkflowContinuationCycleCompatibilityError:
        return False
    return True


def _check_prepared(
    value: object,
    decision: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object,
) -> None:
    if type(value) is not PreparedWorkflowStep or type(employee) is not EmployeeDefinition:
        _fail("phase145_contract")
    assert type(value) is PreparedWorkflowStep and type(employee) is EmployeeDefinition
    if not _valid_employee(employee):
        _fail("phase145_contract")
    step = workflow.steps[decision.next_step_index - 1]  # type: ignore[index]
    if not (
        _exact(value.workflow_id, workflow.id)
        and _exact(value.step_id, step.id)
        and type(value.step_index) is int
        and value.step_index == decision.next_step_index
        and _exact(value.employee_id, employee.id)
        and _exact(value.employee_id, step.employee)
        and _exact(value.employee_instructions, employee.instructions)
        and _exact(value.step_instructions, step.instructions)
        and _exact(value.model, employee.model)
        and type(value.allowed_tool_names) is tuple
        and all(_nonempty(item) for item in value.allowed_tool_names)
        and value.allowed_tool_names == tuple(employee.allowed_tools)
    ):
        _fail("phase145_contract")


def _valid_prepared_start(
    value: object,
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    employee: object,
) -> bool:
    try:
        _check_prepared_start(value, prepared, workflow, employee)
    except ApprovedWorkflowContinuationCycleCompatibilityError:
        return False
    return True


def _check_prepared_start(
    value: object,
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    employee: object,
) -> None:
    if (
        type(value) is not PreparedStepExecutionStart
        or type(employee) is not EmployeeDefinition
        or not _valid_employee(employee)
    ):
        _fail("phase146_contract")
    assert type(value) is PreparedStepExecutionStart and type(employee) is EmployeeDefinition
    request = value.request
    running = value.running_state
    if type(request) is not ModelInvocationRequest or type(running) is not WorkflowExecutionState:
        _fail("phase146_contract")
    expected_prefix = tuple(step.id for step in workflow.steps[: prepared.step_index - 1])
    if not (
        _exact(running.workflow_id, workflow.id)
        and _exact(running.status, "running")
        and _exact(running.current_step_id, prepared.step_id)
        and type(running.current_step_index) is int
        and running.current_step_index == prepared.step_index
        and _exact(running.current_employee_id, prepared.employee_id)
        and running.current_employee_id == employee.id
        and type(running.completed_step_ids) is tuple
        and all(_nonempty(item) for item in running.completed_step_ids)
        and running.completed_step_ids == expected_prefix
        and running.last_failure_category is None
        and _exact(request.model, prepared.model)
        and _exact(request.system_instructions, prepared.employee_instructions)
        and _exact(request.task_instructions, prepared.step_instructions)
        and type(request.allowed_tools) is tuple
        and all(_nonempty(item) for item in request.allowed_tools)
        and request.allowed_tools == prepared.allowed_tool_names
    ):
        _fail("phase146_contract")


def _check_persisted_running(
    value: object,
    prepared_start: PreparedStepExecutionStart,
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
    pre_persistence: tuple[bytes, bytes],
) -> None:
    expected = serialize_workflow_execution_state_json(
        prepared_start.running_state
    ).encode("utf-8")
    try:
        state_bytes = state_path.read_bytes()
        event_bytes = events_path.read_bytes()
    except Exception:
        _restore_or_fail(state_path, events_path, original)
        _fail("phase147_contract")

    # The state replacement is the one authorized mutation of this stage.
    # Anything other than that exact state-only write is an unauthorized
    # mutation and must be classified separately from a malformed return.
    if state_bytes != expected or event_bytes != original[1]:
        if (state_bytes, event_bytes) == pre_persistence:
            _fail("phase147_contract")
        _restore_or_fail(state_path, events_path, original)
        _fail("committed_mutation")
    try:
        loaded = load_workflow_execution_state(state_path)
    except Exception:
        _restore_or_fail(state_path, events_path, original)
        _fail("phase147_contract")
    if not (
        type(value) is RunningStatePersistenceResult
        and type(value.state_bytes_written) is int
        and value.state_bytes_written > 0
        and value.state_bytes_written == len(expected)
        and type(loaded) is WorkflowExecutionState
        and loaded == prepared_start.running_state
    ):
        _restore_or_fail(state_path, events_path, original)
        _fail("phase147_contract")


def _valid_runtime_result(
    value: object,
    prepared_start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
) -> bool:
    try:
        _check_runtime_result(value, prepared_start, workflow)
    except ApprovedWorkflowContinuationCycleCompatibilityError:
        return False
    return True


def _check_runtime_result(
    value: object,
    prepared_start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
) -> None:
    if type(value) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        _fail("phase155_contract")
    running = prepared_start.running_state
    try:
        valid = is_valid_step_runtime_execution_result(
            value,
            workflow_id=workflow.id,
            step_id=running.current_step_id,
            step_index=running.current_step_index,
            employee_id=running.current_employee_id,
        )
    except Exception:
        valid = False
    if not valid:
        _fail("phase155_contract")


def _valid_phase172_result(
    value: object,
    runtime_result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    running_snapshot: tuple[bytes, bytes],
) -> bool:
    if type(value) not in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return False
    index = runtime_result.step_index
    step = workflow.steps[index - 1]
    identity = (
        _exact(value.workflow_id, workflow.id)
        and _exact(value.current_step_id, step.id)
        and type(value.current_step_index) is int
        and value.current_step_index == index
        and _exact(value.current_employee_id, step.employee)
    )
    if not identity:
        return False
    if type(runtime_result) is StepRuntimeExecutionFailure:
        result_ok = (
            type(value) is PersistedExecutionOutcome
            and _exact(value.outcome, "persisted_failure")
            and _exact(value.failure_category, runtime_result.invocation_result.category)
            and type(value.failure_category) is str
            and value.failure_category in _FAILURE_CATEGORIES
        )
    elif type(value) is not WorkflowProgressionDecision:
        return False
    elif index == len(workflow.steps):
        result_ok = (
            _exact(value.decision, "workflow_complete")
            and value.next_step_id is None
            and value.next_step_index is None
            and value.next_employee_id is None
            and _exact(value.reason, "last_step_succeeded")
        )
    else:
        next_step = workflow.steps[index]
        result_ok = (
            _exact(value.decision, "prepare_next_step")
            and _exact(value.next_step_id, next_step.id)
            and type(value.next_step_index) is int
            and value.next_step_index == index + 1
            and _exact(value.next_step_index, index + 1)
            and _exact(value.next_employee_id, next_step.employee)
            and _exact(value.reason, "next_step_available")
        )
    if not result_ok:
        return False
    return _valid_phase172_persistence(
        value, runtime_result, workflow, state_path, events_path, running_snapshot
    )


def _valid_phase172_persistence(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
    runtime_result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    running_snapshot: tuple[bytes, bytes],
) -> bool:
    """Verify the Phase-172 terminal state and its one appended event."""
    try:
        state_bytes = state_path.read_bytes()
        event_bytes = events_path.read_bytes()
        if not event_bytes.startswith(running_snapshot[1]):
            return False
        appended = event_bytes[len(running_snapshot[1]) :]
        if not appended or not appended.endswith(b"\n") or appended.count(b"\n") != 1:
            return False
        event = parse_runtime_step_event(json.loads(appended.decode("utf-8")))
        if type(event) is not RuntimeStepEvent:
            return False
        if serialize_runtime_step_event_jsonl(event).encode("utf-8") != appended:
            return False
        state = load_workflow_execution_state(state_path)
        if state_bytes != serialize_workflow_execution_state_json(state).encode("utf-8"):
            return False
    except Exception:
        return False

    step = workflow.steps[runtime_result.step_index - 1]
    invocation = runtime_result.invocation_result
    common = (
        type(state) is WorkflowExecutionState
        and _exact(state.workflow_id, workflow.id)
        and _exact(state.current_step_id, step.id)
        and type(state.current_step_index) is int
        and state.current_step_index == runtime_result.step_index
        and _exact(state.current_employee_id, step.employee)
        and _exact(event.workflow_id, workflow.id)
        and _exact(event.step_id, step.id)
        and type(event.step_index) is int
        and event.step_index == runtime_result.step_index
        and _exact(event.employee_id, step.employee)
        and _exact(event.previous_status, "running")
        and _exact(event.provider, invocation.provider)
        and event.request_id == invocation.request_id
    )
    if not common:
        return False
    if type(runtime_result) is StepRuntimeExecutionSuccess:
        return (
            type(value) is WorkflowProgressionDecision
            and _exact(state.status, "succeeded")
            and type(state.completed_step_ids) is tuple
            and state.completed_step_ids
            == tuple(item.id for item in workflow.steps[: runtime_result.step_index])
            and state.last_failure_category is None
            and _exact(event.event_type, "step_succeeded")
            and _exact(event.next_status, "succeeded")
            and event.failure_category is None
            and event.response_id == invocation.response_id
            and event.output_text == invocation.text
            and event.message is None
        )
    return (
        type(value) is PersistedExecutionOutcome
        and _exact(state.status, "failed")
        and type(state.completed_step_ids) is tuple
        and state.completed_step_ids
        == tuple(item.id for item in workflow.steps[: runtime_result.step_index - 1])
        and _exact(state.last_failure_category, invocation.category)
        and _exact(event.event_type, "step_failed")
        and _exact(event.next_status, "failed")
        and _exact(event.failure_category, invocation.category)
        and event.response_id is None
        and event.output_text is None
        and event.message == invocation.message
    )


def _valid_workflow(workflow: WorkflowDefinition) -> bool:
    if not (
        _nonempty(workflow.id)
        and _nonempty(workflow.name)
        and _nonempty(workflow.description)
        and type(workflow.steps) is list
        and bool(workflow.steps)
    ):
        return False
    if any(
        type(step) is not WorkflowStepDefinition
        or not _nonempty(step.id)
        or not _nonempty(step.name)
        or not _nonempty(step.employee)
        or not _nonempty(step.instructions)
        for step in workflow.steps
    ):
        return False
    ids = tuple(step.id for step in workflow.steps)
    return len(ids) == len(set(ids))


def _valid_employee(employee: EmployeeDefinition) -> bool:
    return (
        _nonempty(employee.id)
        and _nonempty(employee.name)
        and _nonempty(employee.role)
        and _nonempty(employee.instructions)
        and _nonempty(employee.model)
        and type(employee.allowed_tools) is list
        and all(_nonempty(item) for item in employee.allowed_tools)
        and len(employee.allowed_tools) == len(set(employee.allowed_tools))
    )


def _changed(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
) -> bool:
    try:
        return (
            not state_path.is_file()
            or not events_path.is_file()
            or state_path.read_bytes() != original[0]
            or events_path.read_bytes() != original[1]
        )
    except Exception:
        return True


def _restore_or_fail(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
) -> None:
    try:
        changed = _changed(state_path, events_path, original)
    except (OSError, ValueError):
        changed = True
    if not changed:
        return
    failed = False
    # Once rollback is required, make exactly one restoration attempt for each
    # target, including an unchanged target.  This is important when a target
    # disappears or a filesystem operation fails halfway through compensation.
    for path, contents in (
        (state_path, original[0]),
        (events_path, original[1]),
    ):
        try:
            path.write_bytes(contents)
        except Exception:
            failed = True
    if failed or _changed(state_path, events_path, original):
        _fail("rollback_failure")


def _exact(value: object, expected: object) -> bool:
    return type(value) is type(expected) and value == expected


def _nonempty(value: object) -> bool:
    return type(value) is str and bool(value)


def _fail(classification: Classification) -> None:
    raise ApprovedWorkflowContinuationCycleCompatibilityError(classification) from None


__all__ = [
    "ApprovedWorkflowContinuationCycleCompatibilityError",
    "ApprovedWorkflowContinuationCycleError",
    "ApprovedWorkflowContinuationCycleFailure",
    "ApprovedWorkflowContinuationCycleFailureDetail",
    "route_approved_workflow_continuation_cycle",
]
