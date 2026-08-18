"""Phase 178 post-runtime → persisted running execution → progression orchestration boundary."""

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
)
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError as Phase173Error,
)
from ai_office.engine.runtime_result_to_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryError as Phase176Error,
)
from ai_office.engine.runtime_result_to_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPreparedStepStartOrchestrationBoundaryError as Phase175Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase177CompatibilityError,
    route_runtime_result_to_persisted_running_execution_orchestration_boundary,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError as Phase172CompatibilityError,
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
    route_runtime_result_to_progression_orchestration_boundary,
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
    is_valid_step_runtime_execution_result,
)
from ai_office.storage import (
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
    "phase177_contract",
    "phase172_contract",
    "dependency_error",
]
Phase177Function = Callable[
    [object, object, object, object, object, object, object, object, object, object],
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]
Phase172Function = Callable[
    [object, object, object, object],
    WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 178 orchestration failure."""

    classification: Classification


class RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryError(
    ValueError
):
    """Base error for the Phase 178 boundary."""


class RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError(
    RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryError
):
    """Raised when the Phase-177 → Phase-172 orchestration cannot safely complete."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime persisted running-execution progression orchestration "
            "inputs are incompatible"
        )
        self.detail = (
            RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail(
                classification
            )
        )


def route_runtime_result_to_persisted_running_execution_progression_orchestration_boundary(
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
    phase177_function: Phase177Function = (
        route_runtime_result_to_persisted_running_execution_orchestration_boundary
    ),
    phase172_function: Phase172Function = (
        route_runtime_result_to_progression_orchestration_boundary
    ),
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Compose public Phase 177 then public Phase 172 exactly once each.

    Phase 177 owns exactly one next-step runtime execution (or the exact
    stop-object pass-through) through the real Phase 176 → 147 → 155 chain;
    Phase 178 returns the exact stop object by identity with Phase 172 zero
    calls, or passes the exact Phase-177 runtime result to public Phase 172
    exactly once, then thinly proves the final durable target effect and the
    exact progression object without reimplementing Phase 161 / 143 / 144 in
    full.  Phase 178 performs no outer rollback across the Phase-177 or
    Phase-172 durable ownership points, writes no targets itself, and never
    retries, loops, finalizes beyond the returned decision, schedules,
    parallelizes, or adds CLI/GUI behavior.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase177_function,
        phase172_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    # preparation_approval / employee / resolved_tools / api_key /
    # execution_approval / transport are deliberately not prevalidated:
    # Phase 177 and Phase 172 remain authoritative for their own input
    # validation after the durable running-state commit.
    _check_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)

    try:
        value = phase177_function(
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
        Phase155Error,
        Phase141Error,
        Phase177CompatibilityError,
    ) as error:
        # Exact identity re-raise; Phase 172 zero calls; no outer rollback.
        raise error
    except Exception:
        _fail("dependency_error")

    if type(value) in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        if value is not result:
            _fail("phase177_contract")
        _require_unchanged(state_path, events_path, original, "phase177_contract")
        return value

    running = _valid_phase177_runtime_output(
        result, workflow, value, state_path, events_path
    )
    if running is None:
        _fail("phase177_contract")

    try:
        progressed = phase172_function(value, workflow, state_path, events_path)
    except (
        Phase161Error,
        Phase143Error,
        Phase144Error,
        Phase172CompatibilityError,
    ) as error:
        # Exact identity re-raise; no outer rollback.
        raise error
    except Exception:
        _fail("dependency_error")

    if not _valid_phase172_output(
        value, workflow, progressed, state_path, events_path, running
    ):
        _fail("phase172_contract")
    return progressed


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase177: object,
    phase172: object,
) -> None:
    # The stop input domain is narrowed before Phase 177: only an exact
    # workflow_complete decision and an exact persisted_failure outcome are
    # stop inputs; a prepare_next_step decision or a persisted_success
    # outcome is rejected here as result_type.
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
    if not (callable(phase177) and callable(phase172)):
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


def _valid_phase177_runtime_output(
    result: object,
    workflow: WorkflowDefinition,
    value: object,
    state_path: Path,
    events_path: Path,
) -> tuple[WorkflowExecutionState, bytes] | None:
    """Thin Phase-177 runtime output linked to the post-Phase177 running snapshot.

    The Phase-177 runtime output must be an exact
    ``StepRuntimeExecutionSuccess`` / ``StepRuntimeExecutionFailure`` whose
    identity matches the post-Phase177 running state (status ``running``
    with the exact workflow id and the exact current step / index /
    employee, a completed-step-id prefix that is exactly
    ``workflow.steps[: step_index - 1]``, and ``last_failure_category``
    ``None``), with committed predecessor history of exactly
    ``step_index - 1`` workflow-linked succeeded terminal events.  Phase
    177's public runtime-result validator semantics remain authoritative
    for the invocation contract; no lower provider parser is duplicated
    here.
    """
    if type(value) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        return None
    try:
        loaded = load_workflow_execution_state(state_path)
        event_bytes = events_path.read_bytes()
    except Exception:
        return None
    if type(loaded) is not WorkflowExecutionState or loaded.status != "running":
        return None
    if (
        type(loaded.current_step_index) is not int
        or loaded.current_step_id != value.step_id
        or loaded.current_step_index != value.step_index
        or loaded.current_employee_id != value.employee_id
    ):
        return None
    try:
        if not is_valid_step_runtime_execution_result(
            value,
            workflow_id=workflow.id,
            step_id=loaded.current_step_id,
            step_index=loaded.current_step_index,
            employee_id=loaded.current_employee_id,
        ):
            return None
    except Exception:
        return None
    if type(value.step_index) is not int or not 1 <= value.step_index <= len(
        workflow.steps
    ):
        return None
    # Thin running-snapshot contract: exact workflow id, an exact
    # completed-step-id prefix (the step ids before the executed step), and
    # no active failure category for a running state.
    expected_completed = tuple(
        step.id for step in workflow.steps[: value.step_index - 1]
    )
    if (
        loaded.workflow_id != workflow.id
        or type(loaded.completed_step_ids) is not tuple
        or loaded.completed_step_ids != expected_completed
        or loaded.last_failure_category is not None
    ):
        return None
    try:
        lines = [
            line
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError):
        return None
    if len(lines) != value.step_index - 1:
        return None
    for position, line in enumerate(lines, start=1):
        try:
            event = RuntimeStepEvent(**json.loads(line))
        except Exception:
            return None
        step = workflow.steps[position - 1]
        # Each predecessor must be a workflow-linked succeeded terminal
        # event for the exact workflow step at this position.
        if (
            event.workflow_id != workflow.id
            or event.step_id != step.id
            or type(event.step_index) is not int
            or event.step_index != position
            or event.employee_id != step.employee
            or event.previous_status != "running"
            or event.event_type != "step_succeeded"
            or event.next_status != "succeeded"
            or event.failure_category is not None
        ):
            return None
    return loaded, event_bytes


def _valid_phase172_output(
    value: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    workflow: WorkflowDefinition,
    progressed: object,
    state_path: Path,
    events_path: Path,
    running: tuple[WorkflowExecutionState, bytes],
) -> bool:
    """Thin Phase-172 output and final durable-target proof.

    The post-Phase177 running snapshot must be preserved byte-for-byte in the
    predecessor event bytes with exactly one terminal event appended for the
    executed step; the final state and appended event must be linked to the
    exact Phase-177 runtime result and workflow; the progression object must
    be the exact ``prepare_next_step`` / ``workflow_complete`` /
    ``persisted_failure`` expected for the runtime route.  Phase 161's full
    terminal-history validator is not duplicated here.
    """
    running_state, running_event_bytes = running
    invocation = value.invocation_result
    successful = type(value) is StepRuntimeExecutionSuccess
    if successful:
        if not _valid_success_progression(value, workflow, progressed):
            return False
    else:
        if not _valid_failure_progression(value, progressed):
            return False
    try:
        loaded_state = load_workflow_execution_state(state_path)
        event_bytes = events_path.read_bytes()
    except Exception:
        return False
    if type(loaded_state) is not WorkflowExecutionState:
        return False
    if not event_bytes.startswith(running_event_bytes):
        return False
    appended = event_bytes[len(running_event_bytes) :]
    if not appended:
        return False
    try:
        terminal = RuntimeStepEvent(**json.loads(appended))
    except Exception:
        return False
    if appended != serialize_runtime_step_event_jsonl(terminal).encode("utf-8"):
        return False
    step_index = value.step_index
    if type(step_index) is not int or not 1 <= step_index <= len(workflow.steps):
        return False
    if successful:
        state_ok = (
            loaded_state.status == "succeeded"
            and type(loaded_state.completed_step_ids) is tuple
            and loaded_state.completed_step_ids
            == running_state.completed_step_ids + (running_state.current_step_id,)
            and loaded_state.last_failure_category is None
        )
    else:
        state_ok = (
            loaded_state.status == "failed"
            and loaded_state.completed_step_ids == running_state.completed_step_ids
            and loaded_state.last_failure_category == invocation.category
        )
    base = (
        loaded_state.workflow_id == workflow.id
        and loaded_state.current_step_id == value.step_id
        and loaded_state.current_step_index == step_index
        and loaded_state.current_employee_id == value.employee_id
        and terminal.workflow_id == value.workflow_id
        and terminal.step_id == value.step_id
        and type(terminal.step_index) is int
        and terminal.step_index == value.step_index
        and terminal.employee_id == value.employee_id
        and terminal.previous_status == "running"
        and terminal.provider == "openai"
        and terminal.request_id == invocation.request_id
    )
    if successful:
        details = (
            terminal.event_type == "step_succeeded"
            and terminal.next_status == "succeeded"
            and terminal.failure_category is None
            and terminal.response_id == invocation.response_id
            and terminal.output_text == invocation.text
            and terminal.message is None
        )
    else:
        details = (
            terminal.event_type == "step_failed"
            and terminal.next_status == "failed"
            and terminal.failure_category == invocation.category
            and terminal.response_id is None
            and terminal.output_text is None
            and terminal.message == invocation.message
        )
    return state_ok and base and details


def _valid_success_progression(
    result: StepRuntimeExecutionSuccess,
    workflow: WorkflowDefinition,
    progressed: object,
) -> bool:
    """Exact success progression: prepare_next_step or workflow_complete."""
    if type(progressed) is not WorkflowProgressionDecision:
        return False
    decision = progressed
    step_index = result.step_index
    if type(step_index) is not int or not 1 <= step_index <= len(workflow.steps):
        return False
    if not (
        decision.workflow_id == result.workflow_id
        and decision.current_step_id == result.step_id
        and decision.current_step_index == step_index
        and decision.current_employee_id == result.employee_id
    ):
        return False
    if step_index == len(workflow.steps):
        return (
            decision.decision == "workflow_complete"
            and decision.next_step_id is None
            and decision.next_step_index is None
            and decision.next_employee_id is None
            and decision.reason == "last_step_succeeded"
        )
    next_step = workflow.steps[step_index]
    return (
        decision.decision == "prepare_next_step"
        and decision.next_step_id == next_step.id
        and decision.next_step_index == step_index + 1
        and decision.next_employee_id == next_step.employee
        and decision.reason == "next_step_available"
    )


def _valid_failure_progression(
    result: StepRuntimeExecutionFailure,
    progressed: object,
) -> bool:
    """Exact failure progression: persisted_failure with the exact category."""
    if type(progressed) is not PersistedExecutionOutcome:
        return False
    return (
        progressed.outcome == "persisted_failure"
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


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _fail(classification)


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _fail(classification: Classification) -> None:
    raise RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
