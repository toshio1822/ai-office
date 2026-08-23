"""Phase 185 post-runtime progression -> prepared-step-start boundary."""

# ruff: noqa: E501,E701,I001

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.persisted_execution_outcome_reentry import PersistedExecutionOutcome
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
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationError as Phase144Error,
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
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_approved_preparation_orchestration_boundary as route_phase184,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory, ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, StepRuntimeExecutionFailure, StepRuntimeExecutionSuccess, WorkflowExecutionState
from ai_office.storage import load_workflow_execution_state

Classification = Literal[
    "result_type", "workflow_definition", "state_target", "event_target",
    "target_conflict", "configuration", "phase184_contract", "phase146_contract",
    "dependency_error", "committed_mutation", "rollback_failure",
]
Phase184Function = Callable[..., PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome]
Phase146Function = Callable[..., PreparedStepExecutionStart]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_SAFE_PHASE184_ERRORS = (
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
    Phase137Error,
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
)

@dataclass(frozen=True)
class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryFailureDetail:
    classification: Classification

class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryError(ValueError):
    pass

class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__("post-runtime persisted running-progression prepared-step-start orchestration inputs are incompatible")
        self.detail = RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryFailureDetail(classification)


def route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_step_start_orchestration_boundary(
    result: object, workflow: object, preparation_approval: object, employee: object,
    state_path: object, events_path: object, resolved_tools: object, api_key: object,
    execution_approval: object, transport: object, next_preparation_approval: object,
    next_employee: object, next_resolved_tools: object, next_api_key: object,
    next_execution_approval: object, next_transport: object,
    following_preparation_approval: object, following_employee: object, *,
    phase184_function: Phase184Function = route_phase184,
    phase146_function: Phase146Function = route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
) -> PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome:
    _check_inputs(result, workflow, state_path, events_path, phase184_function, phase146_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    _check_targets(state_path, events_path)
    pre = _capture(state_path, events_path)
    try:
        progressed = phase184_function(
            result, workflow, preparation_approval, employee, state_path, events_path,
            resolved_tools, api_key, execution_approval, transport,
            next_preparation_approval, next_employee, next_resolved_tools, next_api_key,
            next_execution_approval, next_transport, following_preparation_approval,
            following_employee,
        )
    except _SAFE_PHASE184_ERRORS as error:
        raise error
    except Exception:
        _fail("dependency_error")

    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        if progressed is not result or _changed(state_path, pre[0]) or _changed(events_path, pre[1]):
            _fail("phase184_contract")
        return progressed

    if type(progressed) is WorkflowProgressionDecision or type(progressed) is PersistedExecutionOutcome:
        if not _valid_stop(result, workflow, progressed, state_path, events_path):
            _fail("phase184_contract")
        return progressed
    if type(progressed) is not PreparedWorkflowStep or not _valid_prepared(result, workflow, following_employee, progressed, state_path, events_path):
        _fail("phase184_contract")

    committed = _capture(state_path, events_path)
    try:
        value = phase146_function(progressed, workflow, following_employee, state_path, events_path)
    except (Phase146Error, Phase138Error) as error:
        _restore_or_fail(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, committed)
        _fail("dependency_error")
    if type(value) is not PreparedStepExecutionStart or not _valid_start(progressed, workflow, value):
        _restore_or_fail(state_path, events_path, committed)
        _fail("phase146_contract")
    if _changed(state_path, committed[0]) or _changed(events_path, committed[1]):
        _restore_or_fail(state_path, events_path, committed)
        _fail("committed_mutation")
    return value


def _check_inputs(result: object, workflow: object, state: object, events: object, phase184: object, phase146: object) -> None:
    if type(result) is WorkflowProgressionDecision:
        if result.decision != "workflow_complete": _fail("result_type")
    elif type(result) is PersistedExecutionOutcome:
        if result.outcome != "persisted_failure": _fail("result_type")
    elif type(result) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition: _fail("workflow_definition")
    if type(state) is not _PATH_TYPE: _fail("state_target")
    if type(events) is not _PATH_TYPE: _fail("event_target")
    if state == events: _fail("target_conflict")
    if not callable(phase184) or not callable(phase146): _fail("configuration")


def _check_targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file(): _fail("state_target")
    except OSError: _fail("state_target")
    try:
        if not events.is_file(): _fail("event_target")
    except OSError: _fail("event_target")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try: return state.read_bytes(), events.read_bytes()
    except OSError: _fail("dependency_error")


def _valid_stop(result: object, workflow: WorkflowDefinition, value: object, state: Path, events: Path) -> bool:
    try:
        if type(result) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure): return False
        original = result.step_index
        current = value.current_step_index
        if type(original) is not int or type(current) is not int or current not in (original + 1, original + 2): return False
        if not 1 <= current <= len(workflow.steps): return False
        step = workflow.steps[current - 1]
        if type(value) is WorkflowProgressionDecision:
            return (value.decision == "workflow_complete" and type(result) is StepRuntimeExecutionSuccess and current == len(workflow.steps) and value.workflow_id == workflow.id == result.workflow_id and value.current_step_id == step.id and value.current_step_index == current and value.current_employee_id == step.employee and value.next_step_id is None and value.next_step_index is None and value.next_employee_id is None and value.reason == "last_step_succeeded" and _valid_snapshot(workflow, current, "succeeded", state, events))
        return (value.outcome == "persisted_failure" and value.workflow_id == workflow.id == result.workflow_id and value.current_step_id == step.id and value.current_step_index == current and value.current_employee_id == step.employee and type(value.failure_category) is str and value.failure_category in _FAILURE_CATEGORIES and _valid_snapshot(workflow, current, "failed", state, events, failure_category=value.failure_category))
    except Exception: return False


def _valid_prepared(result: object, workflow: WorkflowDefinition, employee: object, prepared: PreparedWorkflowStep, state: Path, events: Path) -> bool:
    try:
        if type(result) is not StepRuntimeExecutionSuccess or type(employee) is not EmployeeDefinition: return False
        original = result.step_index
        if type(original) is not int or original + 3 > len(workflow.steps): return False
        step = workflow.steps[original + 2]
        return (prepared.workflow_id == workflow.id == result.workflow_id and prepared.step_id == step.id and type(prepared.step_index) is int and prepared.step_index == original + 3 and prepared.employee_id == employee.id and prepared.employee_id == step.employee and prepared.employee_instructions == employee.instructions and prepared.step_instructions == step.instructions and prepared.model == employee.model and type(prepared.allowed_tool_names) is tuple and prepared.allowed_tool_names == tuple(employee.allowed_tools) and _valid_snapshot(workflow, original + 2, "succeeded", state, events))
    except Exception: return False


def _valid_snapshot(workflow: WorkflowDefinition, index: int, status: Literal["succeeded", "failed"], state: Path, events: Path, *, failure_category: object = None) -> bool:
    try:
        loaded = load_workflow_execution_state(state)
        lines = [line for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
        step = workflow.steps[index - 1]
        expected = tuple(item.id for item in workflow.steps[:index]) if status == "succeeded" else tuple(item.id for item in workflow.steps[:index - 1])
        if type(loaded) is not WorkflowExecutionState or len(lines) != index or loaded.workflow_id != workflow.id or loaded.status != status or loaded.current_step_id != step.id or loaded.current_step_index != index or loaded.current_employee_id != step.employee or loaded.completed_step_ids != expected: return False
        if status == "succeeded" and loaded.last_failure_category is not None: return False
        if status == "failed" and loaded.last_failure_category != failure_category: return False
        terminal = RuntimeStepEvent(**json.loads(lines[-1]))
        return terminal.workflow_id == workflow.id and terminal.step_id == step.id and terminal.step_index == index and terminal.employee_id == step.employee and terminal.next_status == status and ((status == "succeeded" and terminal.event_type == "step_succeeded" and terminal.failure_category is None) or (status == "failed" and terminal.event_type == "step_failed" and terminal.failure_category == failure_category))
    except Exception: return False


def _valid_start(prepared: PreparedWorkflowStep, workflow: WorkflowDefinition, value: PreparedStepExecutionStart) -> bool:
    request, running = value.request, value.running_state
    return (type(request) is ModelInvocationRequest and type(running) is WorkflowExecutionState and request.model == prepared.model and request.system_instructions == prepared.employee_instructions and request.task_instructions == prepared.step_instructions and type(request.allowed_tools) is tuple and request.allowed_tools == prepared.allowed_tool_names and running.workflow_id == workflow.id == prepared.workflow_id and running.status == "running" and running.current_step_id == prepared.step_id and running.current_step_index == prepared.step_index and running.current_employee_id == prepared.employee_id and type(running.completed_step_ids) is tuple and running.completed_step_ids == tuple(step.id for step in workflow.steps[:prepared.step_index - 1]) and running.last_failure_category is None)


def _changed(path: Path, before: bytes) -> bool:
    try: return not path.is_file() or path.read_bytes() != before
    except OSError: return True


def _restore_or_fail(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    if not (_changed(state, original[0]) or _changed(events, original[1])): return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try: path.write_bytes(contents)
        except OSError: failed = True
    if failed or _changed(state, original[0]) or _changed(events, original[1]): _fail("rollback_failure")


def _fail(classification: Classification) -> None:
    raise RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError(classification) from None
