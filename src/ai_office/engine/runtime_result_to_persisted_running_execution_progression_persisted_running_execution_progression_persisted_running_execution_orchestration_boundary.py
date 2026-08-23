"""Phase 187: Phase 186 -> one step-9 execution -> stop."""

# ruff: noqa: E501,E701,I001

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.persisted_execution_outcome_reentry import PersistedExecutionOutcome
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase155Error,
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationError as Phase141Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary import (
    _SAFE_PHASE185_ERRORS,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase186CompatibilityError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryError as Phase186Error,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory, ModelInvocationRequest
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    is_valid_step_runtime_execution_result,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
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
    "phase186_contract",
    "phase155_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
Phase186Function = Callable[..., object]
Phase147Function = Callable[[object, object, object, object, object], object]
Phase155Function = Callable[..., object]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))

# Phase 186 deliberately re-raises this exact lower safe family.  Reuse the
# public module's declared family rather than broadening or sanitizing it.
_SAFE_PHASE186_ERRORS = (Phase186Error, Phase186CompatibilityError, *_SAFE_PHASE185_ERRORS)
_SAFE_PHASE155_ERRORS = (Phase155Error, Phase141Error)


@dataclass(frozen=True)
class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 187 orchestration failure."""

    classification: Classification


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryError(ValueError):
    """Base error for the Phase 187 boundary."""


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryError
):
    """Raised for a detail-safe Phase 187 contract failure."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime persisted-running execution orchestration inputs are incompatible"
        )
        self.detail = RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryFailureDetail(
            classification
        )


def route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary(
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
    following_resolved_tools: object,
    following_api_key: object,
    following_execution_approval: object,
    following_transport: object,
    *,
    phase186_function: Phase186Function = route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary,
    phase147_function: Phase147Function = route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    phase155_function: Phase155Function = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
) -> StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Execute exactly one following step through public Phase 155, then stop."""
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase186_function,
        phase147_function,
        phase155_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    _check_targets(state_path, events_path)
    pre_phase186 = _capture(state_path, events_path)
    captured: list[tuple[object, object, object, object, object]] = []

    def capture_phase147(
        start: object,
        captured_workflow: object,
        captured_employee: object,
        captured_state: object,
        captured_events: object,
    ) -> object:
        captured.append(
            (
                start,
                captured_workflow,
                captured_employee,
                captured_state,
                captured_events,
            )
        )
        return phase147_function(
            start,
            captured_workflow,
            captured_employee,
            captured_state,
            captured_events,
        )

    try:
        persisted = phase186_function(
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
            phase147_function=capture_phase147,
        )
    except _SAFE_PHASE186_ERRORS as error:
        raise error
    except Exception:
        _fail("dependency_error")

    if type(persisted) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        if captured or not _valid_stop(result, persisted, workflow, state_path, events_path, pre_phase186):
            _fail("phase186_contract")
        return persisted

    if type(persisted) is not RunningStatePersistenceResult or not _valid_running_output(
        persisted, captured, workflow, following_employee, state_path, events_path
    ):
        _fail("phase186_contract")
    committed = _capture(state_path, events_path)

    try:
        runtime = phase155_function(
            persisted,
            captured[0][0],
            workflow,
            following_employee,
            state_path,
            events_path,
            following_resolved_tools,
            following_api_key,
            following_execution_approval,
            following_transport,
        )
    except _SAFE_PHASE155_ERRORS as error:
        _restore_or_fail(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, committed)
        _fail("dependency_error")

    if not _valid_runtime(runtime, captured[0][0], workflow):
        _restore_or_fail(state_path, events_path, committed)
        _fail("phase155_contract")
    if _changed(state_path, committed[0]) or _changed(events_path, committed[1]):
        _restore_or_fail(state_path, events_path, committed)
        _fail("committed_mutation")
    return runtime


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase186: object,
    phase147: object,
    phase155: object,
) -> None:
    if type(result) is WorkflowProgressionDecision:
        if result.decision != "workflow_complete":
            _fail("result_type")
    elif type(result) is PersistedExecutionOutcome:
        if result.outcome != "persisted_failure":
            _fail("result_type")
    elif type(result) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _fail("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _fail("state_target")
    if type(events) is not _PATH_TYPE:
        _fail("event_target")
    if state == events:
        _fail("target_conflict")
    if not callable(phase186) or not callable(phase147) or not callable(phase155):
        _fail("configuration")


def _check_targets(state: Path, events: Path) -> None:
    for path, classification in ((state, "state_target"), (events, "event_target")):
        try:
            if not path.is_file():
                _fail(classification)
        except OSError:
            _fail(classification)


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _fail("dependency_error")


def _valid_workflow(workflow: WorkflowDefinition) -> bool:
    if not (
        type(workflow.id) is str
        and bool(workflow.id)
        and type(workflow.name) is str
        and bool(workflow.name)
        and type(workflow.description) is str
        and bool(workflow.description)
        and type(workflow.steps) is list
        and bool(workflow.steps)
    ):
        return False
    return all(
        type(step) is WorkflowStepDefinition
        and type(step.id) is str
        and bool(step.id)
        and type(step.name) is str
        and bool(step.name)
        and type(step.employee) is str
        and bool(step.employee)
        and type(step.instructions) is str
        and bool(step.instructions)
        for step in workflow.steps
    ) and len({step.id for step in workflow.steps}) == len(workflow.steps)


def _valid_stop(
    result: object,
    value: object,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    pre_phase186: tuple[bytes, bytes],
) -> bool:
    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return value is result and not _changed(state, pre_phase186[0]) and not _changed(
            events, pre_phase186[1]
        )
    if type(result) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        return False
    try:
        loaded = load_workflow_execution_state(state)
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
        index = value.current_step_index
        if type(index) is not int or index not in (result.step_index + 1, result.step_index + 2):
            return False
        step = workflow.steps[index - 1]
        identity = (
            value.workflow_id == workflow.id == result.workflow_id
            and value.current_step_id == step.id
            and value.current_employee_id == step.employee
            and type(history.events) is tuple
            and type(loaded) is WorkflowExecutionState
            and loaded.current_step_index == index
        )
        if type(value) is WorkflowProgressionDecision:
            return bool(
                identity
                and type(result) is StepRuntimeExecutionSuccess
                and value.decision == "workflow_complete"
                and index == len(workflow.steps)
                and value.next_step_id is None
                and value.next_step_index is None
                and value.next_employee_id is None
                and value.reason == "last_step_succeeded"
                and loaded.status == "succeeded"
            )
        return bool(
            identity
            and type(value) is PersistedExecutionOutcome
            and value.outcome == "persisted_failure"
            and type(value.failure_category) is str
            and value.failure_category in _FAILURE_CATEGORIES
            and loaded.status == "failed"
            and loaded.last_failure_category == value.failure_category
        )
    except Exception:
        return False


def _valid_running_output(
    value: RunningStatePersistenceResult,
    captured: list[tuple[object, object, object, object, object]],
    workflow: WorkflowDefinition,
    employee: object,
    state: Path,
    events: Path,
) -> bool:
    if len(captured) != 1:
        return False
    start, captured_workflow, captured_employee, captured_state, captured_events = captured[0]
    if (
        captured_workflow is not workflow
        or captured_employee is not employee
        or captured_state is not state
        or captured_events is not events
        or type(start) is not PreparedStepExecutionStart
        or type(employee) is not EmployeeDefinition
    ):
        return False
    try:
        running = start.running_state
        request = start.request
        expected = serialize_workflow_execution_state_json(running).encode()
        loaded = load_workflow_execution_state(state)
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
        step = workflow.steps[8]
        return (
            type(value) is RunningStatePersistenceResult
            and type(value.state_bytes_written) is int
            and value.state_bytes_written == len(expected)
            and state.read_bytes() == expected
            and type(request) is ModelInvocationRequest
            and type(running) is WorkflowExecutionState
            and running.workflow_id == workflow.id
            and running.status == "running"
            and running.current_step_index == 9
            and running.current_step_id == step.id
            and running.current_employee_id == employee.id == step.employee
            and running.completed_step_ids == tuple(item.id for item in workflow.steps[:8])
            and running.last_failure_category is None
            and request.model == employee.model
            and request.system_instructions == employee.instructions
            and request.task_instructions == step.instructions
            and request.allowed_tools == tuple(employee.allowed_tools)
            and loaded == running
            and type(history.events) is tuple
            and len(history.events) == 8
        )
    except Exception:
        return False


def _valid_runtime(value: object, start: object, workflow: WorkflowDefinition) -> bool:
    try:
        running = start.running_state
        return bool(
            is_valid_step_runtime_execution_result(
                value,
                workflow_id=workflow.id,
                step_id=running.current_step_id,
                step_index=running.current_step_index,
                employee_id=running.current_employee_id,
            )
            and type(value.step_index) is int
            and value.step_index == 9
        )
    except Exception:
        return False


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
    raise RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
