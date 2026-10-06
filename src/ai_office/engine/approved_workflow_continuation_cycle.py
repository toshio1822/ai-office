"""One explicit approved-workflow next-step continuation cycle (Phase 190).

This module is deliberately a thin orchestration boundary.  It owns the
boundary between the already persisted terminal decision and the next durable
terminal result, while each phase remains the owner of its own lower-level
contract and persistence semantics.
"""

# ruff: noqa: E501,E701,I001

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.execution_evidence import ExecutionEvidenceError
from ai_office.execution_target import (
    ModelExecutionTargetError,
    execution_target_fingerprint,
    validate_execution_target_for_provider,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    NextStepPreparationError,
    PreparedWorkflowStep,
    _prepare_approved_next_workflow_step,
)
from ai_office.engine.workflow_approval_evidence import (
    build_execution_approval_evidence_for_tools,
    find_business_approval_evidence,
    load_business_approval_evidence,
    load_execution_approval_evidence,
    persist_business_approval_evidence,
    persist_execution_approval_evidence,
    validate_business_approval_evidence,
    validate_execution_approval_evidence,
)
from ai_office.engine.persisted_continuation_runtime_facts import (
    build_persisted_continuation_runtime_facts,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcomeError,
    PersistedExecutionOutcome,
    validate_loaded_persisted_execution_history,
)
from ai_office.engine.persisted_execution_outcome_routing_reentry import (
    PersistedExecutionOutcomeRoutingError,
    route_persisted_execution_outcome_reentry,
)
from ai_office.engine.persisted_success_progression import (
    PersistedSuccessProgressionError,
)
from ai_office.engine.prepared_step_execution_start import (
    PreparedStepExecutionStart,
    PreparedStepExecutionStartError,
    prepare_prepared_step_execution_start,
)
from ai_office.engine.upstream_step_output_handoff import (
    build_immediate_predecessor_upstream_inputs,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError as Phase172CompatibilityError,
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172BoundaryError,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    load_workflow_run_manifest,
)
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationFailureCategory,
    ModelInvocationRequest,
    UpstreamStepOutput,
    validate_model_invocation_execution_approval,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    binding_of,
    is_valid_step_runtime_execution_result,
)
from ai_office.runtime.executed_step_transition_persistence import (
    ExecutedStepTransitionPersistenceError,
)
from ai_office.runtime.persisted_start_execution import (
    PersistedStartExecutionError,
    execute_persisted_start_openai_step,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    RunningStatePersistenceError,
    RunningStatePersistenceInputError,
    RunningStatePersistenceRollbackError,
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceTargets,
    WorkflowExecutionPersistenceRollbackError,
    load_workflow_execution_history,
    load_workflow_execution_history_with_source_digests,
    load_workflow_execution_state,
    parse_runtime_step_event,
    persist_prepared_running_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "preparation_contract",
    "start_contract",
    "running_persistence_contract",
    "execution_contract",
    "approval_contract",
    "business_approval",
    "approval_evidence",
    "post_runtime_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]

_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class ApprovedWorkflowContinuationCycleFailureDetail:
    """Detail-safe classification for one Phase-190 boundary failure."""

    classification: Classification


class ApprovedWorkflowContinuationCycleError(ValueError):
    """Base error for the Phase-190 continuation-cycle boundary."""


class ApprovedWorkflowContinuationCycleCompatibilityError(
    ApprovedWorkflowContinuationCycleError
):
    """Raised when the supplied cycle cannot safely cross a public seam."""

    def __init__(self, classification: Classification) -> None:
        super().__init__("approved workflow continuation cycle inputs are incompatible")
        self.detail = ApprovedWorkflowContinuationCycleFailureDetail(classification)


# A few callers use the explicit "failure detail" spelling used by older
# boundaries.  They are aliases, not another error family.
ApprovedWorkflowContinuationCycleFailure = ApprovedWorkflowContinuationCycleError

# Preserve the current safe-error identity for the owners on the active route;
# unexpected errors are sanitized as a Phase-190 dependency error.
_SAFE_PREPARATION_ERRORS = (
    NextStepPreparationError,
    PersistedExecutionOutcomeRoutingError,
    PersistedExecutionOutcomeError,
    PersistedSuccessProgressionError,
    WorkflowExecutionLoadError,
)
_SAFE_START_ERRORS = (PreparedStepExecutionStartError, WorkflowExecutionLoadError)
_SAFE_RUNNING_PERSISTENCE_ERRORS = (
    RunningStatePersistenceError,
    RunningStatePersistenceInputError,
    RunningStatePersistenceRollbackError,
)
_SAFE_EXECUTION_ERRORS = (PersistedStartExecutionError, ExecutionEvidenceError)
# Post-commit errors retain their semantic owner; the continuation never
# restores the running state after terminal persistence has begun.
_SAFE_POST_RUNTIME_ERRORS = (
    Phase172BoundaryError,
    Phase172CompatibilityError,
    ExecutedStepTransitionPersistenceError,
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceRollbackError,
    PersistedExecutionOutcomeRoutingError,
    PersistedExecutionOutcomeError,
    PersistedSuccessProgressionError,
)


def route_approved_workflow_continuation_cycle(
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
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Execute exactly one approved next workflow step and then stop.

    Terminal decisions are authoritative stop values and are returned before
    operational context is inspected.  A prepare decision is handled by the
    current preparation, start, persistence, execution, and progression
    owners, and then stops after one step.
    """
    _check_result_and_workflow(result, workflow)
    assert type(workflow) is WorkflowDefinition

    # A terminal value has already been produced by an authoritative
    # progression stage.  It is intentionally an identity-preserving
    # short-circuit: no target, approval, employee, tool, credential,
    # transport, or injected dependency is consulted.
    if type(result) is WorkflowProgressionDecision:
        if result.decision == "workflow_complete":
            _check_terminal_decision(result, workflow)
            return result
        _check_prepare_decision(result, workflow)
    else:
        assert type(result) is PersistedExecutionOutcome
        if result.outcome == "persisted_failure":
            _check_terminal_failure(result, workflow)
            return result
        _fail("result_type")

    _check_prepare_configuration(
        workflow,
        state_path,
        events_path,
    )
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    _check_result_target_binding(result, state_path, events_path)
    effective_preparation_approval = _ensure_business_approval(
        result,
        workflow,
        preparation_approval,
        state_path,
    )
    original = _capture_targets(state_path, events_path)

    try:
        authoritative_result = route_persisted_execution_outcome_reentry(
            workflow,
            state_path,
            events_path,
            allow_artifact_completion=False,
        )
        if authoritative_result != result:
            _fail("preparation_contract")
        history, _state_digest, _events_digest = (
            load_workflow_execution_history_with_source_digests(
                WorkflowExecutionPersistenceTargets(
                    state_path,
                    events_path,
                    binding=binding_of(result),
                )
            )
        )
        prepared = _prepare_approved_next_workflow_step(
            workflow,
            history,
            result,
            effective_preparation_approval,
            employee,
        )
    except _SAFE_PREPARATION_ERRORS as error:
        _restore_or_fail(state_path, events_path, original)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, original)
        _fail("dependency_error")
    prepared_valid = _valid_prepared(prepared, result, workflow, employee)
    if _changed(state_path, events_path, original):
        _restore_or_fail(state_path, events_path, original)
        _fail("committed_mutation" if prepared_valid else "preparation_contract")
    if not prepared_valid:
        _fail("preparation_contract")
    assert type(prepared) is PreparedWorkflowStep

    try:
        prepared_history, state_source_sha256, _events_source_sha256 = (
            load_workflow_execution_history_with_source_digests(
                WorkflowExecutionPersistenceTargets(
                    state_path,
                    events_path,
                    binding=prepared.binding,
                )
            )
        )
        validate_loaded_persisted_execution_history(
            workflow,
            prepared_history,
            require_terminal_state=True,
            require_execution_provenance=True,
        )
        run_input = _pinned_run_input(prepared, workflow, state_path)
        prepared_start = prepare_prepared_step_execution_start(
            prepared,
            prepared_history,
            state_source_sha256=state_source_sha256,
            run_input=run_input,
        )
    except _SAFE_START_ERRORS as error:
        _restore_or_fail(state_path, events_path, original)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, original)
        _fail("dependency_error")
    prepared_start_valid = _valid_prepared_start(
        prepared_start, prepared, workflow, employee
    )
    if _changed(state_path, events_path, original):
        _restore_or_fail(state_path, events_path, original)
        _fail("committed_mutation" if prepared_start_valid else "start_contract")
    if not prepared_start_valid:
        _fail("start_contract")
    assert type(prepared_start) is PreparedStepExecutionStart

    guard_before = _capture_targets(state_path, events_path)
    try:
        effective_execution_approval = _check_authoritative_pre_persistence(
            prepared_start,
            workflow,
            state_path,
            events_path,
            resolved_tools,
            execution_approval,
        )
    except ApprovedWorkflowContinuationCycleCompatibilityError:
        if _changed(state_path, events_path, guard_before):
            _restore_or_fail(state_path, events_path, original)
            _fail("committed_mutation")
        raise
    if _changed(state_path, events_path, guard_before):
        _restore_or_fail(state_path, events_path, original)
        _fail("committed_mutation")

    _persist_execution_approval_evidence(
        prepared_start,
        workflow,
        employee,
        resolved_tools,
        effective_execution_approval,
        state_path,
    )

    pre_persistence = _capture_targets(state_path, events_path)
    try:
        _check_authoritative_pre_persistence(
            prepared_start,
            workflow,
            state_path,
            events_path,
            resolved_tools,
            effective_execution_approval,
        )
        persisted_running = persist_prepared_running_state(
            prepared_start, state_path
        )
    except _SAFE_RUNNING_PERSISTENCE_ERRORS as error:
        _restore_or_fail(state_path, events_path, original)
        raise error
    except ApprovedWorkflowContinuationCycleCompatibilityError:
        _restore_or_fail(state_path, events_path, original)
        raise
    except Exception:
        _restore_or_fail(state_path, events_path, original)
        _fail("dependency_error")
    _check_persisted_running(
        persisted_running,
        prepared_start,
        state_path,
        events_path,
        original,
        pre_persistence,
    )
    assert type(persisted_running) is RunningStatePersistenceResult
    running_snapshot = _capture_targets(state_path, events_path)

    try:
        runtime_result = execute_persisted_start_openai_step(
            prepared_start,
            state_path,
            workflow,
            employee,
            resolved_tools,
            api_key,
            effective_execution_approval,
            transport=transport,
            events_path=events_path,
        )
    except _SAFE_EXECUTION_ERRORS as error:
        _restore_or_fail(state_path, events_path, running_snapshot)
        raise error
    except Exception:
        _restore_or_fail(state_path, events_path, running_snapshot)
        _fail("dependency_error")
    runtime_result_valid = _valid_runtime_result(
        runtime_result, prepared_start, workflow
    )
    if runtime_result_valid and (
        runtime_result.invocation_result.provider
        != effective_execution_approval.provider
    ):
        runtime_result_valid = False
    if _changed(state_path, events_path, running_snapshot):
        _restore_or_fail(state_path, events_path, running_snapshot)
        _fail("committed_mutation" if runtime_result_valid else "execution_contract")
    if not runtime_result_valid:
        _fail("execution_contract")
    assert type(runtime_result) in (
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
    )

    # The transition owner commits the terminal result.  This outer route owns
    # only post-commit routing and committed-snapshot safety.  No restoration
    # to the running state is permitted from this point onward.
    try:
        progressed = route_runtime_result_to_progression_orchestration_boundary(
            runtime_result,
            workflow,
            state_path,
            events_path,
        )
    except _SAFE_POST_RUNTIME_ERRORS as error:
        raise error
    except Exception:
        _fail("dependency_error")
    if not _valid_phase172_result(
        progressed,
        runtime_result,
        workflow,
        state_path,
        events_path,
        running_snapshot,
    ):
        _fail("post_runtime_contract")
    return progressed


def _ensure_business_approval(
    result: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    preparation_approval: object,
    state_path: Path,
) -> object:
    """Commit the exact Business Approval before next-step preparation."""
    binding = binding_of(result)
    if binding is None:
        _fail("business_approval")
    try:
        store = WorkflowRunManifestStore(state_path.parent)
        manifest = load_workflow_run_manifest(store, binding.run_id)
        if (
            manifest.workflow_id != workflow.id
            or manifest.digest != binding.manifest_digest
        ):
            _fail("business_approval")
        next_index = result.next_step_index
        if type(next_index) is not int or not 1 <= next_index <= len(workflow.steps):
            _fail("business_approval")
        next_step = workflow.steps[next_index - 1]
        snapshot = manifest.workflow_snapshot.steps[next_index - 1]
        if not (
            snapshot.id == next_step.id and snapshot.employee == next_step.employee
        ):
            _fail("business_approval")
        # The Run namespace is authoritative after restart.  A caller carrier
        # is only a new explicit approval when no exact durable evidence is
        # present; it never replaces an already committed grant.
        evidence = None
        durable_evidence = None
        if snapshot.business_approval_required:
            durable_evidence = find_business_approval_evidence(
                store,
                binding=binding,
                workflow_id=workflow.id,
                step_id=next_step.id,
                step_index=next_index,
                employee_id=next_step.employee,
                progression_from_step_id=result.current_step_id,
                progression_from_step_index=result.current_step_index,
            )
            evidence = durable_evidence
            if (
                evidence is None
                and type(preparation_approval) is NextStepPreparationApproval
            ):
                evidence = preparation_approval.business_approval_evidence
            if evidence is None:
                _fail("business_approval")
            validate_business_approval_evidence(
                evidence,
                binding=binding,
                workflow_id=workflow.id,
                step_id=next_step.id,
                step_index=next_index,
                employee_id=next_step.employee,
                progression_from_step_id=result.current_step_id,
                progression_from_step_index=result.current_step_index,
            )
            if durable_evidence is None:
                persist_business_approval_evidence(store, evidence)
                loaded = load_business_approval_evidence(
                    store, binding.run_id, evidence.approval_id
                )
                validate_business_approval_evidence(
                    loaded,
                    binding=binding,
                    workflow_id=workflow.id,
                    step_id=next_step.id,
                    step_index=next_index,
                    employee_id=next_step.employee,
                    progression_from_step_id=result.current_step_id,
                    progression_from_step_index=result.current_step_index,
                )
        # The lower provider-free preparation owner still consumes its
        # historical shape, but its boolean is normalized only after the
        # pinned business policy/evidence gate has completed.  A malformed
        # carrier is left untouched so that the existing lower owner retains
        # its established compatibility classification; it is not an
        # alternative Business Approval authority.
        if type(preparation_approval) is NextStepPreparationApproval:
            return replace(
                preparation_approval,
                approved=True,
                business_approval_evidence=(
                    evidence if snapshot.business_approval_required else None
                ),
            )
        return preparation_approval
    except ApprovedWorkflowContinuationCycleCompatibilityError:
        raise
    except Exception:
        _fail("approval_evidence")


def _persist_execution_approval_evidence(
    prepared_start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    employee: object,
    resolved_tools: object,
    execution_approval: ModelInvocationExecutionApproval,
    state_path: Path,
) -> None:
    """Commit validated Execution Approval evidence before running persistence."""
    try:
        binding = binding_of(prepared_start.request)
        if binding is None or type(resolved_tools) is not tuple:
            _fail("approval_evidence")
        target = validate_execution_target_for_provider(
            execution_approval.execution_target,
            provider=execution_approval.provider,
        )
        if type(employee) is not EmployeeDefinition:
            _fail("approval_evidence")
        evidence = build_execution_approval_evidence_for_tools(
            prepared_start.request,
            resolved_tools,
            execution_approval,
            workflow_id=workflow.id,
            step_id=prepared_start.running_state.current_step_id,
            step_index=prepared_start.running_state.current_step_index,
            employee_id=employee.id,
            target=target,
        )
        store = WorkflowRunManifestStore(state_path.parent)
        persist_execution_approval_evidence(store, evidence)
        loaded = load_execution_approval_evidence(
            store, binding.run_id, evidence.approval_id
        )
        validate_execution_approval_evidence(
            loaded,
            binding=binding,
            workflow_id=workflow.id,
            step_id=prepared_start.running_state.current_step_id,
            step_index=prepared_start.running_state.current_step_index,
            employee_id=employee.id,
            provider=target.provider,
            execution_target_fingerprint_value=execution_target_fingerprint(target),
            request_fingerprint=execution_approval.request_fingerprint,
        )
    except ApprovedWorkflowContinuationCycleCompatibilityError:
        raise
    except Exception:
        _fail("approval_evidence")


def _check_result_and_workflow(result: object, workflow: object) -> None:
    if type(result) not in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _fail("workflow_definition")


def _check_prepare_configuration(
    workflow: WorkflowDefinition,
    state_path: object,
    events_path: object,
) -> None:
    del workflow
    if type(state_path) is not _PATH_TYPE:
        _fail("state_target")
    if type(events_path) is not _PATH_TYPE:
        _fail("event_target")
    if state_path == events_path:
        _fail("target_conflict")
    _check_regular_file(state_path, "state_target")
    _check_regular_file(events_path, "event_target")


def _check_result_target_binding(
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    state_path: Path,
    events_path: Path,
) -> None:
    """Require one authoritative Manifest-backed execution namespace."""
    try:
        result_binding = binding_of(result)
        # ``WorkflowExecutionPersistenceTargets`` deliberately supports an
        # unbound, provider-free persistence primitive.  It must not be
        # allowed to make this provider-owning boundary appear bound merely
        # because ``None == None``.
        if result_binding is None:
            _fail("preparation_contract")
        store = WorkflowRunManifestStore(state_path.parent)
        manifest = load_workflow_run_manifest(store, result_binding.run_id)
        expected_state, expected_events = store.execution_paths(result_binding.run_id)
        if (
            manifest.workflow_id != result.workflow_id
            or manifest.digest != result_binding.manifest_digest
            or state_path != expected_state
            or events_path != expected_events
        ):
            _fail("preparation_contract")
        targets = WorkflowExecutionPersistenceTargets(
            state_path, events_path, binding=result_binding
        )
        history = load_workflow_execution_history(targets)
    except Exception:
        _fail("preparation_contract")
    target_binding = targets.binding
    if (
        target_binding != result_binding
        or binding_of(history.state) != result_binding
        or any(binding_of(event) != result_binding for event in history.events)
    ):
        _fail("preparation_contract")


def _check_regular_file(path: Path, classification: Classification) -> None:
    try:
        if not path.is_file():
            _fail(classification)
    except OSError:
        _fail(classification)


def _capture_targets(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state_path.read_bytes()
    except OSError:
        _fail("state_target")
    try:
        event_bytes = events_path.read_bytes()
    except OSError:
        _fail("event_target")
    return state_bytes, event_bytes


def _check_terminal_decision(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        _exact(value.decision, "workflow_complete")
        and _exact(value.workflow_id, workflow.id)
        and _exact(value.current_step_id, final.id)
        and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps)
        and _exact(value.current_employee_id, final.employee)
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and _exact(value.reason, "last_step_succeeded")
    ):
        _fail("result_type")


def _check_terminal_failure(
    value: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    index = value.current_step_index
    if not (
        _exact(value.outcome, "persisted_failure")
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
        and _exact(value.workflow_id, workflow.id)
        and _exact(value.current_step_id, workflow.steps[index - 1].id)
        and _exact(value.current_employee_id, workflow.steps[index - 1].employee)
        and type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
    ):
        _fail("result_type")


def _check_prepare_decision(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    current_index = value.current_step_index
    next_index = value.next_step_index
    if not (
        _exact(value.decision, "prepare_next_step")
        and type(current_index) is int
        and 1 <= current_index < len(workflow.steps)
        and type(next_index) is int
        and next_index == current_index + 1
    ):
        _fail("result_type")
    current = workflow.steps[current_index - 1]
    next_step = workflow.steps[next_index - 1]
    if not (
        _exact(value.workflow_id, workflow.id)
        and _exact(value.current_step_id, current.id)
        and _exact(value.current_employee_id, current.employee)
        and _exact(value.next_step_id, next_step.id)
        and _exact(value.next_employee_id, next_step.employee)
        and _exact(value.reason, "next_step_available")
    ):
        _fail("result_type")


def _pinned_run_input(
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    state_path: Path,
) -> str | None:
    """Read Run Input from the Manifest named by the prepared step."""
    binding = prepared.binding
    if binding is None:
        return None
    try:
        manifest = load_workflow_run_manifest(
            WorkflowRunManifestStore(state_path.parent), binding.run_id
        )
    except Exception:
        _fail("start_contract")
    if manifest.workflow_id != workflow.id or manifest.digest != binding.manifest_digest:
        _fail("start_contract")
    return manifest.run_input


def _valid_prepared(
    value: object,
    decision: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object,
) -> bool:
    try:
        _check_prepared(value, decision, workflow, employee)
    except ApprovedWorkflowContinuationCycleCompatibilityError:
        return False
    return True


def _check_prepared(
    value: object,
    decision: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object,
) -> None:
    if (
        type(value) is not PreparedWorkflowStep
        or type(employee) is not EmployeeDefinition
    ):
        _fail("preparation_contract")
    assert type(value) is PreparedWorkflowStep and type(employee) is EmployeeDefinition
    if not _valid_employee(employee):
        _fail("preparation_contract")
    step = workflow.steps[decision.next_step_index - 1]  # type: ignore[index]
    if not (
        _exact(value.workflow_id, workflow.id)
        and _exact(value.step_id, step.id)
        and type(value.step_index) is int
        and value.step_index == decision.next_step_index
        and _exact(value.employee_id, employee.id)
        and _exact(value.employee_id, step.employee)
        and _exact(value.employee_instructions, employee.instructions)
        and _exact(value.step_instructions, step.instructions)
        and _exact(value.model, employee.model)
        and type(value.allowed_tool_names) is tuple
        and all(_nonempty(item) for item in value.allowed_tool_names)
        and value.allowed_tool_names == tuple(employee.allowed_tools)
        and value.binding == binding_of(decision)
    ):
        _fail("preparation_contract")


def _valid_prepared_start(
    value: object,
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    employee: object,
) -> bool:
    try:
        _check_prepared_start(value, prepared, workflow, employee)
    except ApprovedWorkflowContinuationCycleCompatibilityError:
        return False
    return True


def _check_prepared_start(
    value: object,
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    employee: object,
) -> None:
    if (
        type(value) is not PreparedStepExecutionStart
        or type(employee) is not EmployeeDefinition
        or not _valid_employee(employee)
    ):
        _fail("start_contract")
    assert (
        type(value) is PreparedStepExecutionStart
        and type(employee) is EmployeeDefinition
    )
    request = value.request
    running = value.running_state
    if (
        type(request) is not ModelInvocationRequest
        or type(running) is not WorkflowExecutionState
    ):
        _fail("start_contract")
    expected_prefix = tuple(
        step.id for step in workflow.steps[: prepared.step_index - 1]
    )
    if not (
        _exact(running.workflow_id, workflow.id)
        and _exact(running.status, "running")
        and _exact(running.current_step_id, prepared.step_id)
        and type(running.current_step_index) is int
        and running.current_step_index == prepared.step_index
        and _exact(running.current_employee_id, prepared.employee_id)
        and running.current_employee_id == employee.id
        and type(running.completed_step_ids) is tuple
        and all(_nonempty(item) for item in running.completed_step_ids)
        and running.completed_step_ids == expected_prefix
        and running.last_failure_category is None
        and _exact(request.model, prepared.model)
        and _exact(request.system_instructions, prepared.employee_instructions)
        and _exact(request.task_instructions, prepared.step_instructions)
        and type(request.allowed_tools) is tuple
        and all(_nonempty(item) for item in request.allowed_tools)
        and request.allowed_tools == prepared.allowed_tool_names
        and binding_of(running) == prepared.binding
        and binding_of(request) == prepared.binding
        and type(request.upstream_inputs) is tuple
        and all(
            type(upstream) is UpstreamStepOutput
            and type(upstream.workflow_id) is str
            and type(upstream.step_id) is str
            and type(upstream.step_index) is int
            and type(upstream.employee_id) is str
            and type(upstream.output_text) is str
            for upstream in request.upstream_inputs
        )
    ):
        _fail("start_contract")


def _check_authoritative_pre_persistence(
    prepared_start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    resolved_tools: object,
    execution_approval: object,
) -> object:
    """Reload terminal history and validate the complete approved request."""
    try:
        binding = binding_of(prepared_start.running_state)
        history, state_source_sha256, _events_source_sha256 = (
            load_workflow_execution_history_with_source_digests(
                WorkflowExecutionPersistenceTargets(
                    state_path,
                    events_path,
                    binding=binding,
                )
            )
        )
        validate_loaded_persisted_execution_history(
            workflow,
            history,
            require_execution_provenance=True,
        )
        authoritative_upstream = build_immediate_predecessor_upstream_inputs(
            prepared_start.running_state.workflow_id,
            prepared_start.running_state.current_step_index,
            history,
        )
    except Exception:
        _fail("approval_contract")
    if prepared_start.request.upstream_inputs != authoritative_upstream:
        _fail("start_contract")
    try:
        authoritative_runtime_facts = build_persisted_continuation_runtime_facts(
            prepared_start.running_state.workflow_id,
            prepared_start.running_state.current_step_index,
            history,
            state_source_sha256=state_source_sha256,
        )
    except Exception:
        _fail("approval_contract")
    if prepared_start.request.runtime_facts != authoritative_runtime_facts:
        _fail("approval_contract")
    try:
        authoritative_request = ModelInvocationRequest(
            model=prepared_start.request.model,
            system_instructions=prepared_start.request.system_instructions,
            task_instructions=prepared_start.request.task_instructions,
            allowed_tools=prepared_start.request.allowed_tools,
            upstream_inputs=authoritative_upstream,
            runtime_facts=authoritative_runtime_facts,
            run_id=prepared_start.request.run_id,
            manifest_digest=prepared_start.request.manifest_digest,
            run_input=prepared_start.request.run_input,
        )
    except (TypeError, ValueError):
        _fail("start_contract")
    if prepared_start.request != authoritative_request:
        _fail("start_contract")
    if type(resolved_tools) is not tuple:
        _fail("approval_contract")
    if type(execution_approval) is not ModelInvocationExecutionApproval:
        _fail("approval_contract")
    try:
        target = validate_execution_target_for_provider(
            execution_approval.execution_target,
            provider=execution_approval.provider,
        )
        validate_model_invocation_execution_approval(
            prepared_start.request,
            resolved_tools,
            execution_approval,
            provider=execution_approval.provider,
            execution_target=target,
        )
    except (AttributeError, ModelExecutionTargetError, TypeError, ValueError):
        _fail("approval_contract")
    return execution_approval


def _check_persisted_running(
    value: object,
    prepared_start: PreparedStepExecutionStart,
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
    pre_persistence: tuple[bytes, bytes],
) -> None:
    expected = serialize_workflow_execution_state_json(
        prepared_start.running_state
    ).encode("utf-8")
    try:
        state_bytes = state_path.read_bytes()
        event_bytes = events_path.read_bytes()
    except Exception:
        _restore_or_fail(state_path, events_path, original)
        _fail("running_persistence_contract")

    # The state replacement is the one authorized mutation of this stage.
    # Anything other than that exact state-only write is an unauthorized
    # mutation and must be classified separately from a malformed return.
    if state_bytes != expected or event_bytes != original[1]:
        if (state_bytes, event_bytes) == pre_persistence:
            _fail("running_persistence_contract")
        _restore_or_fail(state_path, events_path, original)
        _fail("committed_mutation")
    try:
        loaded = load_workflow_execution_state(
            state_path, binding=binding_of(prepared_start.running_state)
        )
    except Exception:
        _restore_or_fail(state_path, events_path, original)
        _fail("running_persistence_contract")
    if not (
        type(value) is RunningStatePersistenceResult
        and type(value.state_bytes_written) is int
        and value.state_bytes_written > 0
        and value.state_bytes_written == len(expected)
        and type(loaded) is WorkflowExecutionState
        and loaded == prepared_start.running_state
    ):
        _restore_or_fail(state_path, events_path, original)
        _fail("running_persistence_contract")


def _valid_runtime_result(
    value: object,
    prepared_start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
) -> bool:
    try:
        _check_runtime_result(value, prepared_start, workflow)
    except ApprovedWorkflowContinuationCycleCompatibilityError:
        return False
    return True


def _check_runtime_result(
    value: object,
    prepared_start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
) -> None:
    if type(value) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        _fail("execution_contract")
    running = prepared_start.running_state
    try:
        valid = is_valid_step_runtime_execution_result(
            value,
            workflow_id=workflow.id,
            step_id=running.current_step_id,
            step_index=running.current_step_index,
            employee_id=running.current_employee_id,
            run_id=running.run_id,
            manifest_digest=running.manifest_digest,
        )
    except Exception:
        valid = False
    if not valid:
        _fail("execution_contract")


def _valid_phase172_result(
    value: object,
    runtime_result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    running_snapshot: tuple[bytes, bytes],
) -> bool:
    if type(value) not in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        return False
    index = runtime_result.step_index
    step = workflow.steps[index - 1]
    identity = (
        _exact(value.workflow_id, workflow.id)
        and _exact(value.current_step_id, step.id)
        and type(value.current_step_index) is int
        and value.current_step_index == index
        and _exact(value.current_employee_id, step.employee)
        and binding_of(value) == binding_of(runtime_result)
    )
    if not identity:
        return False
    if type(runtime_result) is StepRuntimeExecutionFailure:
        result_ok = (
            type(value) is PersistedExecutionOutcome
            and _exact(value.outcome, "persisted_failure")
            and _exact(
                value.failure_category, runtime_result.invocation_result.category
            )
            and type(value.failure_category) is str
            and value.failure_category in _FAILURE_CATEGORIES
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
            and _exact(value.next_step_id, next_step.id)
            and type(value.next_step_index) is int
            and value.next_step_index == index + 1
            and _exact(value.next_step_index, index + 1)
            and _exact(value.next_employee_id, next_step.employee)
            and _exact(value.reason, "next_step_available")
        )
    if not result_ok:
        return False
    return _valid_phase172_persistence(
        value, runtime_result, workflow, state_path, events_path, running_snapshot
    )


def _valid_phase172_persistence(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
    runtime_result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    running_snapshot: tuple[bytes, bytes],
) -> bool:
    """Verify the Phase-172 terminal state and its one appended event."""
    try:
        state_bytes = state_path.read_bytes()
        event_bytes = events_path.read_bytes()
        if not event_bytes.startswith(running_snapshot[1]):
            return False
        appended = event_bytes[len(running_snapshot[1]) :]
        if not appended or not appended.endswith(b"\n") or appended.count(b"\n") != 1:
            return False
        binding = binding_of(runtime_result)
        event = parse_runtime_step_event(
            json.loads(appended.decode("utf-8")), binding=binding
        )
        if type(event) is not RuntimeStepEvent:
            return False
        if serialize_runtime_step_event_jsonl(event).encode("utf-8") != appended:
            return False
        state = load_workflow_execution_state(state_path, binding=binding)
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
        and _exact(state.workflow_id, workflow.id)
        and _exact(state.current_step_id, step.id)
        and type(state.current_step_index) is int
        and state.current_step_index == runtime_result.step_index
        and _exact(state.current_employee_id, step.employee)
        and _exact(event.workflow_id, workflow.id)
        and _exact(event.step_id, step.id)
        and type(event.step_index) is int
        and event.step_index == runtime_result.step_index
        and _exact(event.employee_id, step.employee)
        and binding_of(state) == binding
        and binding_of(event) == binding
        and _exact(event.previous_status, "running")
        and _exact(event.provider, invocation.provider)
        and event.request_id == invocation.request_id
    )
    if not common:
        return False
    if type(runtime_result) is StepRuntimeExecutionSuccess:
        return (
            type(value) is WorkflowProgressionDecision
            and _exact(state.status, "succeeded")
            and type(state.completed_step_ids) is tuple
            and state.completed_step_ids
            == tuple(item.id for item in workflow.steps[: runtime_result.step_index])
            and state.last_failure_category is None
            and _exact(event.event_type, "step_succeeded")
            and _exact(event.next_status, "succeeded")
            and event.failure_category is None
            and event.response_id == invocation.response_id
            and event.output_text == invocation.text
            and event.message is None
        )
    return (
        type(value) is PersistedExecutionOutcome
        and _exact(state.status, "failed")
        and type(state.completed_step_ids) is tuple
        and state.completed_step_ids
        == tuple(item.id for item in workflow.steps[: runtime_result.step_index - 1])
        and _exact(state.last_failure_category, invocation.category)
        and _exact(event.event_type, "step_failed")
        and _exact(event.next_status, "failed")
        and _exact(event.failure_category, invocation.category)
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


def _valid_employee(employee: EmployeeDefinition) -> bool:
    return (
        _nonempty(employee.id)
        and _nonempty(employee.name)
        and _nonempty(employee.role)
        and _nonempty(employee.instructions)
        and _nonempty(employee.model)
        and type(employee.allowed_tools) is list
        and all(_nonempty(item) for item in employee.allowed_tools)
        and len(employee.allowed_tools) == len(set(employee.allowed_tools))
    )


def _changed(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
) -> bool:
    try:
        return (
            not state_path.is_file()
            or not events_path.is_file()
            or state_path.read_bytes() != original[0]
            or events_path.read_bytes() != original[1]
        )
    except Exception:
        return True


def _restore_or_fail(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
) -> None:
    try:
        changed = _changed(state_path, events_path, original)
    except (OSError, ValueError):
        changed = True
    if not changed:
        return
    failed = False
    # Once rollback is required, make exactly one restoration attempt for each
    # target, including an unchanged target.  This is important when a target
    # disappears or a filesystem operation fails halfway through compensation.
    for path, contents in (
        (state_path, original[0]),
        (events_path, original[1]),
    ):
        try:
            path.write_bytes(contents)
        except Exception:
            failed = True
    if failed or _changed(state_path, events_path, original):
        _fail("rollback_failure")


def _exact(value: object, expected: object) -> bool:
    return type(value) is type(expected) and value == expected


def _nonempty(value: object) -> bool:
    return type(value) is str and bool(value)


def _fail(classification: Classification) -> None:
    raise ApprovedWorkflowContinuationCycleCompatibilityError(classification) from None


__all__ = [
    "ApprovedWorkflowContinuationCycleCompatibilityError",
    "ApprovedWorkflowContinuationCycleError",
    "ApprovedWorkflowContinuationCycleFailure",
    "ApprovedWorkflowContinuationCycleFailureDetail",
    "route_approved_workflow_continuation_cycle",
]
