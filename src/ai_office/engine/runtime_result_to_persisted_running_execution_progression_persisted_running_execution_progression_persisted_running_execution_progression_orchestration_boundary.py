"""Phase 188: Phase 187 step-9 runtime result -> Phase 172 progression."""

# ruff: noqa: E501,E701,I001

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
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
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationError as Phase137Error,
)
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError as Phase173Error,
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
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError as Phase179Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase178Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase182Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase184CompatibilityError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError as Phase184Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase186CompatibilityError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryError as Phase186Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError as Phase185Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase183Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase181Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryError as Phase180Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase187CompatibilityError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryError as Phase187Error,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary as route_phase187,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase161ChainError,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase161Error,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    is_valid_step_runtime_execution_result,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "configuration",
    "phase187_contract",
    "phase172_contract",
    "dependency_error",
]
Phase187Function = Callable[..., object]
Phase172Function = Callable[[object, object, object, object], object]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))

_SAFE_PHASE187_ERRORS = (
    Phase187Error,
    Phase187CompatibilityError,
    Phase186Error,
    Phase186CompatibilityError,
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
    Phase177Error,
    Phase161Error,
    Phase143Error,
    Phase144Error,
)
_SAFE_PHASE172_ERRORS = (
    Phase172Error,
    Phase172CompatibilityError,
    Phase161ChainError,
    Phase161Error,
    Phase143Error,
    Phase144Error,
)


@dataclass(frozen=True)
class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 188 orchestration failure."""

    classification: Classification


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryError(
    ValueError
):
    """Base error for the Phase 188 boundary."""


class RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryError
):
    """Raised for a detail-safe Phase 188 contract failure."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime persisted-running execution progression orchestration inputs are incompatible"
        )
        self.detail = RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail(
            classification
        )


def route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_persisted_running_execution_progression_orchestration_boundary(
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
    phase187_function: Phase187Function = route_phase187,
    phase172_function: Phase172Function = route_runtime_result_to_progression_orchestration_boundary,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Compose public Phase 187 and public Phase 172 exactly once.

    Phase 187 owns every operation through the exact step-9 runtime result.
    Phase 172 owns persistence, classification, and progression of that
    runtime result. This boundary only validates each public seam and stops
    after the one Phase-172 call.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase187_function,
        phase172_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    _check_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)

    try:
        value = phase187_function(
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
            following_resolved_tools,
            following_api_key,
            following_execution_approval,
            following_transport,
        )
    except _SAFE_PHASE187_ERRORS as error:
        raise error
    except Exception:
        _fail("dependency_error")

    if type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        if value is not result or not _same_targets(state_path, events_path, original):
            _fail("phase187_contract")
        return value

    if type(value) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        if not _valid_phase187_stop(result, value, workflow, state_path, events_path):
            _fail("phase187_contract")
        return value

    if not _valid_phase187_runtime(
        result, value, workflow, state_path, events_path
    ):
        _fail("phase187_contract")
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
    phase187: object,
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
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _fail("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _fail("state_target")
    if type(events) is not _PATH_TYPE:
        _fail("event_target")
    if state == events:
        _fail("target_conflict")
    if not callable(phase187) or not callable(phase172):
        _fail("configuration")


def _check_targets(state: Path, events: Path) -> None:
    for path, classification in ((state, "state_target"), (events, "event_target")):
        try:
            if not path.is_file():
                _fail(classification)
        except OSError:
            _fail(classification)


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


def _valid_phase187_runtime(
    initial: object,
    value: object,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> bool:
    if (
        type(initial) is not StepRuntimeExecutionSuccess
        or type(value) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)
        or len(workflow.steps) < 9
    ):
        return False
    initial_index = initial.step_index
    if type(initial_index) is not int or not 1 <= initial_index <= len(workflow.steps):
        return False
    initial_step = workflow.steps[initial_index - 1]
    if not is_valid_step_runtime_execution_result(
        initial,
        workflow_id=workflow.id,
        step_id=initial_step.id,
        step_index=initial_index,
        employee_id=initial_step.employee,
    ):
        return False
    step = workflow.steps[8]
    if (
        type(value.step_index) is not int
        or value.step_index != initial_index + 3
        or value.step_index != 9
        or value.workflow_id != workflow.id
        or value.step_id != step.id
        or value.employee_id != step.employee
    ):
        return False
    try:
        loaded = load_workflow_execution_state(state)
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
        if (
            type(loaded) is not WorkflowExecutionState
            or loaded.workflow_id != workflow.id
            or loaded.status != "running"
            or loaded.current_step_id != step.id
            or loaded.current_step_index != 9
            or loaded.current_employee_id != step.employee
            or loaded.completed_step_ids
            != tuple(item.id for item in workflow.steps[:8])
            or loaded.last_failure_category is not None
            or type(history.events) is not tuple
            or len(history.events) != 8
            or not is_valid_step_runtime_execution_result(
                value,
                workflow_id=workflow.id,
                step_id=step.id,
                step_index=9,
                employee_id=step.employee,
            )
        ):
            return False
        return _valid_running_history(workflow, history.events)
    except Exception:
        return False


def _valid_running_history(
    workflow: WorkflowDefinition,
    events: object,
) -> bool:
    """Thinly validate the committed step-1..8 history without lower helpers."""
    if type(events) is not tuple or len(events) != 8:
        return False
    immediate_position = 8
    for position, event in enumerate(events, start=1):
        if type(event) is not RuntimeStepEvent:
            return False
        step = workflow.steps[position - 1]
        provider_valid = type(event.provider) is str and bool(event.provider)
        if position == immediate_position:
            provider_valid = provider_valid and event.provider == "openai"
        if event.request_id is None:
            provenance_valid = (
                provider_valid
                and event.provider == "openai"
                and (position == immediate_position or position >= 5)
            )
        else:
            provenance_valid = (
                provider_valid
                and type(event.request_id) is str
                and bool(event.request_id)
            )
        if not (
            event.event_type == "step_succeeded"
            and event.workflow_id == workflow.id
            and event.step_id == step.id
            and type(event.step_index) is int
            and event.step_index == position
            and event.employee_id == step.employee
            and event.previous_status == "running"
            and event.next_status == "succeeded"
            and provenance_valid
            and event.failure_category is None
            and type(event.response_id) is str
            and bool(event.response_id)
            and type(event.output_text) is str
            and event.message is None
        ):
            return False
    return True


def _valid_phase187_stop(
    result: object,
    value: object,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> bool:
    if type(result) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        return False
    if type(value) not in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return False
    try:
        original_index = result.step_index
        current_index = value.current_step_index
        if (
            type(original_index) is not int
            or type(current_index) is not int
            or current_index not in (original_index + 1, original_index + 2)
            or not 1 <= original_index < current_index <= len(workflow.steps)
        ):
            return False
        step = workflow.steps[current_index - 1]
        identity = (
            value.workflow_id == workflow.id == result.workflow_id
            and value.current_step_id == step.id
            and value.current_step_index == current_index
            and value.current_employee_id == step.employee
        )
        if type(value) is WorkflowProgressionDecision:
            return bool(
                identity
                and type(result) is StepRuntimeExecutionSuccess
                and value.decision == "workflow_complete"
                and current_index == len(workflow.steps)
                and value.next_step_id is None
                and value.next_step_index is None
                and value.next_employee_id is None
                and value.reason == "last_step_succeeded"
                and _valid_terminal_snapshot(
                    workflow, current_index, "succeeded", state, events
                )
            )
        return bool(
            identity
            and value.outcome == "persisted_failure"
            and type(value.failure_category) is str
            and value.failure_category in _FAILURE_CATEGORIES
            and _valid_terminal_snapshot(
                workflow,
                current_index,
                "failed",
                state,
                events,
                failure_category=value.failure_category,
            )
        )
    except Exception:
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
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
        if type(loaded) is not WorkflowExecutionState or type(history.events) is not tuple:
            return False
        if len(history.events) != executed:
            return False
        step = workflow.steps[executed - 1]
        expected_completed = (
            tuple(item.id for item in workflow.steps[:executed])
            if status == "succeeded"
            else tuple(item.id for item in workflow.steps[: executed - 1])
        )
        if not (
            loaded.workflow_id == workflow.id
            and loaded.status == status
            and loaded.current_step_id == step.id
            and loaded.current_step_index == executed
            and loaded.current_employee_id == step.employee
            and loaded.completed_step_ids == expected_completed
        ):
            return False
        if status == "succeeded" and loaded.last_failure_category is not None:
            return False
        if status == "failed" and loaded.last_failure_category != failure_category:
            return False
        for position, event in enumerate(history.events, start=1):
            if type(event) is not RuntimeStepEvent:
                return False
            expected_step = workflow.steps[position - 1]
            base = (
                event.workflow_id == workflow.id
                and event.step_id == expected_step.id
                and event.step_index == position
                and event.employee_id == expected_step.employee
                and event.previous_status == "running"
            )
            if position < executed:
                if not (
                    base
                    and event.event_type == "step_succeeded"
                    and event.next_status == "succeeded"
                    and event.failure_category is None
                    and type(event.response_id) is str
                    and bool(event.response_id)
                    and type(event.output_text) is str
                    and event.message is None
                ):
                    return False
            elif status == "succeeded":
                if not (
                    base
                    and event.event_type == "step_succeeded"
                    and event.next_status == "succeeded"
                    and event.failure_category is None
                    and type(event.response_id) is str
                    and bool(event.response_id)
                    and type(event.output_text) is str
                    and event.message is None
                ):
                    return False
            elif not (
                base
                and event.event_type == "step_failed"
                and event.next_status == "failed"
                and event.failure_category == failure_category
                and event.response_id is None
                and event.output_text is None
                and type(event.message) is str
                and bool(event.message)
            ):
                return False
        return True
    except Exception:
        return False


def _valid_phase172_output(
    value: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    workflow: WorkflowDefinition,
    progressed: object,
    state: Path,
    events: Path,
    committed: tuple[bytes, bytes],
) -> bool:
    index = value.step_index
    if type(index) is not int or index != 9 or value.workflow_id != workflow.id:
        return False
    if type(progressed) is WorkflowProgressionDecision:
        if not (
            progressed.workflow_id == value.workflow_id
            and progressed.current_step_id == value.step_id
            and progressed.current_step_index == index
            and progressed.current_employee_id == value.employee_id
        ):
            return False
        if len(workflow.steps) == 9:
            progression_ok = (
                progressed.decision == "workflow_complete"
                and progressed.next_step_id is None
                and progressed.next_step_index is None
                and progressed.next_employee_id is None
                and progressed.reason == "last_step_succeeded"
            )
        else:
            next_step = workflow.steps[9]
            progression_ok = (
                progressed.decision == "prepare_next_step"
                and progressed.next_step_id == next_step.id
                and progressed.next_step_index == 10
                and progressed.next_employee_id == next_step.employee
                and progressed.reason == "next_step_available"
            )
        if not progression_ok or type(value) is not StepRuntimeExecutionSuccess:
            return False
    elif type(progressed) is PersistedExecutionOutcome:
        if not (
            type(value) is StepRuntimeExecutionFailure
            and progressed.outcome == "persisted_failure"
            and progressed.workflow_id == value.workflow_id
            and progressed.current_step_id == value.step_id
            and progressed.current_step_index == index
            and progressed.current_employee_id == value.employee_id
            and progressed.failure_category == value.invocation_result.category
        ):
            return False
    else:
        return False
    try:
        loaded = load_workflow_execution_state(state)
        event_bytes = events.read_bytes()
        if type(loaded) is not WorkflowExecutionState or not event_bytes.startswith(committed[1]):
            return False
        appended = event_bytes[len(committed[1]) :]
        terminal = RuntimeStepEvent(**json.loads(appended))
        if appended != serialize_runtime_step_event_jsonl(terminal).encode("utf-8"):
            return False
        invocation = value.invocation_result
        base = (
            loaded.workflow_id == workflow.id
            and loaded.current_step_id == value.step_id
            and loaded.current_step_index == 9
            and loaded.current_employee_id == value.employee_id
            and terminal.workflow_id == value.workflow_id
            and terminal.step_id == value.step_id
            and terminal.step_index == 9
            and terminal.employee_id == value.employee_id
            and terminal.previous_status == "running"
            and terminal.provider == invocation.provider
            and terminal.request_id == invocation.request_id
        )
        if type(value) is StepRuntimeExecutionSuccess:
            state_ok = (
                loaded.status == "succeeded"
                and loaded.completed_step_ids
                == tuple(step.id for step in workflow.steps[:9])
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
                == tuple(step.id for step in workflow.steps[:8])
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


def _fail(classification: Classification) -> None:
    raise RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
