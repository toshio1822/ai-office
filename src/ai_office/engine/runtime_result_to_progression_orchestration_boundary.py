"""Phase 172 post-runtime persistence → classification → progression orchestration boundary."""

# ruff: noqa: E501,E701,I001

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationError as Phase144Error,
    route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationError as Phase143Error,
    route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase161Error,
    route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
)
from ai_office.storage import WorkflowExecutionPersistenceResult

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "configuration",
    "phase161_contract",
    "phase143_contract",
    "phase144_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
Phase161Function = Callable[
    [object, object, object, object],
    WorkflowExecutionPersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]
Phase143Function = Callable[
    [object, object, object, object],
    PersistedExecutionOutcome | WorkflowProgressionDecision,
]
Phase144Function = Callable[
    [object, object, object, object],
    WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class RuntimeResultToProgressionOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 172 orchestration failure."""

    classification: Classification


class RuntimeResultToProgressionOrchestrationBoundaryError(ValueError):
    """Base error for the Phase 172 boundary."""


class RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError(
    RuntimeResultToProgressionOrchestrationBoundaryError
):
    """Raised when a post-runtime orchestration cannot safely complete."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "post-runtime orchestration boundary inputs are incompatible"
        )
        self.detail = (
            RuntimeResultToProgressionOrchestrationBoundaryFailureDetail(
                classification
            )
        )


def route_runtime_result_to_progression_orchestration_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase161_function: Phase161Function = (
        route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    ),
    phase143_function: Phase143Function = (
        route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    ),
    phase144_function: Phase144Function = (
        route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    ),
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Route one exact Phase-155 result through Phase 161 → 143 → 144 once.

    Phase 161 stays the authoritative first-stage input/terminal/history
    validator. Once Phase 161 returns an exact persistence result, its
    post-call target bytes become the durable commit point; Phase 143 and
    Phase 144 may never cause a rollback to the pre-Phase161 running state.

    Route provenance (Issue #383): when the original input is an exact
    ``StepRuntimeExecutionFailure`` and Phase 143 newly classifies it as an
    exact ``PersistedExecutionOutcome(persisted_failure)``, the exact built-in
    default Phase 144 receives the private active-failure opt-in
    (``_allow_accumulated_none_request_id_for_active_failure=True``) so the
    accumulated aged-None provenance preserved by Issue #380 is accepted on
    this active runtime-failure path only. A custom injected Phase 144
    dependency is never given the opt-in; it keeps the exact four-positional
    call contract. Direct/original ``persisted_failure`` stop inputs are not
    broadened and Phase 136 remains zero-call for persisted_failure.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
        phase161_function,
        phase143_function,
        phase144_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) in (
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        stop = True
    else:
        stop = False
        assert type(result) in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)

    _check_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)

    try:
        value = phase161_function(result, workflow, state_path, events_path)
    except Phase161Error as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _fail("dependency_error")

    if stop:
        if value is not result:
            _restore_if_changed(state_path, events_path, original)
            _fail("phase161_contract")
        _require_unchanged(
            state_path, events_path, original, "phase161_contract"
        )
        return value

    if not _valid_phase161_result(value, state_path, events_path):
        _restore_if_changed(state_path, events_path, original)
        _fail("phase161_contract")

    committed = _capture_targets(state_path, events_path)

    try:
        classified = phase143_function(value, workflow, state_path, events_path)
    except Phase143Error as error:
        _restore_if_changed(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, committed)
        _fail("dependency_error")
    if not _valid_phase143_result(result, classified):
        _restore_if_changed(state_path, events_path, committed)
        _fail("phase143_contract")
    _require_unchanged(state_path, events_path, committed, "committed_mutation")

    phase144_kwargs: dict[str, object] = {}
    if (
        type(result) is StepRuntimeExecutionFailure
        and type(classified) is PersistedExecutionOutcome
        and classified.outcome == "persisted_failure"
        and phase144_function
        is route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    ):
        phase144_kwargs["_allow_accumulated_none_request_id_for_active_failure"] = True

    try:
        progressed = phase144_function(
            classified,
            workflow,
            state_path,
            events_path,
            **phase144_kwargs,
        )
    except Phase144Error as error:
        _restore_if_changed(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, committed)
        _fail("dependency_error")
    if not _valid_phase144_result(workflow, result, classified, progressed):
        _restore_if_changed(state_path, events_path, committed)
        _fail("phase144_contract")
    _require_unchanged(state_path, events_path, committed, "committed_mutation")
    return progressed


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    phase161: object,
    phase143: object,
    phase144: object,
) -> None:
    if type(result) not in (
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
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
    if not (
        callable(phase161) and callable(phase143) and callable(phase144)
    ):
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


def _valid_phase161_result(
    value: object,
    state_path: Path,
    events_path: Path,
) -> bool:
    """Phase 161 must return an exact persistence result for the supplied targets.

    This mirrors the existing Phase 161 output contract (exact type, exact
    target identity, positive int byte counts) without duplicating the
    Phase-155 provenance/history validator.
    """
    if type(value) is not WorkflowExecutionPersistenceResult:
        return False
    if value.state_path is not state_path or value.events_path is not events_path:
        return False
    if (
        type(value.state_bytes_written) is not int
        or value.state_bytes_written <= 0
        or type(value.event_bytes_appended) is not int
        or value.event_bytes_appended <= 0
    ):
        return False
    return True


def _valid_phase143_result(
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    classified: object,
) -> bool:
    """Phase 143 outcome must be semantically consistent with the runtime result.

    Success results classify as persisted_success with no failure category;
    failure results classify as persisted_failure with the exact runtime
    failure category; identity fields must match the original runtime result.
    """
    if type(classified) is not PersistedExecutionOutcome:
        return False
    if type(result) is StepRuntimeExecutionSuccess:
        if classified.outcome != "persisted_success":
            return False
        if classified.failure_category is not None:
            return False
    else:
        if classified.outcome != "persisted_failure":
            return False
        if classified.failure_category != result.invocation_result.category:
            return False
    return (
        classified.workflow_id == result.workflow_id
        and classified.current_step_id == result.step_id
        and classified.current_step_index == result.step_index
        and classified.current_employee_id == result.employee_id
    )


def _valid_phase144_result(
    workflow: WorkflowDefinition,
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    classified: PersistedExecutionOutcome,
    progressed: object,
) -> bool:
    """Phase 144 return contract branches on the classified outcome.

    persisted_success must yield an exact WorkflowProgressionDecision whose
    decision, identity linkage, next fields and reason are consistent with the
    supplied workflow. persisted_failure must yield the exact classified
    object by identity.
    """
    if classified.outcome == "persisted_success":
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
    return progressed is classified and classified.outcome == "persisted_failure"


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
        _restore_if_changed(state, events, original)
        _fail(classification)


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
    raise RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
