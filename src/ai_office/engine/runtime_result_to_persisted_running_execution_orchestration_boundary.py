"""Phase 177 post-runtime → persisted next-step running execution orchestration boundary."""

# ruff: noqa: E501,E701,I001

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
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
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError as Phase173Error,
)
from ai_office.engine.runtime_result_to_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryError as Phase176Error,
    route_runtime_result_to_prepared_start_persistence_orchestration_boundary,
)
from ai_office.engine.runtime_result_to_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPreparedStepStartOrchestrationBoundaryError as Phase175Error,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
)
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
    "phase176_contract",
    "phase155_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
Phase176Function = Callable[
    [object, object, object, object, object, object],
    RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
Phase147Function = Callable[
    [object, object, object, object, object],
    RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
Phase155Function = Callable[
    [object, object, object, object, object, object, object, object, object, object],
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 177 orchestration failure."""

    classification: Classification


class RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryError(ValueError):
    """Base error for the Phase 177 boundary."""


class RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryError
):
    """Raised when the Phase-176 → Phase-155 orchestration cannot safely complete."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime persisted running-execution orchestration inputs are incompatible"
        )
        self.detail = RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryFailureDetail(
            classification
        )


def route_runtime_result_to_persisted_running_execution_orchestration_boundary(
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
    phase176_function: Phase176Function = (
        route_runtime_result_to_prepared_start_persistence_orchestration_boundary
    ),
    phase147_function: Phase147Function = (
        route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    ),
    phase155_function: Phase155Function = (
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    ),
) -> (
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    """Compose public Phase 176 then public Phase 155 exactly once each.

    Phase 176 owns the durable current runtime-result persistence,
    classification/progression, explicit approval/employee validation, and the
    durable next-step running-state persistence; the capture-only Phase-147
    adapter records the exact PreparedStepExecutionStart produced by the real
    Phase-147 handoff; Phase 155 owns exactly one next-step runtime execution
    through the real Phase141 → 133 → 126 → lower chain.  Phase 177 stops
    after returning the exact next-step runtime result or the exact stop
    object and must not persist that result, progress again, prepare another
    step, retry, loop, finalize, schedule, parallelize, or add CLI/GUI
    behavior.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase176_function,
        phase147_function,
        phase155_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    # preparation_approval / employee / resolved_tools / api_key /
    # execution_approval / transport are deliberately not prevalidated:
    # Phase 176 must durably establish the finished current-step commit and
    # the next running-state commit before Phase 155 validates execution
    # inputs.  Phase 176 remains authoritative for preparation rejection.
    _check_targets(state_path, events_path)

    captured: list[object] = []
    capture_calls: list[tuple[object, object, object, object, object]] = []

    def capture_phase147(
        value: object, wf: object, emp: object, sp: object, ep: object
    ) -> object:
        captured.append(value)
        capture_calls.append((value, wf, emp, sp, ep))
        return phase147_function(value, wf, emp, sp, ep)

    try:
        progressed = phase176_function(
            result,
            workflow,
            preparation_approval,
            employee,
            state_path,
            events_path,
            phase147_function=capture_phase147,  # type: ignore[arg-type]
        )
    except (
        Phase176Error,
        Phase175Error,
        Phase173Error,
        Phase172Error,
        Phase145Error,
        Phase146Error,
        Phase138Error,
        Phase147Error,
        Phase139Error,
    ) as error:
        raise error
    except Exception:
        _fail("dependency_error")

    if not _valid_phase176_output(
        result,
        workflow,
        employee,
        progressed,
        captured,
        capture_calls,
        state_path,
        events_path,
    ):
        _fail("phase176_contract")

    if type(progressed) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return progressed

    assert type(progressed) is RunningStatePersistenceResult
    start = captured[0]
    assert type(start) is PreparedStepExecutionStart
    committed = _capture_targets(state_path, events_path)

    try:
        value = phase155_function(
            progressed,
            start,
            workflow,
            employee,
            state_path,
            events_path,
            resolved_tools,
            api_key,
            execution_approval,
            transport,
        )
    except (Phase155Error, Phase141Error) as error:
        _restore_if_changed(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, committed)
        _fail("dependency_error")

    try:
        _require_unchanged(state_path, events_path, committed, "committed_mutation")
        if not _valid_phase155_output(start, workflow, employee, value):
            _fail("phase155_contract")
    except RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as error:
        if error.detail.classification != "rollback_failure":
            _restore_if_changed(state_path, events_path, committed)
        raise
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase176: object,
    phase147: object,
    phase155: object,
) -> None:
    # The stop input domain is narrowed to the discriminator before Phase 176:
    # only an exact workflow_complete decision and an exact persisted_failure
    # outcome are stop inputs; a prepare_next_step decision or a
    # persisted_success outcome is rejected here as result_type.
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
    if not (callable(phase176) and callable(phase147) and callable(phase155)):
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


def _valid_phase176_output(
    result: object,
    workflow: WorkflowDefinition,
    employee: object,
    value: object,
    captured: list[object],
    capture_calls: list[tuple[object, object, object, object, object]],
    state: Path,
    events: Path,
) -> bool:
    """Thin Phase-176 output/capture contract for one exact Phase-155 input.

    Stop routes require the exact stop value delegated through the
    capture-only Phase-147 adapter exactly once with canonical 5 args, with
    no captured prepared start; the prepared route requires an exact
    RunningStatePersistenceResult plus exactly one exact
    PreparedStepExecutionStart captured from the real Phase-147 handoff, with
    thin linkage to the persisted running state.  Phase 176's full
    terminal-history and persistence validation is not duplicated here.
    """
    if type(value) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return _valid_phase176_stop(
            result,
            workflow,
            employee,
            value,
            captured,
            capture_calls,
            state,
            events,
        )
    if (
        type(value) is not RunningStatePersistenceResult
        or type(value.state_bytes_written) is not int
        or value.state_bytes_written <= 0
        or len(captured) != 1
        or type(captured[0]) is not PreparedStepExecutionStart
    ):
        return False
    start = captured[0]
    request, running = start.request, start.running_state
    if type(result) is not StepRuntimeExecutionSuccess:
        return False
    index = result.step_index
    if type(index) is not int or not 1 <= index < len(workflow.steps):
        return False
    next_step = workflow.steps[index]
    expected_completed = tuple(step.id for step in workflow.steps[:index])
    if type(request) is not ModelInvocationRequest:
        return False
    if type(running) is not WorkflowExecutionState:
        return False
    try:
        state_bytes = state.read_bytes()
        loaded = load_workflow_execution_state(state)
        expected = serialize_workflow_execution_state_json(running).encode("utf-8")
    except Exception:
        return False
    return (
        type(employee) is EmployeeDefinition
        and result.workflow_id == workflow.id
        and result.step_id == workflow.steps[index - 1].id
        and result.employee_id == workflow.steps[index - 1].employee
        and request.model == employee.model
        and request.system_instructions == employee.instructions
        and request.task_instructions == next_step.instructions
        and type(request.allowed_tools) is tuple
        and request.allowed_tools == tuple(employee.allowed_tools)
        and running.workflow_id == workflow.id
        and running.status == "running"
        and running.current_step_id == next_step.id
        and type(running.current_step_index) is int
        and running.current_step_index == index + 1
        and running.current_employee_id == next_step.employee
        and running.current_employee_id == employee.id
        and type(running.completed_step_ids) is tuple
        and running.completed_step_ids == expected_completed
        and running.last_failure_category is None
        and value.state_bytes_written == len(state_bytes)
        and state_bytes == expected
        and _valid_phase176_event_history(result, workflow, events)
        and type(loaded) is WorkflowExecutionState
        and loaded == running
    )


def _valid_phase176_event_history(
    result: StepRuntimeExecutionSuccess,
    workflow: WorkflowDefinition,
    events: Path,
) -> bool:
    """Thin committed terminal history check through the just-finished step.

    Requires exactly ``result.step_index`` JSONL records, each an exact
    succeeded runtime event whose workflow / step / index / employee linkage
    matches the corresponding workflow step, with running→succeeded transition
    and no failure category.  The just-finished event must exactly match the
    original runtime success (provider / response / request / output / no
    message).  Phase 176's full terminal-history validator is not duplicated
    here.
    """
    invocation = result.invocation_result
    if type(invocation) is not ModelInvocationSuccess:
        return False
    try:
        lines = [
            line
            for line in events.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError):
        return False
    if len(lines) != result.step_index:
        return False
    for position, line in enumerate(lines, start=1):
        try:
            event = RuntimeStepEvent(**json.loads(line))
        except Exception:
            return False
        step = workflow.steps[position - 1]
        if (
            event.event_type != "step_succeeded"
            or event.workflow_id != workflow.id
            or event.step_id != step.id
            or type(event.step_index) is not int
            or event.step_index != position
            or event.employee_id != step.employee
            or event.previous_status != "running"
            or event.next_status != "succeeded"
            or event.failure_category is not None
        ):
            return False
        if position == result.step_index and (
            event.provider != invocation.provider
            or event.response_id != invocation.response_id
            or event.request_id != invocation.request_id
            or event.output_text != invocation.text
            or event.message is not None
        ):
            return False
    return True


def _valid_phase176_stop(
    result: object,
    workflow: WorkflowDefinition,
    employee: object,
    value: object,
    captured: list[object],
    capture_calls: list[tuple[object, object, object, object, object]],
    state: Path,
    events: Path,
) -> bool:
    """Stop-route thin contract: exactly-once canonical delegation + identity.

    Stop routes require the exact stop value delegated through the
    capture-only Phase-147 adapter exactly once with canonical 5 args
    (value, workflow, employee, state, events); zero calls, multiple calls, a
    different value, or non-canonical args are ``phase176_contract`` with
    Phase 155 zero calls.  Original stop inputs require the exact supplied
    stop object by identity.  Original final success requires exact
    ``workflow_complete`` with exact workflow / current-step / index /
    employee / None-next-fields / reason linkage.  Original runtime failure
    requires exact ``persisted_failure`` with exact linkage and failure
    category.
    """
    if (
        len(capture_calls) != 1
        or capture_calls[0][0] is not value
        or capture_calls[0][1] is not workflow
        or capture_calls[0][2] is not employee
        or capture_calls[0][3] is not state
        or capture_calls[0][4] is not events
    ):
        return False
    if any(type(item) is PreparedStepExecutionStart for item in captured):
        return False
    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return value is result
    if type(result) is StepRuntimeExecutionSuccess:
        return _valid_workflow_complete(result, workflow, value)
    return _valid_persisted_failure(result, workflow, value)


def _valid_workflow_complete(
    result: StepRuntimeExecutionSuccess,
    workflow: WorkflowDefinition,
    value: object,
) -> bool:
    """Exact final-success stop: workflow_complete with exact linkage / reason."""
    if type(value) is not WorkflowProgressionDecision:
        return False
    return (
        type(result.step_index) is int
        and result.step_index == len(workflow.steps)
        and result.workflow_id == workflow.id
        and result.step_id == workflow.steps[result.step_index - 1].id
        and result.employee_id == workflow.steps[result.step_index - 1].employee
        and value.decision == "workflow_complete"
        and value.workflow_id == workflow.id
        and value.current_step_id == result.step_id
        and type(value.current_step_index) is int
        and value.current_step_index == result.step_index
        and value.current_employee_id == result.employee_id
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and value.reason == "last_step_succeeded"
    )


def _valid_persisted_failure(
    result: StepRuntimeExecutionFailure,
    workflow: WorkflowDefinition,
    value: object,
) -> bool:
    """Exact runtime-failure stop: persisted_failure with exact linkage / category."""
    if type(value) is not PersistedExecutionOutcome:
        return False
    if type(result.invocation_result) is not ModelInvocationFailure:
        return False
    index = result.step_index
    if type(index) is not int or not 1 <= index <= len(workflow.steps):
        return False
    current_step = workflow.steps[index - 1]
    return (
        value.outcome == "persisted_failure"
        and result.workflow_id == workflow.id
        and result.step_id == current_step.id
        and result.employee_id == current_step.employee
        and value.workflow_id == workflow.id
        and value.current_step_id == result.step_id
        and type(value.current_step_index) is int
        and value.current_step_index == result.step_index
        and value.current_employee_id == result.employee_id
        and value.failure_category == result.invocation_result.category
    )


def _valid_phase155_output(
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    employee: object,
    value: object,
) -> bool:
    """Thin Phase-155 output contract for one exact next-step runtime result.

    Reuses the public canonical runtime-result validator
    (``is_valid_step_runtime_execution_result``) for exact invocation-result
    type / provider / response / status / request / text semantics.  The
    handoff linkage is exact step-id / index / employee against the captured
    start running state and the supplied employee.  Phase 155's
    request/tool/key/approval/transport validator and lower provider response
    parser are not duplicated here.
    """
    if type(employee) is not EmployeeDefinition:
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
        )
    except Exception:
        return False


def _capture_targets(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _fail("dependency_error")


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
    raise RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
