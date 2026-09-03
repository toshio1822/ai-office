"""Phase 208 explicit fresh workflow step-1 bootstrap boundary.

This module implements the first authoritative public production owner for
starting a brand-new workflow at step 1 exactly once from nonexistent durable
targets.  Phase 207 / Issue #439 re-proved on repaired main that every lower
public owner is fresh-step-1 compatible; this boundary composes only those
existing owners in a fixed stage order and stops after the exact Phase-172
outer result.

Stage order owned by this boundary::

    validate pre-initialization configuration
        -> create canonical ready state + empty event log as a pair
        -> strict loadback validation
        -> DURABLE READY COMMIT
        -> validate explicit step1 preparation approval + employee
        -> build exact PreparedWorkflowStep(step1)        [pure]
        -> build exact PreparedStepExecutionStart(step1)  [pure]
        -> persist_prepared_running_state exactly once
        -> validate accepted running commit
        -> DURABLE RUNNING COMMIT
        -> execute_persisted_start_openai_step exactly once
        -> exact StepRuntimeExecutionSuccess | Failure
        -> Phase172 exactly once
        -> prepare_next_step(step2) | workflow_complete | persisted_failure
        -> STOP

Phase 208 never calls Phase 190 or Phase 192 internally.  A later bounded
Phase-192 continuation remains a separate caller action.
"""

# ruff: noqa: E501,E701,I001

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import (
    WorkflowDefinition,
    WorkflowStepDefinition,
)
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationError as Phase144Error,
)
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationError as Phase143Error,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase161Error,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError as Phase172CompatibilityError,
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationRequest,
)
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    is_valid_step_runtime_execution_result,
)
from ai_office.runtime.persisted_start_execution import (
    PersistedStartExecutionCompatibilityError,
    PersistedStartExecutionError,
    execute_persisted_start_openai_step,
)
from ai_office.storage import (
    RunningStatePersistenceError,
    RunningStatePersistenceInputError,
    RunningStatePersistenceRollbackError,
    RunningStatePersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    parse_runtime_step_event,
    persist_prepared_running_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.storage.workflow_execution_history import LoadedWorkflowExecutionHistory
from ai_office.tools import ToolDefinition

FreshWorkflowBootstrapClassification = Literal[
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "target_exists",
    "context_type",
    "configuration",
    "initialization_contract",
    "preparation_approval",
    "employee_contract",
    "running_persistence_contract",
    "execution_contract",
    "phase172_contract",
    "dependency_error",
    "rollback_failure",
]
_PATH_TYPE = type(Path())
_SAFE_EXECUTION_ERRORS = (
    PersistedStartExecutionError,
    PersistedStartExecutionCompatibilityError,
)
_SAFE_RUNNING_PERSISTENCE_ERRORS = (
    RunningStatePersistenceError,
    RunningStatePersistenceInputError,
    RunningStatePersistenceRollbackError,
)
_SAFE_PHASE172_ERRORS = (
    Phase172Error,
    Phase172CompatibilityError,
    Phase161Error,
    Phase143Error,
    Phase144Error,
)
_READY_EMPTY_EVENTS = b""


@dataclass(frozen=True)
class InitialStepPreparationApproval:
    """Explicit step-1 preparation approval for one brand-new workflow."""

    approved: bool
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str


@dataclass(frozen=True)
class ApprovedWorkflowBootstrapContext:
    """The six caller-supplied values required by one fresh bootstrap."""

    preparation_approval: InitialStepPreparationApproval
    employee: EmployeeDefinition
    resolved_tools: tuple[ToolDefinition, ...]
    api_key: OpenAIApiKey
    execution_approval: ModelInvocationExecutionApproval
    transport: object


@dataclass(frozen=True)
class FreshWorkflowBootstrapFailureDetail:
    """Safe classification for one fresh bootstrap failure."""

    classification: FreshWorkflowBootstrapClassification


class FreshWorkflowBootstrapError(ValueError):
    """Base error for the fresh workflow step-1 bootstrap boundary."""


class FreshWorkflowBootstrapCompatibilityError(FreshWorkflowBootstrapError):
    """Raised when a fresh step-1 bootstrap cannot safely complete."""

    def __init__(
        self, classification: FreshWorkflowBootstrapClassification
    ) -> None:
        super().__init__(
            "approved workflow fresh-start inputs are incompatible"
        )
        self.detail = FreshWorkflowBootstrapFailureDetail(classification)


def route_approved_workflow_fresh_start(
    workflow: object,
    state_path: object,
    events_path: object,
    context: object,
    *,
    running_persistence_function: Callable[..., object] = persist_prepared_running_state,
    execution_function: Callable[..., object] = execute_persisted_start_openai_step,
    phase172_function: Callable[..., object] = (
        route_runtime_result_to_progression_orchestration_boundary
    ),
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Start one brand-new workflow at step 1 exactly once and then stop.

    The caller supplies two nonexistent targets and an explicit
    :class:`ApprovedWorkflowBootstrapContext`.  This boundary creates the
    canonical ready state plus empty event log, strictly load-verifies them,
    and treats that pair as the first durable commit.  It then validates the
    explicit step-1 preparation approval and employee, builds the exact
    prepared step-1 models, persists the running state once, executes the
    persisted start exactly once through the current public execution owner,
    and delegates the exact runtime result to Phase 172 exactly once.  The
    exact Phase-172 outer result is returned by identity and the boundary
    stops: Phase 190 / Phase 192 are never called here.
    """
    _check_initial_inputs(
        workflow,
        state_path,
        events_path,
        context,
        running_persistence_function,
        execution_function,
        phase172_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    assert type(context) is ApprovedWorkflowBootstrapContext

    first_step = workflow.steps[0]
    ready_state = WorkflowExecutionState(
        workflow_id=workflow.id,
        status="ready",
        current_step_id=first_step.id,
        current_step_index=1,
        current_employee_id=first_step.employee,
        completed_step_ids=(),
        last_failure_category=None,
    )
    ready_bytes = serialize_workflow_execution_state_json(ready_state).encode(
        "utf-8"
    )

    created_targets = _create_pair(
        state_path, events_path, ready_bytes, _READY_EMPTY_EVENTS
    )
    if type(created_targets) is not tuple:
        # Keep ownership conservative for an injected private pair seam that
        # does not return the normal marker.
        created_targets = (state_path.exists(), events_path.exists())
    _loadback_accept_ready(
        workflow,
        ready_bytes,
        state_path,
        events_path,
        created_targets,
    )

    # From this point onward the ready pair is the first durable commit and is
    # never removed by later approval/employee/preparation errors.
    _validate_approval(context.preparation_approval, workflow)
    _validate_employee(context.employee, workflow, first_step)
    prepared_step = _build_prepared_step(workflow, first_step, context.employee)
    prepared_start = _build_prepared_start(
        workflow, prepared_step, context.employee
    )

    ready_snapshot = _capture(state_path, events_path)
    try:
        persisted = running_persistence_function(prepared_start, state_path)
    except _SAFE_RUNNING_PERSISTENCE_ERRORS as error:
        _restore_or_fail(state_path, events_path, ready_snapshot)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, ready_snapshot)
        _fail("dependency_error")
    if not _valid_running_commit(
        persisted, prepared_start, state_path, events_path, ready_snapshot
    ):
        _restore_or_fail(state_path, events_path, ready_snapshot)
        _fail("running_persistence_contract")

    # The running step-1 state is now the second durable commit.  From this
    # point onward it is never rolled back to the ready pair.
    running_snapshot = _capture(state_path, events_path)
    try:
        runtime_result = execution_function(
            prepared_start,
            state_path,
            workflow,
            context.employee,
            context.resolved_tools,
            context.api_key,
            context.execution_approval,
            transport=context.transport,
        )
    except _SAFE_EXECUTION_ERRORS as error:
        _restore_or_fail(state_path, events_path, running_snapshot)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, running_snapshot)
        _fail("dependency_error")
    try:
        execution_mutated = _changed(state_path, running_snapshot[0]) or _changed(
            events_path, running_snapshot[1]
        )
    except Exception:
        execution_mutated = True
    if execution_mutated:
        _restore_or_fail(state_path, events_path, running_snapshot)
        _fail("execution_contract")
    if not _valid_runtime_result(runtime_result, prepared_start, workflow):
        _restore_or_fail(state_path, events_path, running_snapshot)
        _fail("execution_contract")

    # Phase 172 owns the terminal durable commit.  Once it is invoked, no
    # outer restoration to the running/ready/nonexistent bytes is permitted.
    assert type(runtime_result) in (
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
    )
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
        progressed, runtime_result, workflow, state_path, events_path
    ):
        _fail("phase172_contract")
    return progressed


def _check_initial_inputs(
    workflow: object,
    state_path: object,
    events_path: object,
    context: object,
    running_persistence: object,
    execution: object,
    phase172: object,
) -> None:
    """Validate everything before any durable target is created."""
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _fail("workflow_definition")
    if type(state_path) is not _PATH_TYPE:
        _fail("state_target")
    if type(events_path) is not _PATH_TYPE:
        _fail("event_target")
    if state_path == events_path:
        _fail("target_conflict")
    if type(context) is not ApprovedWorkflowBootstrapContext:
        _fail("context_type")
    if not (
        callable(running_persistence)
        and callable(execution)
        and callable(phase172)
    ):
        _fail("configuration")
    try:
        if not state_path.parent.is_dir() or not events_path.parent.is_dir():
            _fail("state_target" if not state_path.parent.is_dir() else "event_target")
        if state_path.exists():
            _fail("target_exists")
        if events_path.exists():
            _fail("target_exists")
    except OSError:
        _fail("target_exists")


def _create_pair(
    state_path: Path,
    events_path: Path,
    state_bytes: bytes,
    event_bytes: bytes,
) -> tuple[bool, bool]:
    """Exclusively create both targets as one bootstrap-owned pair."""
    state_created = False
    events_created = False
    try:
        with state_path.open("xb") as handle:
            state_created = True
            handle.write(state_bytes)
            handle.flush()
        with events_path.open("xb") as handle:
            events_created = True
            handle.write(event_bytes)
            handle.flush()
    except FileExistsError:
        _remove_created_or_fail(
            state_path,
            events_path,
            (state_created, events_created),
        )
        _fail("target_exists")
    except OSError:
        _remove_created_or_fail(
            state_path,
            events_path,
            (state_created, events_created),
        )
        _fail("dependency_error")
    return state_created, events_created


def _remove_created_or_fail(
    state_path: Path,
    events_path: Path,
    created_targets: tuple[bool, bool],
) -> None:
    """Remove only the targets opened by this pair attempt."""
    failed = False
    for path, created in (
        (state_path, created_targets[0]),
        (events_path, created_targets[1]),
    ):
        if not created:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            failed = True
    for path, created in (
        (state_path, created_targets[0]),
        (events_path, created_targets[1]),
    ):
        if created and _lexists(path):
            failed = True
    if failed:
        _fail("rollback_failure")


def _compensate_created(
    state_path: Path,
    events_path: Path,
    created_targets: tuple[bool, bool],
) -> None:
    """Remove only bootstrap-created targets after a failed initialization."""
    failed = False
    for path, created in (
        (state_path, created_targets[0]),
        (events_path, created_targets[1]),
    ):
        if not created:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            failed = True
    for path, created in (
        (state_path, created_targets[0]),
        (events_path, created_targets[1]),
    ):
        if created and _lexists(path):
            failed = True
    if failed:
        _fail("rollback_failure")


def _loadback_accept_ready(
    workflow: WorkflowDefinition,
    ready_bytes: bytes,
    state_path: Path,
    events_path: Path,
    created_targets: tuple[bool, bool],
) -> None:
    """Strictly load and accept the durable ready commit."""
    try:
        state_bytes = state_path.read_bytes()
        event_bytes = events_path.read_bytes()
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except Exception:
        _compensate_created(state_path, events_path, created_targets)
        _fail("initialization_contract")
    if (
        state_bytes != ready_bytes
        or event_bytes != _READY_EMPTY_EVENTS
        or not _valid_ready_history(workflow, history)
    ):
        _compensate_created(state_path, events_path, created_targets)
        _fail("initialization_contract")


def _valid_ready_history(
    workflow: WorkflowDefinition, history: object
) -> bool:
    if type(history) is not LoadedWorkflowExecutionHistory:
        return False
    state = history.state
    first_step = workflow.steps[0]
    return (
        type(state) is WorkflowExecutionState
        and state.workflow_id == workflow.id
        and state.status == "ready"
        and state.current_step_id == first_step.id
        and state.current_step_index == 1
        and state.current_employee_id == first_step.employee
        and state.completed_step_ids == ()
        and state.last_failure_category is None
        and history.events == ()
    )


def _validate_approval(
    approval: object, workflow: WorkflowDefinition
) -> None:
    if type(approval) is not InitialStepPreparationApproval:
        _fail("preparation_approval")
    assert type(approval) is InitialStepPreparationApproval
    first_step = workflow.steps[0]
    if not (
        approval.approved is True
        and type(approval.workflow_id) is str
        and bool(approval.workflow_id)
        and type(approval.step_id) is str
        and bool(approval.step_id)
        and type(approval.employee_id) is str
        and bool(approval.employee_id)
        and type(approval.step_index) is int
        and not isinstance(approval.step_index, bool)
        and approval.step_index == 1
        and approval.workflow_id == workflow.id
        and approval.step_id == first_step.id
        and approval.employee_id == first_step.employee
    ):
        _fail("preparation_approval")


def _validate_employee(
    employee: object,
    workflow: WorkflowDefinition,
    first_step: WorkflowStepDefinition,
) -> None:
    if type(employee) is not EmployeeDefinition:
        _fail("employee_contract")
    assert type(employee) is EmployeeDefinition
    if not (
        _nonempty(employee.id)
        and _nonempty(employee.name)
        and _nonempty(employee.role)
        and _nonempty(employee.instructions)
        and _nonempty(employee.model)
        and type(employee.allowed_tools) is list
        and all(_nonempty(item) for item in employee.allowed_tools)
        and len(employee.allowed_tools) == len(set(employee.allowed_tools))
        and employee.id == first_step.employee
    ):
        _fail("employee_contract")


def _build_prepared_step(
    workflow: WorkflowDefinition,
    first_step: WorkflowStepDefinition,
    employee: EmployeeDefinition,
) -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        workflow_id=workflow.id,
        step_id=first_step.id,
        step_index=1,
        employee_id=employee.id,
        employee_instructions=employee.instructions,
        step_instructions=first_step.instructions,
        model=employee.model,
        allowed_tool_names=tuple(employee.allowed_tools),
    )


def _build_prepared_start(
    workflow: WorkflowDefinition,
    prepared_step: PreparedWorkflowStep,
    employee: EmployeeDefinition,
) -> PreparedStepExecutionStart:
    del workflow
    request = ModelInvocationRequest(
        model=prepared_step.model,
        system_instructions=prepared_step.employee_instructions,
        task_instructions=prepared_step.step_instructions,
        allowed_tools=tuple(prepared_step.allowed_tool_names),
    )
    running_state = WorkflowExecutionState(
        workflow_id=prepared_step.workflow_id,
        status="running",
        current_step_id=prepared_step.step_id,
        current_step_index=1,
        current_employee_id=employee.id,
        completed_step_ids=(),
        last_failure_category=None,
    )
    return PreparedStepExecutionStart(request, running_state)


def _valid_running_commit(
    value: object,
    prepared_start: PreparedStepExecutionStart,
    state_path: Path,
    events_path: Path,
    ready_snapshot: tuple[bytes, bytes],
) -> bool:
    """Accept the new running commit only under the exact postconditions."""
    expected = serialize_workflow_execution_state_json(
        prepared_start.running_state
    ).encode("utf-8")
    try:
        state_bytes = state_path.read_bytes()
        event_bytes = events_path.read_bytes()
        loaded = load_workflow_execution_state(state_path)
    except Exception:
        return False
    if (
        type(value) is not RunningStatePersistenceResult
        or type(value.state_bytes_written) is not int
        or isinstance(value.state_bytes_written, bool)
        or value.state_bytes_written <= 0
        or value.state_bytes_written != len(expected)
        or state_bytes != expected
        or event_bytes != ready_snapshot[1]
        or type(loaded) is not WorkflowExecutionState
        or loaded != prepared_start.running_state
    ):
        return False
    return True


def _valid_runtime_result(
    value: object,
    prepared_start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
) -> bool:
    if type(value) not in (
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
    ):
        return False
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
    return valid


def _valid_phase172_result(
    value: object,
    runtime_result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> bool:
    """Validate the exact Phase-172 outer result without rolling it back."""
    if type(value) not in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return False
    index = runtime_result.step_index
    if type(index) is not int or not 1 <= index <= len(workflow.steps):
        return False
    step = workflow.steps[index - 1]
    if not (
        _nonempty(value.workflow_id)
        and value.workflow_id == workflow.id
        and _nonempty(value.current_step_id)
        and value.current_step_id == step.id
        and type(value.current_step_index) is int
        and not isinstance(value.current_step_index, bool)
        and value.current_step_index == index
        and _nonempty(value.current_employee_id)
        and value.current_employee_id == step.employee
    ):
        return False

    if type(runtime_result) is StepRuntimeExecutionFailure:
        result_ok = (
            type(value) is PersistedExecutionOutcome
            and _exact(value.outcome, "persisted_failure")
            and type(value.failure_category) is str
            and value.failure_category
            == runtime_result.invocation_result.category
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
            and _nonempty(value.next_step_id)
            and value.next_step_id == next_step.id
            and type(value.next_step_index) is int
            and not isinstance(value.next_step_index, bool)
            and value.next_step_index == index + 1
            and _nonempty(value.next_employee_id)
            and value.next_employee_id == next_step.employee
            and _exact(value.reason, "next_step_available")
        )
    if not result_ok:
        return False
    return _valid_phase172_persistence(
        value, runtime_result, workflow, state_path, events_path
    )


def _valid_phase172_persistence(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
    runtime_result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> bool:
    """Verify the Phase-172 terminal state and its one appended event."""
    try:
        state_bytes = state_path.read_bytes()
        event_bytes = events_path.read_bytes()
        if not event_bytes or not event_bytes.endswith(b"\n"):
            return False
        if event_bytes.count(b"\n") != 1:
            return False
        event = parse_runtime_step_event(json.loads(event_bytes.decode("utf-8")))
        if type(event) is not RuntimeStepEvent:
            return False
        if serialize_runtime_step_event_jsonl(event).encode("utf-8") != event_bytes:
            return False
        state = load_workflow_execution_state(state_path)
        if state_bytes != serialize_workflow_execution_state_json(state).encode(
            "utf-8"
        ):
            return False
    except Exception:
        return False

    step = workflow.steps[runtime_result.step_index - 1]
    invocation = runtime_result.invocation_result
    common = (
        type(state) is WorkflowExecutionState
        and type(state.workflow_id) is str
        and state.workflow_id == workflow.id
        and state.current_step_id == step.id
        and type(state.current_step_index) is int
        and state.current_step_index == runtime_result.step_index
        and state.current_employee_id == step.employee
        and type(event.workflow_id) is str
        and event.workflow_id == workflow.id
        and event.step_id == step.id
        and type(event.step_index) is int
        and event.step_index == runtime_result.step_index
        and event.employee_id == step.employee
        and event.previous_status == "running"
        and event.provider == invocation.provider
        and event.request_id == invocation.request_id
    )
    if not common:
        return False
    if type(runtime_result) is StepRuntimeExecutionSuccess:
        return (
            type(value) is WorkflowProgressionDecision
            and state.status == "succeeded"
            and type(state.completed_step_ids) is tuple
            and state.completed_step_ids
            == tuple(item.id for item in workflow.steps[: runtime_result.step_index])
            and state.last_failure_category is None
            and event.event_type == "step_succeeded"
            and event.next_status == "succeeded"
            and event.failure_category is None
            and event.response_id == invocation.response_id
            and event.output_text == invocation.text
            and event.message is None
        )
    return (
        type(value) is PersistedExecutionOutcome
        and state.status == "failed"
        and type(state.completed_step_ids) is tuple
        and state.completed_step_ids
        == tuple(item.id for item in workflow.steps[: runtime_result.step_index - 1])
        and state.last_failure_category == invocation.category
        and event.event_type == "step_failed"
        and event.next_status == "failed"
        and event.failure_category == invocation.category
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


def _capture(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        return state_path.read_bytes(), events_path.read_bytes()
    except OSError:
        _fail("dependency_error")


def _restore_or_fail(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
) -> None:
    try:
        changed = _changed(state_path, original[0]) or _changed(
            events_path, original[1]
        )
    except OSError:
        changed = True
    if not changed:
        return
    failed = False
    for path, contents in ((state_path, original[0]), (events_path, original[1])):
        try:
            path.write_bytes(contents)
        except Exception:
            failed = True
    try:
        if _changed(state_path, original[0]) or _changed(
            events_path, original[1]
        ):
            failed = True
    except Exception:
        failed = True
    if failed:
        _fail("rollback_failure")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _lexists(path: Path) -> bool:
    """Return whether a directory entry exists, including a broken symlink."""
    try:
        return os.path.lexists(path)
    except OSError:
        return True


def _nonempty(value: object) -> bool:
    return type(value) is str and bool(value)


def _exact(value: object, expected: object) -> bool:
    return type(value) is type(expected) and value == expected


def _fail(classification: FreshWorkflowBootstrapClassification) -> None:
    raise FreshWorkflowBootstrapCompatibilityError(classification) from None


__all__ = [
    "ApprovedWorkflowBootstrapContext",
    "FreshWorkflowBootstrapClassification",
    "FreshWorkflowBootstrapCompatibilityError",
    "FreshWorkflowBootstrapError",
    "FreshWorkflowBootstrapFailureDetail",
    "InitialStepPreparationApproval",
    "route_approved_workflow_fresh_start",
]
