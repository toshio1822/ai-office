"""Phase 183: Phase 182 runtime execution -> Phase 172 progression."""

# ruff: noqa: E501,E701,I001

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationError as Phase144Error,
)
from ai_office.engine.persisted_execution_outcome_reentry import PersistedExecutionOutcome
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
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationError as Phase137Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
)
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError as Phase173Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase177Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase179Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase178Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase182Error,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary as route_phase182,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase181Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError as Phase180Error,
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
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase161Error,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    is_valid_step_runtime_execution_result,
)
from ai_office.storage import load_workflow_execution_state, serialize_runtime_step_event_jsonl

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "configuration",
    "phase182_contract",
    "phase172_contract",
    "dependency_error",
]
Phase182Function = Callable[..., object]
Phase172Function = Callable[[object, object, object, object], object]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(
    {
        "api_error",
        "transport_error",
        "invalid_response",
        "invalid_output",
        "invalid_request",
        "approval_required",
    }
)

_SAFE_PHASE182_ERRORS = (
    Phase182Error,
    Phase181Error,
    Phase180Error,
    Phase179Error,
    Phase178Error,
    Phase177Error,
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
    Phase161Error,
    Phase143Error,
    Phase144Error,
)
_SAFE_PHASE172_ERRORS = (
    Phase161Error,
    Phase143Error,
    Phase144Error,
    Phase172Error,
    Phase172CompatibilityError,
)


@dataclass(frozen=True)
class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 183 failure."""

    classification: Classification


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryError(
    ValueError
):
    """Base error for the Phase 183 boundary."""


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryError
):
    """Raised when the Phase 182 -> Phase 172 composition is incompatible."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime persisted running-execution progression persisted-running execution progression orchestration inputs are incompatible"
        )
        self.detail = RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail(
            classification
        )


def route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_orchestration_boundary(
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
    phase182_function: Phase182Function = route_phase182,
    phase172_function: Phase172Function = route_runtime_result_to_progression_orchestration_boundary,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Compose public Phase 182 and public Phase 172 exactly once.

    Phase 182 owns all preceding durable running-state work and the second
    continuation execution.  Phase 172 owns the terminal runtime-result
    persistence, classification, and progression.  This boundary never adds
    rollback, retry, preparation, execution, or continuation of its own.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase182_function,
        phase172_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    _check_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)

    try:
        value = phase182_function(
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
    except _SAFE_PHASE182_ERRORS as error:
        raise error
    except Exception:
        _fail("dependency_error")

    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        if value is not result or not _same_targets(state_path, events_path, original):
            _fail("phase182_contract")
        return value

    if type(value) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        if not _valid_phase182_stop(result, workflow, value, state_path, events_path):
            _fail("phase182_contract")
        return value

    if not _valid_phase182_runtime_output(
        result, workflow, value, state_path, events_path
    ):
        _fail("phase182_contract")
    committed = _capture_targets(state_path, events_path)

    try:
        progressed = phase172_function(value, workflow, state_path, events_path)
    except _SAFE_PHASE172_ERRORS as error:
        raise error
    except Exception:
        _fail("dependency_error")

    if not _valid_phase172_output(
        value, workflow, progressed, state_path, events_path, committed
    ):
        _fail("phase172_contract")
    return progressed


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase182: object,
    phase172: object,
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
    if not (callable(phase182) and callable(phase172)):
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


def _valid_phase182_runtime_output(
    result: object,
    workflow: WorkflowDefinition,
    value: object,
    state: Path,
    events: Path,
) -> bool:
    if type(result) is not StepRuntimeExecutionSuccess:
        return False
    if type(value) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        return False
    try:
        original_index = result.step_index
        index = value.step_index
        if (
            type(original_index) is not int
            or type(index) is not int
            or index != original_index + 2
            or not 1 <= original_index < index <= len(workflow.steps)
            or value.workflow_id != workflow.id
            or value.step_id != workflow.steps[index - 1].id
            or value.employee_id != workflow.steps[index - 1].employee
        ):
            return False
        loaded = load_workflow_execution_state(state)
        event_bytes = events.read_bytes()
    except Exception:
        return False
    if type(loaded) is not WorkflowExecutionState or loaded.status != "running":
        return False
    if (
        loaded.workflow_id != workflow.id
        or loaded.current_step_id != value.step_id
        or loaded.current_step_index != index
        or loaded.current_employee_id != value.employee_id
        or type(loaded.completed_step_ids) is not tuple
        or loaded.completed_step_ids
        != tuple(step.id for step in workflow.steps[: index - 1])
        or loaded.last_failure_category is not None
    ):
        return False
    try:
        if not is_valid_step_runtime_execution_result(
            value,
            workflow_id=workflow.id,
            step_id=loaded.current_step_id,
            step_index=loaded.current_step_index,
            employee_id=loaded.current_employee_id,
        ):
            return False
        lines = [line for line in event_bytes.decode("utf-8").splitlines() if line.strip()]
    except Exception:
        return False
    if len(lines) != index - 1:
        return False
    for position, line in enumerate(lines, start=1):
        try:
            event = RuntimeStepEvent(**json.loads(line))
        except Exception:
            return False
        if not _valid_succeeded_event(event, workflow, position):
            return False
    return True


def _valid_phase182_stop(
    result: object,
    workflow: WorkflowDefinition,
    value: object,
    state: Path,
    events: Path,
) -> bool:
    if type(result) is not StepRuntimeExecutionSuccess:
        return False
    try:
        original_index = result.step_index
        index = original_index + 1
        if (
            type(original_index) is not int
            or not 1 <= original_index < index <= len(workflow.steps)
        ):
            return False
        step = workflow.steps[index - 1]
        if type(value) is WorkflowProgressionDecision:
            if not (
                index == len(workflow.steps)
                and value.decision == "workflow_complete"
                and value.workflow_id == workflow.id == result.workflow_id
                and value.current_step_id == step.id
                and value.current_step_index == index
                and value.current_employee_id == step.employee
                and value.next_step_id is None
                and value.next_step_index is None
                and value.next_employee_id is None
                and value.reason == "last_step_succeeded"
            ):
                return False
            expected_status = "succeeded"
            failure_category = None
        elif type(value) is PersistedExecutionOutcome:
            if not (
                value.outcome == "persisted_failure"
                and value.workflow_id == workflow.id == result.workflow_id
                and value.current_step_id == step.id
                and value.current_step_index == index
                and value.current_employee_id == step.employee
                and type(value.failure_category) is str
                and value.failure_category in _FAILURE_CATEGORIES
            ):
                return False
            expected_status = "failed"
            failure_category = value.failure_category
        else:
            return False
        return _valid_terminal_snapshot(
            workflow, index, expected_status, state, events, failure_category
        )
    except Exception:
        return False


def _valid_terminal_snapshot(
    workflow: WorkflowDefinition,
    index: int,
    status: Literal["succeeded", "failed"],
    state: Path,
    events: Path,
    failure_category: object = None,
) -> bool:
    try:
        loaded = load_workflow_execution_state(state)
        lines = [line for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
        if type(loaded) is not WorkflowExecutionState or len(lines) != index:
            return False
        step = workflow.steps[index - 1]
        expected_completed = (
            tuple(item.id for item in workflow.steps[:index])
            if status == "succeeded"
            else tuple(item.id for item in workflow.steps[: index - 1])
        )
        if (
            loaded.workflow_id != workflow.id
            or loaded.status != status
            or loaded.current_step_id != step.id
            or loaded.current_step_index != index
            or loaded.current_employee_id != step.employee
            or loaded.completed_step_ids != expected_completed
            or loaded.last_failure_category != failure_category
        ):
            return False
        for position, line in enumerate(lines, start=1):
            event = RuntimeStepEvent(**json.loads(line))
            if position < index:
                valid = _valid_succeeded_event(event, workflow, position)
            else:
                valid = _valid_terminal_event(
                    event, workflow, position, status, failure_category
                )
            if not valid:
                return False
        return True
    except Exception:
        return False


def _valid_succeeded_event(
    event: object, workflow: WorkflowDefinition, position: int
) -> bool:
    if type(event) is not RuntimeStepEvent:
        return False
    step = workflow.steps[position - 1]
    return (
        event.event_type == "step_succeeded"
        and event.workflow_id == workflow.id
        and event.step_id == step.id
        and type(event.step_index) is int
        and event.step_index == position
        and event.employee_id == step.employee
        and event.previous_status == "running"
        and event.next_status == "succeeded"
        and event.provider == "openai"
        and event.failure_category is None
        and type(event.response_id) is str
        and event.response_id != ""
        and (
            event.request_id is None
            or (type(event.request_id) is str and event.request_id != "")
        )
        and type(event.output_text) is str
        and event.message is None
    )


def _valid_terminal_event(
    event: object,
    workflow: WorkflowDefinition,
    position: int,
    status: Literal["succeeded", "failed"],
    failure_category: object,
) -> bool:
    if type(event) is not RuntimeStepEvent:
        return False
    step = workflow.steps[position - 1]
    base = (
        event.workflow_id == workflow.id
        and event.step_id == step.id
        and type(event.step_index) is int
        and event.step_index == position
        and event.employee_id == step.employee
        and event.previous_status == "running"
        and event.provider == "openai"
        and (
            event.request_id is None
            or (type(event.request_id) is str and event.request_id != "")
        )
    )
    if status == "succeeded":
        return (
            base
            and event.event_type == "step_succeeded"
            and event.next_status == "succeeded"
            and event.failure_category is None
            and type(event.response_id) is str
            and event.response_id != ""
            and type(event.output_text) is str
            and event.message is None
        )
    return (
        base
        and event.event_type == "step_failed"
        and event.next_status == "failed"
        and type(failure_category) is str
        and failure_category in _FAILURE_CATEGORIES
        and event.failure_category == failure_category
        and event.response_id is None
        and event.output_text is None
        and type(event.message) is str
        and event.message != ""
    )


def _valid_phase172_output(
    value: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    workflow: WorkflowDefinition,
    progressed: object,
    state: Path,
    events: Path,
    committed: tuple[bytes, bytes],
) -> bool:
    index = value.step_index
    if type(index) is not int or not 1 <= index <= len(workflow.steps):
        return False
    if type(value) is StepRuntimeExecutionSuccess:
        if not _valid_success_progression(value, workflow, progressed):
            return False
    elif not _valid_failure_progression(value, progressed):
        return False
    try:
        loaded = load_workflow_execution_state(state)
        event_bytes = events.read_bytes()
        if type(loaded) is not WorkflowExecutionState:
            return False
        if not event_bytes.startswith(committed[1]):
            return False
        appended = event_bytes[len(committed[1]) :]
        terminal = RuntimeStepEvent(**json.loads(appended))
        if appended != serialize_runtime_step_event_jsonl(terminal).encode("utf-8"):
            return False
        invocation = value.invocation_result
        base = (
            loaded.workflow_id == workflow.id
            and loaded.current_step_id == value.step_id
            and loaded.current_step_index == index
            and loaded.current_employee_id == value.employee_id
            and terminal.workflow_id == value.workflow_id
            and terminal.step_id == value.step_id
            and terminal.step_index == index
            and terminal.employee_id == value.employee_id
            and terminal.previous_status == "running"
            and terminal.provider == invocation.provider
            and terminal.request_id == invocation.request_id
        )
        if type(value) is StepRuntimeExecutionSuccess:
            state_ok = (
                loaded.status == "succeeded"
                and loaded.completed_step_ids
                == tuple(step.id for step in workflow.steps[:index])
                and loaded.last_failure_category is None
            )
            event_ok = (
                terminal.event_type == "step_succeeded"
                and terminal.next_status == "succeeded"
                and terminal.failure_category is None
                and terminal.response_id == invocation.response_id
                and terminal.output_text == invocation.text
                and terminal.message is None
            )
        else:
            state_ok = (
                loaded.status == "failed"
                and loaded.completed_step_ids
                == tuple(step.id for step in workflow.steps[: index - 1])
                and loaded.last_failure_category == invocation.category
            )
            event_ok = (
                terminal.event_type == "step_failed"
                and terminal.next_status == "failed"
                and terminal.failure_category == invocation.category
                and terminal.response_id is None
                and terminal.output_text is None
                and terminal.message == invocation.message
            )
        return base and state_ok and event_ok
    except Exception:
        return False


def _valid_success_progression(
    result: StepRuntimeExecutionSuccess,
    workflow: WorkflowDefinition,
    progressed: object,
) -> bool:
    if type(progressed) is not WorkflowProgressionDecision:
        return False
    index = result.step_index
    if not (
        progressed.workflow_id == result.workflow_id
        and progressed.current_step_id == result.step_id
        and progressed.current_step_index == index
        and progressed.current_employee_id == result.employee_id
    ):
        return False
    if index == len(workflow.steps):
        return (
            progressed.decision == "workflow_complete"
            and progressed.next_step_id is None
            and progressed.next_step_index is None
            and progressed.next_employee_id is None
            and progressed.reason == "last_step_succeeded"
        )
    next_step = workflow.steps[index]
    return (
        progressed.decision == "prepare_next_step"
        and progressed.next_step_id == next_step.id
        and progressed.next_step_index == index + 1
        and progressed.next_employee_id == next_step.employee
        and progressed.reason == "next_step_available"
    )


def _valid_failure_progression(
    result: StepRuntimeExecutionFailure,
    progressed: object,
) -> bool:
    return (
        type(progressed) is PersistedExecutionOutcome
        and progressed is not result
        and progressed.outcome == "persisted_failure"
        and progressed.workflow_id == result.workflow_id
        and progressed.current_step_id == result.step_id
        and progressed.current_step_index == result.step_index
        and progressed.current_employee_id == result.employee_id
        and progressed.failure_category == result.invocation_result.category
    )


def _capture_targets(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _fail("dependency_error")


def _same_targets(state: Path, events: Path, original: tuple[bytes, bytes]) -> bool:
    try:
        return state.is_file() and events.is_file() and (
            state.read_bytes() == original[0] and events.read_bytes() == original[1]
        )
    except OSError:
        return False


def _fail(classification: Classification) -> None:
    raise RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
