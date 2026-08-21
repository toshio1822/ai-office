"""Phase 182: Phase 181 durable running state -> one Phase 155 execution."""

# ruff: noqa: E501,E701,I001

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import PersistedExecutionOutcome
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase155Error,
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationError as Phase141Error,
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
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError as Phase173Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase181Error,
    route_runtime_result_to_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError as Phase180Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase179Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase177Error,
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
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationError as Phase143Error,
)
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationError as Phase144Error,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationRequest, ModelInvocationSuccess
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
    serialize_workflow_execution_state_json,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "configuration",
    "phase181_contract",
    "phase155_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
Phase181Function = Callable[..., object]
Phase147Function = Callable[[object, object, object, object, object], object]
Phase155Function = Callable[..., object]
_PATH_TYPE = type(Path())

_SAFE_PHASE181_ERRORS = (
    Phase181Error,
    Phase180Error,
    Phase179Error,
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
    Phase177Error,
    Phase161Error,
    Phase143Error,
    Phase144Error,
)


@dataclass(frozen=True)
class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 182 failure."""

    classification: Classification


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryError(
    ValueError
):
    """Base error for the Phase 182 boundary."""


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryError
):
    """Raised when the Phase 181 -> Phase 155 composition is incompatible."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime persisted running-progression persisted-running execution orchestration inputs are incompatible"
        )
        self.detail = RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryFailureDetail(
            classification
        )


def route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary(
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
    *,
    phase181_function: Phase181Function = route_runtime_result_to_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary,
    phase147_function: Phase147Function = route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    phase155_function: Phase155Function = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
) -> (
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    """Compose public Phase 181 and public Phase 155 exactly once.

    The adapter captures the exact prepared start at the real Phase-147 seam;
    it does not reconstruct one.  Phase 181 owns the durable running-state
    commit.  Phase 155 executes the newly persisted step once and remains
    read-only relative to that committed snapshot.  This boundary stops after
    returning the exact runtime result.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase181_function,
        phase147_function,
        phase155_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    _check_targets(state_path, events_path)
    before_phase181 = _snapshot(state_path, events_path)

    captured: list[object] = []
    adapter_calls: list[tuple[object, object, object, object, object]] = []

    def capture_phase147(
        value: object, wf: object, emp: object, state: object, events: object
    ) -> object:
        captured.append(value)
        adapter_calls.append((value, wf, emp, state, events))
        return phase147_function(value, wf, emp, state, events)

    try:
        progressed = phase181_function(
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
            phase147_function=capture_phase147,
        )
    except _SAFE_PHASE181_ERRORS as error:
        raise error
    except Exception:
        _fail("dependency_error")

    if not _valid_phase181_output(
        result,
        workflow,
        next_employee,
        progressed,
        captured,
        adapter_calls,
        state_path,
        events_path,
        before_phase181,
    ):
        _fail("phase181_contract")

    if type(progressed) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return progressed

    assert type(progressed) is RunningStatePersistenceResult
    assert type(captured[0]) is PreparedStepExecutionStart
    committed = _snapshot(state_path, events_path)

    try:
        value = phase155_function(
            progressed,
            captured[0],
            workflow,
            next_employee,
            state_path,
            events_path,
            next_resolved_tools,
            next_api_key,
            next_execution_approval,
            next_transport,
        )
    except (Phase155Error, Phase141Error) as error:
        _restore_if_changed(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, committed)
        _fail("dependency_error")

    if not _valid_phase155_output(captured[0], workflow, next_employee, value):
        _restore_if_changed(state_path, events_path, committed)
        _fail("phase155_contract")
    _require_unchanged(state_path, events_path, committed, "committed_mutation")
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase181: object,
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
    if type(workflow) is not WorkflowDefinition:
        _fail("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _fail("state_target")
    if type(events) is not _PATH_TYPE:
        _fail("event_target")
    if state == events:
        _fail("target_conflict")
    if not (callable(phase181) and callable(phase147) and callable(phase155)):
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


def _valid_phase181_output(
    result: object,
    workflow: WorkflowDefinition,
    next_employee: object,
    value: object,
    captured: list[object],
    adapter_calls: list[tuple[object, object, object, object, object]],
    state: Path,
    events: Path,
    before: tuple[bytes, bytes],
) -> bool:
    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return (
            value is result
            and not captured
            and not adapter_calls
            and not _changed(state, before[0])
            and not _changed(events, before[1])
        )
    if type(value) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return (
            not captured
            and not adapter_calls
            and _valid_phase181_stop(result, workflow, value, state, events)
        )
    if (
        type(value) is not RunningStatePersistenceResult
        or type(value.state_bytes_written) is not int
        or value.state_bytes_written <= 0
        or len(captured) != 1
        or len(adapter_calls) != 1
    ):
        return False
    start = captured[0]
    call = adapter_calls[0]
    if not (
        type(start) is PreparedStepExecutionStart
        and call[0] is start
        and call[1] is workflow
        and call[2] is next_employee
        and call[3] is state
        and call[4] is events
    ):
        return False
    return _valid_phase181_start(
        result, workflow, next_employee, start, value, state, events
    )


def _valid_phase181_start(
    result: object,
    workflow: WorkflowDefinition,
    next_employee: object,
    start: PreparedStepExecutionStart,
    persistence: RunningStatePersistenceResult,
    state: Path,
    events: Path,
) -> bool:
    try:
        if type(result) is not StepRuntimeExecutionSuccess:
            return False
        index = result.step_index
        if (
            type(index) is not int
            or not 1 <= index < len(workflow.steps) - 1
            or result.workflow_id != workflow.id
            or result.step_id != workflow.steps[index - 1].id
            or result.employee_id != workflow.steps[index - 1].employee
        ):
            return False
        if type(next_employee) is not EmployeeDefinition:
            return False
        next_step = workflow.steps[index + 1]
        request, running = start.request, start.running_state
        expected = serialize_workflow_execution_state_json(running).encode("utf-8")
        state_bytes = state.read_bytes()
        loaded = load_workflow_execution_state(state)
        return (
            type(request) is ModelInvocationRequest
            and type(running) is WorkflowExecutionState
            and request.model == next_employee.model
            and request.system_instructions == next_employee.instructions
            and request.task_instructions == next_step.instructions
            and type(request.allowed_tools) is tuple
            and request.allowed_tools == tuple(next_employee.allowed_tools)
            and running.workflow_id == workflow.id == result.workflow_id
            and running.status == "running"
            and running.current_step_id == next_step.id
            and type(running.current_step_index) is int
            and running.current_step_index == index + 2
            and running.current_employee_id == next_employee.id
            and running.current_employee_id == next_step.employee
            and type(running.completed_step_ids) is tuple
            and running.completed_step_ids == tuple(
                step.id for step in workflow.steps[: index + 1]
            )
            and running.last_failure_category is None
            and type(loaded) is WorkflowExecutionState
            and loaded == running
            and state_bytes == expected
            and persistence.state_bytes_written == len(state_bytes)
            and _valid_running_history(result, workflow, running, events)
        )
    except Exception:
        return False


def _valid_phase181_stop(
    result: object,
    workflow: WorkflowDefinition,
    value: object,
    state: Path,
    events: Path,
) -> bool:
    try:
        if type(result) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
            return False
        index = result.step_index
        if (
            type(index) is not int
            or not 1 <= index < len(workflow.steps)
            or result.workflow_id != workflow.id
            or result.step_id != workflow.steps[index - 1].id
            or result.employee_id != workflow.steps[index - 1].employee
        ):
            return False
        step = workflow.steps[index]
        if type(value) is WorkflowProgressionDecision:
            return (
                value.decision == "workflow_complete"
                and value.workflow_id == workflow.id == result.workflow_id
                and value.current_step_id == step.id
                and value.current_step_index == index + 1
                and value.current_employee_id == step.employee
                and value.next_step_id is None
                and value.next_step_index is None
                and value.next_employee_id is None
                and value.reason == "last_step_succeeded"
                and _valid_terminal_snapshot(workflow, index + 1, "succeeded", state, events)
            )
        if type(value) is PersistedExecutionOutcome:
            return (
                value.outcome == "persisted_failure"
                and value.workflow_id == workflow.id == result.workflow_id
                and value.current_step_id == step.id
                and value.current_step_index == index + 1
                and value.current_employee_id == step.employee
                and type(value.failure_category) is str
                and _valid_terminal_snapshot(
                    workflow,
                    index + 1,
                    "failed",
                    state,
                    events,
                    failure_category=value.failure_category,
                )
            )
    except Exception:
        return False
    return False


def _valid_terminal_snapshot(
    workflow: WorkflowDefinition,
    executed: int,
    status: Literal["succeeded", "failed"],
    state: Path,
    events: Path,
    *,
    failure_category: object = None,
) -> bool:
    try:
        loaded = load_workflow_execution_state(state)
        lines = [line for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
        if type(loaded) is not WorkflowExecutionState or len(lines) != executed:
            return False
        step = workflow.steps[executed - 1]
        expected_completed = (
            tuple(item.id for item in workflow.steps[:executed])
            if status == "succeeded"
            else tuple(item.id for item in workflow.steps[: executed - 1])
        )
        terminal = RuntimeStepEvent(**json.loads(lines[-1]))
        return (
            loaded.workflow_id == workflow.id
            and loaded.status == status
            and loaded.current_step_id == step.id
            and loaded.current_step_index == executed
            and loaded.current_employee_id == step.employee
            and loaded.completed_step_ids == expected_completed
            and loaded.last_failure_category == failure_category
            and terminal.workflow_id == workflow.id
            and terminal.step_id == step.id
            and terminal.step_index == executed
            and terminal.employee_id == step.employee
            and terminal.next_status == status
            and (
                terminal.event_type == "step_succeeded"
                and status == "succeeded"
                and terminal.failure_category is None
                or terminal.event_type == "step_failed"
                and status == "failed"
                and terminal.failure_category == failure_category
            )
        )
    except Exception:
        return False


def _valid_running_history(
    result: StepRuntimeExecutionSuccess,
    workflow: WorkflowDefinition,
    running: WorkflowExecutionState,
    events: Path,
) -> bool:
    try:
        lines = [line for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
        completed = running.current_step_index - 1
        if len(lines) != completed:
            return False
        invocation = result.invocation_result
        if type(invocation) is not ModelInvocationSuccess:
            return False
        for position, line in enumerate(lines, 1):
            event = RuntimeStepEvent(**json.loads(line))
            step = workflow.steps[position - 1]
            if not (
                type(event) is RuntimeStepEvent
                and event.event_type == "step_succeeded"
                and event.workflow_id == workflow.id
                and event.step_id == step.id
                and event.step_index == position
                and event.employee_id == step.employee
                and event.previous_status == "running"
                and event.next_status == "succeeded"
                and event.failure_category is None
                and event.message is None
            ):
                return False
            if position == result.step_index:
                if not (
                    event.provider == invocation.provider
                    and event.response_id == invocation.response_id
                    and event.request_id == invocation.request_id
                    and event.output_text == invocation.text
                ):
                    return False
        return True
    except Exception:
        return False


def _valid_phase155_output(
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    next_employee: object,
    value: object,
) -> bool:
    if type(next_employee) is not EmployeeDefinition:
        return False
    running = start.running_state
    if type(running) is not WorkflowExecutionState or running.status != "running":
        return False
    try:
        return is_valid_step_runtime_execution_result(
            value,
            workflow_id=workflow.id,
            step_id=running.current_step_id,
            step_index=running.current_step_index,
            employee_id=running.current_employee_id,
        ) and running.current_employee_id == next_employee.id
    except Exception:
        return False


def _snapshot(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _fail("dependency_error")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_if_changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
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


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _fail(classification)


def _fail(classification: Classification) -> None:
    raise RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
