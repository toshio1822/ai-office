"""Phase 176 post-runtime → prepared running-state persistence orchestration boundary."""

# ruff: noqa: E501,E701,I001

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError as Phase173Error,
)
from ai_office.engine.runtime_result_to_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPreparedStepStartOrchestrationBoundaryError as Phase175Error,
    route_runtime_result_to_prepared_step_start_orchestration_boundary,
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
    "phase175_contract",
    "phase147_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
Phase175Function = Callable[
    [object, object, object, object, object, object],
    PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
Phase147Function = Callable[
    [object, object, object, object, object],
    RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 176 orchestration failure."""

    classification: Classification


class RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryError(ValueError):
    """Base error for the Phase 176 boundary."""


class RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryError
):
    """Raised when the Phase-175 → Phase-147 orchestration cannot safely complete."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime prepared-start persistence orchestration inputs are incompatible"
        )
        self.detail = (
            RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryFailureDetail(
                classification
            )
        )


def route_runtime_result_to_prepared_start_persistence_orchestration_boundary(
    result: object,
    workflow: object,
    approval: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase175_function: Phase175Function = (
        route_runtime_result_to_prepared_step_start_orchestration_boundary
    ),
    phase147_function: Phase147Function = (
        route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    ),
) -> RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Compose public Phase 175 then public Phase 147 exactly once each.

    Phase 175 owns the durable runtime-result persistence/classification/
    progression chain plus explicit approval/employee validation and approved
    next-step preparation; Phase 147 owns the prepared-start persistence
    terminal-history validation and the durable running-state persistence for
    the exact prepared step.  Phase 176 stops after one exact
    RunningStatePersistenceResult or one exact stop object and must not
    persist, start, execute, retry, or continue the prepared step.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase175_function,
        phase147_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    # approval/employee are deliberately not prevalidated: Phase 175 may
    # durably persist/classify/progress the finished runtime result before
    # discovering invalid or missing approval/employee data.
    _check_targets(state_path, events_path)

    try:
        progressed = phase175_function(
            result, workflow, approval, employee, state_path, events_path
        )
    except (Phase175Error, Phase173Error, Phase172Error, Phase145Error, Phase146Error, Phase138Error) as error:
        raise error
    except Exception:
        _fail("dependency_error")

    if not _valid_phase175_result(result, workflow, employee, progressed):
        _fail("phase175_contract")

    # Exact post-Phase175 target bytes are the committed terminal snapshot.
    committed = _capture_targets(state_path, events_path)

    try:
        value = phase147_function(
            progressed, workflow, employee, state_path, events_path
        )
    except (Phase147Error, Phase139Error) as error:
        _restore_if_changed(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, committed)
        _fail("dependency_error")

    if not _valid_phase147_value(progressed, value):
        _restore_if_changed(state_path, events_path, committed)
        _fail("phase147_contract")

    if not _phase147_byte_contract(
        progressed, value, state_path, events_path, committed
    ):
        _restore_if_changed(state_path, events_path, committed)
        _fail("committed_mutation")

    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase175: object,
    phase147: object,
) -> None:
    # The stop input domain is narrowed to the discriminator before Phase
    # 175: only an exact workflow_complete decision and an exact
    # persisted_failure outcome may reach the Phase-175 stage.  A
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
    if not (callable(phase175) and callable(phase147)):
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


def _valid_phase175_result(
    result: object,
    workflow: WorkflowDefinition,
    employee: object,
    value: object,
) -> bool:
    """Thin Phase-175 output contract for one exact Phase-155 input.

    Stop inputs must return the exact supplied stop object by identity; a
    runtime success at a non-final step must yield an exact
    PreparedStepExecutionStart whose request/running state match the runtime
    result, supplied workflow, next step, and employee; a runtime success at
    the final step must yield an exact workflow_complete decision; a runtime
    failure must yield an exact persisted_failure outcome.  Phase 175 / its
    immediate stages' full terminal-history validation is not duplicated.
    """
    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return value is result
    if type(result) is StepRuntimeExecutionSuccess:
        index = result.step_index
        if type(index) is not int or not 1 <= index <= len(workflow.steps):
            return False
        if index == len(workflow.steps):
            if type(value) is not WorkflowProgressionDecision:
                return False
            decision = value
            current = workflow.steps[index - 1]
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
        if type(value) is not PreparedStepExecutionStart:
            return False
        request, running = value.request, value.running_state
        next_index = index + 1
        next_step = workflow.steps[index]
        expected_completed = tuple(step.id for step in workflow.steps[:index])
        return (
            type(employee) is EmployeeDefinition
            and type(request) is ModelInvocationRequest
            and request.model == employee.model
            and request.system_instructions == employee.instructions
            and request.task_instructions == next_step.instructions
            and type(request.allowed_tools) is tuple
            and request.allowed_tools == tuple(employee.allowed_tools)
            and type(running) is WorkflowExecutionState
            and running.workflow_id == workflow.id
            and running.status == "running"
            and running.current_step_id == next_step.id
            and type(running.current_step_index) is int
            and running.current_step_index == next_index
            and running.current_employee_id == employee.id
            and running.current_employee_id == next_step.employee
            and type(running.completed_step_ids) is tuple
            and running.completed_step_ids == expected_completed
            and running.last_failure_category is None
        )
    if type(value) is not PersistedExecutionOutcome:
        return False
    index = result.step_index
    if type(index) is not int or not 1 <= index <= len(workflow.steps):
        return False
    current = workflow.steps[index - 1]
    return (
        value.outcome == "persisted_failure"
        and value.workflow_id == result.workflow_id
        and value.workflow_id == workflow.id
        and value.current_step_id == result.step_id
        and value.current_step_id == current.id
        and value.current_step_index == index
        and value.current_employee_id == result.employee_id
        and value.current_employee_id == current.employee
        and value.failure_category == result.invocation_result.category
    )


def _valid_phase147_value(
    progressed: PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome,
    value: object,
) -> bool:
    """Thin Phase-147 value contract branched on the validated Phase-175 result.

    The stop routes require the exact Phase-175 stop object by identity; the
    prepared route requires an exact RunningStatePersistenceResult with an
    exact built-in positive state_bytes_written.  Phase 147's internal
    terminal-history and persistence validators are not duplicated here.
    """
    if type(progressed) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return value is progressed
    return (
        type(value) is RunningStatePersistenceResult
        and type(value.state_bytes_written) is int
        and value.state_bytes_written > 0
    )


def _phase147_byte_contract(
    progressed: PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome,
    value: RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome,
    state: Path,
    events: Path,
    committed: tuple[bytes, bytes],
) -> bool:
    """Exact byte contract after the Phase-147 stage.

    On a stop route both targets must remain exactly the committed terminal
    snapshot.  On a prepared route the state target must equal the exact
    UTF-8 serialization of the Phase-175 running state, the event target must
    remain exactly the committed event bytes, and the returned persistence
    count must equal the exact persisted state byte length; loading the final
    state through the public storage loader must reproduce the running state.
    """
    if type(progressed) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        try:
            return (
                state.read_bytes() == committed[0]
                and events.read_bytes() == committed[1]
            )
        except OSError:
            return False
    try:
        state_bytes = state.read_bytes()
        event_bytes = events.read_bytes()
        loaded = load_workflow_execution_state(state)
    except Exception:
        return False
    expected = serialize_workflow_execution_state_json(
        progressed.running_state
    ).encode("utf-8")
    return (
        type(value.state_bytes_written) is int
        and value.state_bytes_written > 0
        and value.state_bytes_written == len(state_bytes)
        and state_bytes == expected
        and event_bytes == committed[1]
        and type(loaded) is WorkflowExecutionState
        and loaded == progressed.running_state
    )


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


def _fail(classification: Classification) -> None:
    raise RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
