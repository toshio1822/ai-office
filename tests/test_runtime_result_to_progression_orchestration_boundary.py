"""Focused Phase 172 post-runtime orchestration boundary tests.

Phase 172 is the first explicit integration/orchestration boundary: it accepts
one exact Phase-155 runtime/stop result and composes the public Phase 161 →
Phase 143 → Phase 144 chains in exactly that order. It must not add another
compatibility repair and must never roll the successfully persisted runtime
result back to the pre-Phase161 running state.

Requirement-to-test mapping (Issue #351):
- public signature / exact default dependency identities / source audit
  -> test_public_signature_and_default_dependency_identities_and_source_audit
- runtime success stage order Phase161 -> Phase143 -> Phase144, canonical
  argument identity, exactly once
  -> test_runtime_success_stage_order_and_canonical_identity_once
- runtime failure stage order and exact persisted-failure identity
  -> test_runtime_failure_stage_order_and_exact_persisted_failure_identity
- workflow_complete stop -> Phase161 once, later stages zero, exact identity
  -> test_workflow_complete_stop_phase161_once_later_zero_exact_identity
- persisted_failure stop -> Phase161 once, later stages zero, exact identity
  -> test_persisted_failure_stop_phase161_once_later_zero_exact_identity
- non-callable dependency configuration rejects before execution
  -> test_non_callable_dependency_configuration_rejects_before_execution
- Phase161 safe error identity; later stages zero; pre-persistence targets
  preserved -> test_phase161_safe_error_identity_and_pre_persistence_preserved
- Phase161 unexpected error sanitized; pre-persistence target mutation
  restored; no retry
  -> test_phase161_unexpected_error_sanitized_and_pre_persistence_restored
- malformed Phase161 return rejected; later stages zero; pre-persistence bytes
  restored -> test_malformed_phase161_return_rejected_and_pre_persistence_restored
- Phase143 safe error identity after successful persistence; committed bytes
  retained; Phase144 zero
  -> test_phase143_safe_error_identity_committed_retained_phase144_zero
- Phase143 unexpected error after persistence; mutation restored to committed
  bytes, not original running bytes
  -> test_phase143_unexpected_error_restored_to_committed_not_running
- malformed Phase143 return after persistence; committed bytes retained;
  Phase144 zero -> test_malformed_phase143_return_committed_retained_phase144_zero
- Phase144 safe error identity after successful persistence; committed bytes
  retained -> test_phase144_safe_error_identity_committed_retained
- Phase144 unexpected error after persistence; mutation restored to committed
  bytes -> test_phase144_unexpected_error_restored_to_committed
- malformed Phase144 return after persistence; committed bytes retained
  -> test_malformed_phase144_return_committed_retained
- apparently valid Phase143 return that mutates a target is rejected and
  restored to committed bytes
  -> test_valid_phase143_return_mutating_target_rejected_restored_to_committed
- apparently valid Phase144 return that mutates a target is rejected and
  restored to committed bytes
  -> test_valid_phase144_return_mutating_target_rejected_restored_to_committed
- rollback failure after the commit attempts both target restorations once and
  never retries a stage
  -> test_rollback_failure_attempts_both_targets_once_without_retry
- real-default integration regression A/B/C
  -> test_real_default_six_step_success_workflow_complete,
     test_real_default_six_step_failure_persisted_failure_identity,
     test_real_default_seven_step_step6_success_prepare_next_step
"""

# ruff: noqa: E501,E701,E702,F401,I001

import ast
import importlib
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
    route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
    route_runtime_result_to_progression_orchestration_boundary,
    route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationError as Phase144Error,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationError as Phase143Error,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError,
    RuntimeResultToProgressionOrchestrationBoundaryError,
    RuntimeResultToProgressionOrchestrationBoundaryFailureDetail,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase161Error,
)
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationSuccess,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

MODULE_NAME = "ai_office.engine.runtime_result_to_progression_orchestration_boundary"


def workflow(steps: int = 6) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{i}",
                    "name": f"Step {i}",
                    "employee": f"e{i}",
                    "instructions": f"step-{i}",
                }
                for i in range(1, steps + 1)
            ],
        }
    )


def predecessor_event(step_id: str, index: int, **changes: object) -> RuntimeStepEvent:
    return replace(
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            step_id,
            index,
            f"e{index}",
            "running",
            "succeeded",
            "openai",
            None,
            f"response-{step_id}",
            f"request-{step_id}",
            f"output-{step_id}",
            None,
        ),
        **changes,  # type: ignore[arg-type]
    )


def setup(tmp_path: Path, steps: int = 6, current: int = 6) -> dict[str, object]:
    """Phase-155-provenance running state at ``current`` for the boundary."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    wf = workflow(steps)
    state = WorkflowExecutionState(
        "w",
        "running",
        wf.steps[current - 1].id,
        current,
        wf.steps[current - 1].employee,
        tuple(step.id for step in wf.steps[: current - 1]),
        None,
    )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events = []
    for index in range(1, current):
        if index == current - 1:
            events.append(
                predecessor_event(
                    wf.steps[index - 1].id, index, output_text="", request_id=None
                )
            )
        elif index in (2, 3, 4):
            events.append(
                predecessor_event(wf.steps[index - 1].id, index, output_text="")
            )
        else:
            events.append(predecessor_event(wf.steps[index - 1].id, index))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    return {
        "workflow": wf,
        "state_path": state_path,
        "events_path": events_path,
        "state_before": state_path.read_bytes(),
        "events_before": events_path.read_bytes(),
    }


def runtime_success(wf: WorkflowDefinition, index: int) -> StepRuntimeExecutionSuccess:
    step = wf.steps[index - 1]
    return StepRuntimeExecutionSuccess(
        "w",
        step.id,
        index,
        step.employee,
        ModelInvocationSuccess(
            "openai",
            f"response-{step.id}",
            f"request-{step.id}",
            "completed",
            ("output",),
            "output",
        ),
    )


def runtime_failure(wf: WorkflowDefinition, index: int) -> StepRuntimeExecutionFailure:
    step = wf.steps[index - 1]
    return StepRuntimeExecutionFailure(
        "w",
        step.id,
        index,
        step.employee,
        ModelInvocationFailure(
            "openai", "api_error", "safe failure", f"request-{step.id}", 500, None, None
        ),
    )


def persistence(values: dict[str, object]) -> WorkflowExecutionPersistenceResult:
    return WorkflowExecutionPersistenceResult(
        values["state_path"], values["events_path"], 1, 1  # type: ignore[arg-type]
    )


def success_outcome(wf: WorkflowDefinition, index: int) -> PersistedExecutionOutcome:
    step = wf.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_success", "w", step.id, index, step.employee, None
    )


def failure_outcome(wf: WorkflowDefinition, index: int) -> PersistedExecutionOutcome:
    step = wf.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure", "w", step.id, index, step.employee, "api_error"
    )


def complete_decision(wf: WorkflowDefinition, index: int) -> WorkflowProgressionDecision:
    step = wf.steps[index - 1]
    return WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        step.id,
        index,
        step.employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )


# ---------------------------------------------------------------------------
# 1. public signature / default identities / source audit
# ---------------------------------------------------------------------------


def test_public_signature_and_default_dependency_identities_and_source_audit() -> None:
    parameters = list(
        inspect.signature(route_runtime_result_to_progression_orchestration_boundary).parameters.values()
    )
    assert [parameter.name for parameter in parameters[:4]] == [
        "result",
        "workflow",
        "state_path",
        "events_path",
    ]
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        and parameter.annotation is object
        for parameter in parameters[:4]
    )
    assert [parameter.name for parameter in parameters[4:]] == [
        "phase161_function",
        "phase143_function",
        "phase144_function",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in parameters[4:]
    )
    assert parameters[4].default is (
        route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    )
    assert parameters[5].default is (
        route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    )
    assert parameters[6].default is (
        route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    )
    assert issubclass(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError,
        RuntimeResultToProgressionOrchestrationBoundaryError,
    )
    assert issubclass(RuntimeResultToProgressionOrchestrationBoundaryError, ValueError)

    module = importlib.import_module(MODULE_NAME)
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    allowed = {
        "collections.abc",
        "dataclasses",
        "pathlib",
        "typing",
        "ai_office.definitions.workflow",
        "ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        "ai_office.engine.persisted_execution_outcome_reentry",
        "ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        "ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        "ai_office.engine.workflow_progression",
        "ai_office.runtime",
        "ai_office.storage",
    }
    assert imported_modules <= allowed
    # No retry loop: no while/async loop; each phase route is called at most
    # once (the bounded for-loop in the compensation helper mirrors Phase 161).
    assert not any(
        isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree)
    )
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert calls.count("phase161_function") == 1
    assert calls.count("phase143_function") == 1
    assert calls.count("phase144_function") == 1
    for forbidden in (
        "route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary",
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary",
    ):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# 2 / 3. runtime stage order and exact identity
# ---------------------------------------------------------------------------


def test_runtime_success_stage_order_and_canonical_identity_once(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    persistence_result = persistence(values)
    outcome = success_outcome(values["workflow"], 6)  # type: ignore[arg-type]
    decision = complete_decision(values["workflow"], 6)  # type: ignore[arg-type]
    order: list[str] = []

    def phase161(r: object, w: object, s: object, e: object) -> object:
        order.append("phase161")
        assert r is result
        assert w is values["workflow"]
        assert s is values["state_path"]
        assert e is values["events_path"]
        return persistence_result

    def phase143(r: object, w: object, s: object, e: object) -> object:
        order.append("phase143")
        assert r is persistence_result
        assert w is values["workflow"]
        assert s is values["state_path"]
        assert e is values["events_path"]
        return outcome

    def phase144(r: object, w: object, s: object, e: object) -> object:
        order.append("phase144")
        assert r is outcome
        assert w is values["workflow"]
        assert s is values["state_path"]
        assert e is values["events_path"]
        return decision

    out = route_runtime_result_to_progression_orchestration_boundary(
        result,
        values["workflow"],
        values["state_path"],
        values["events_path"],
        phase161_function=phase161,
        phase143_function=phase143,
        phase144_function=phase144,
    )
    assert order == ["phase161", "phase143", "phase144"]
    assert out is decision


def test_runtime_failure_stage_order_and_exact_persisted_failure_identity(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    result = runtime_failure(values["workflow"], 6)  # type: ignore[arg-type]
    persistence_result = persistence(values)
    outcome = failure_outcome(values["workflow"], 6)  # type: ignore[arg-type]
    order: list[str] = []

    def phase161(r: object, w: object, s: object, e: object) -> object:
        order.append("phase161")
        return persistence_result

    def phase143(r: object, w: object, s: object, e: object) -> object:
        order.append("phase143")
        assert r is persistence_result
        return outcome

    def phase144(r: object, w: object, s: object, e: object) -> object:
        order.append("phase144")
        assert r is outcome
        return r  # exact identity

    out = route_runtime_result_to_progression_orchestration_boundary(
        result,
        values["workflow"],
        values["state_path"],
        values["events_path"],
        phase161_function=phase161,
        phase143_function=phase143,
        phase144_function=phase144,
    )
    assert order == ["phase161", "phase143", "phase144"]
    assert out is outcome


# ---------------------------------------------------------------------------
# 4 / 5. stop routes
# ---------------------------------------------------------------------------


def test_workflow_complete_stop_phase161_once_later_zero_exact_identity(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    stop = complete_decision(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase161": 0, "phase143": 0, "phase144": 0}

    def phase161(r: object, w: object, s: object, e: object) -> object:
        calls["phase161"] += 1
        assert r is stop
        assert w is values["workflow"]
        assert s is values["state_path"]
        assert e is values["events_path"]
        return stop

    def phase143(*_: object) -> object:
        calls["phase143"] += 1
        raise AssertionError("phase143 must not be called")

    def phase144(*_: object) -> object:
        calls["phase144"] += 1
        raise AssertionError("phase144 must not be called")

    out = route_runtime_result_to_progression_orchestration_boundary(
        stop,
        values["workflow"],
        values["state_path"],
        values["events_path"],
        phase161_function=phase161,
        phase143_function=phase143,
        phase144_function=phase144,
    )
    assert out is stop
    assert calls == {"phase161": 1, "phase143": 0, "phase144": 0}
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


def test_persisted_failure_stop_phase161_once_later_zero_exact_identity(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    stop = failure_outcome(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase161": 0, "phase143": 0, "phase144": 0}

    def phase161(r: object, w: object, s: object, e: object) -> object:
        calls["phase161"] += 1
        assert r is stop
        return stop

    def phase143(*_: object) -> object:
        calls["phase143"] += 1
        raise AssertionError("phase143 must not be called")

    def phase144(*_: object) -> object:
        calls["phase144"] += 1
        raise AssertionError("phase144 must not be called")

    out = route_runtime_result_to_progression_orchestration_boundary(
        stop,
        values["workflow"],
        values["state_path"],
        values["events_path"],
        phase161_function=phase161,
        phase143_function=phase143,
        phase144_function=phase144,
    )
    assert out is stop
    assert calls == {"phase161": 1, "phase143": 0, "phase144": 0}
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 6. non-callable configuration
# ---------------------------------------------------------------------------


def test_non_callable_dependency_configuration_rejects_before_execution(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]

    def phase161(*_: object) -> object:
        raise AssertionError("phase161 must not be called")

    def phase144(*_: object) -> object:
        raise AssertionError("phase144 must not be called")

    with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase161_function=phase161,
            phase143_function=None,  # type: ignore[arg-type]
            phase144_function=phase144,
        )
    assert exc.value.detail.classification == "configuration"


# ---------------------------------------------------------------------------
# 7 / 8 / 9. Phase161 stage failures
# ---------------------------------------------------------------------------


def test_phase161_safe_error_identity_and_pre_persistence_preserved(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    safe = Phase161Error("phase161 safe failure")
    calls: dict[str, int] = {"phase143": 0, "phase144": 0}

    def phase161(*_: object) -> object:
        raise safe

    def phase143(*_: object) -> object:
        calls["phase143"] += 1
        raise AssertionError("phase143 must not be called")

    def phase144(*_: object) -> object:
        calls["phase144"] += 1
        raise AssertionError("phase144 must not be called")

    with pytest.raises(Phase161Error) as exc:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase161_function=phase161,
            phase143_function=phase143,
            phase144_function=phase144,
        )
    assert exc.value is safe
    assert calls == {"phase143": 0, "phase144": 0}
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


def test_phase161_unexpected_error_sanitized_and_pre_persistence_restored(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase161": 0, "phase143": 0, "phase144": 0}

    def phase161(r: object, w: object, s: object, e: object) -> object:
        calls["phase161"] += 1
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"mutated-state")
        e.write_bytes(b"mutated-events")
        raise RuntimeError("secret provider payload")

    def phase143(*_: object) -> object:
        calls["phase143"] += 1
        raise AssertionError("phase143 must not be called")

    def phase144(*_: object) -> object:
        calls["phase144"] += 1
        raise AssertionError("phase144 must not be called")

    with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase161_function=phase161,
            phase143_function=phase143,
            phase144_function=phase144,
        )
    assert exc.value.detail.classification == "dependency_error"
    assert "secret" not in str(exc.value)
    assert calls == {"phase161": 1, "phase143": 0, "phase144": 0}
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


def test_malformed_phase161_return_rejected_and_pre_persistence_restored(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    # exact-but-malformed injected Phase 161 results: wrong type, wrong target
    # identity, or non-positive byte counts must be rejected as
    # phase161_contract before Phase 143 runs (Phase-155 provenance/history
    # validator is intentionally not duplicated here).
    malformed_returns: list[object] = [
        "malformed",
        WorkflowExecutionPersistenceResult(tmp_path / "other-state", events_path, 1, 1),
        WorkflowExecutionPersistenceResult(state_path, tmp_path / "other-events", 1, 1),
        WorkflowExecutionPersistenceResult(state_path, events_path, 0, 1),
        WorkflowExecutionPersistenceResult(state_path, events_path, 1, 0),
    ]
    for bad in malformed_returns:
        calls: dict[str, int] = {"phase143": 0, "phase144": 0}

        def phase161(*_: object, bad: object = bad) -> object:
            return bad

        def phase143(*_: object) -> object:
            calls["phase143"] += 1
            raise AssertionError("phase143 must not be called")

        def phase144(*_: object) -> object:
            calls["phase144"] += 1
            raise AssertionError("phase144 must not be called")

        with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
            route_runtime_result_to_progression_orchestration_boundary(
                result,
                values["workflow"],
                state_path,
                events_path,
                phase161_function=phase161,
                phase143_function=phase143,
                phase144_function=phase144,
            )
        assert exc.value.detail.classification == "phase161_contract"
        assert calls == {"phase143": 0, "phase144": 0}
        assert state_path.read_bytes() == values["state_before"]  # type: ignore[union-attr]
        assert events_path.read_bytes() == values["events_before"]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 10 / 11 / 12. Phase143 stage failures after the durable commit
# ---------------------------------------------------------------------------


def _commit_phase161(values: dict[str, object], result: object) -> WorkflowExecutionPersistenceResult:
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    state_path.write_bytes(b"committed-state")
    events_path.write_bytes(b"committed-events")
    return WorkflowExecutionPersistenceResult(state_path, events_path, 15, 15)


def test_phase143_safe_error_identity_committed_retained_phase144_zero(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    safe = Phase143Error("phase143 safe failure")
    calls: dict[str, int] = {"phase144": 0}

    def phase161(r: object, w: object, s: object, e: object) -> object:
        return _commit_phase161(values, result)

    def phase143(*_: object) -> object:
        raise safe

    def phase144(*_: object) -> object:
        calls["phase144"] += 1
        raise AssertionError("phase144 must not be called")

    with pytest.raises(Phase143Error) as exc:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase161_function=phase161,
            phase143_function=phase143,
            phase144_function=phase144,
        )
    assert exc.value is safe
    assert calls == {"phase144": 0}
    # committed terminal bytes retained, never rolled back to running state
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]
    assert values["state_path"].read_bytes() != values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() != values["events_before"]  # type: ignore[union-attr]


def test_phase143_unexpected_error_restored_to_committed_not_running(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase144": 0}

    def phase161(r: object, w: object, s: object, e: object) -> object:
        return _commit_phase161(values, result)

    def phase143(r: object, w: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"mutated-state")
        e.write_bytes(b"mutated-events")
        raise RuntimeError("boom")

    def phase144(*_: object) -> object:
        calls["phase144"] += 1
        raise AssertionError("phase144 must not be called")

    with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase161_function=phase161,
            phase143_function=phase143,
            phase144_function=phase144,
        )
    assert exc.value.detail.classification == "dependency_error"
    assert calls == {"phase144": 0}
    # restored to the committed bytes, not the original running snapshot
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]
    assert values["state_path"].read_bytes() != values["state_before"]  # type: ignore[union-attr]


def test_malformed_phase143_return_committed_retained_phase144_zero(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    success = runtime_success(wf, 6)
    failure = runtime_failure(wf, 6)
    success_out = success_outcome(wf, 6)
    failure_out = failure_outcome(wf, 6)
    # Phase 143 must return a PersistedExecutionOutcome semantically consistent
    # with the original runtime result: exact type, discriminator matching the
    # runtime outcome, exact identity fields, and a failure category matching
    # the runtime failure category (None on success).
    bad_cases: list[tuple[object, object]] = [
        (success, "malformed"),
        (success, failure_out),  # wrong discriminator for a success result
        (success, replace(success_out, workflow_id="other")),
        (success, replace(success_out, current_step_id="step-5")),
        (success, replace(success_out, current_step_index=5)),
        (success, replace(success_out, current_employee_id="e9")),
        (success, replace(success_out, failure_category="api_error")),
        (failure, success_out),  # wrong discriminator for a failure result
        (failure, replace(failure_out, failure_category="rate_limit")),
        (failure, replace(failure_out, workflow_id="other")),
    ]
    for result, bad in bad_cases:
        calls: dict[str, int] = {"phase144": 0}

        def phase161(r: object, w: object, s: object, e: object) -> object:
            return _commit_phase161(values, result)

        def phase143(*_: object, bad: object = bad) -> object:
            return bad

        def phase144(*_: object) -> object:
            calls["phase144"] += 1
            raise AssertionError("phase144 must not be called")

        with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
            route_runtime_result_to_progression_orchestration_boundary(
                result,
                wf,
                state_path,
                events_path,
                phase161_function=phase161,
                phase143_function=phase143,
                phase144_function=phase144,
            )
        assert exc.value.detail.classification == "phase143_contract"
        assert calls == {"phase144": 0}
        assert state_path.read_bytes() == b"committed-state"
        assert events_path.read_bytes() == b"committed-events"


# ---------------------------------------------------------------------------
# 13 / 14 / 15. Phase144 stage failures after the durable commit
# ---------------------------------------------------------------------------


def test_phase144_safe_error_identity_committed_retained(tmp_path: Path) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    outcome = success_outcome(values["workflow"], 6)  # type: ignore[arg-type]
    safe = Phase144Error("phase144 safe failure")

    def phase161(r: object, w: object, s: object, e: object) -> object:
        return _commit_phase161(values, result)

    def phase143(*_: object) -> object:
        return outcome

    def phase144(*_: object) -> object:
        raise safe

    with pytest.raises(Phase144Error) as exc:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase161_function=phase161,
            phase143_function=phase143,
            phase144_function=phase144,
        )
    assert exc.value is safe
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]


def test_phase144_unexpected_error_restored_to_committed(tmp_path: Path) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    outcome = success_outcome(values["workflow"], 6)  # type: ignore[arg-type]

    def phase161(r: object, w: object, s: object, e: object) -> object:
        return _commit_phase161(values, result)

    def phase143(*_: object) -> object:
        return outcome

    def phase144(r: object, w: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"mutated-state")
        e.write_bytes(b"mutated-events")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase161_function=phase161,
            phase143_function=phase143,
            phase144_function=phase144,
        )
    assert exc.value.detail.classification == "dependency_error"
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]
    assert values["state_path"].read_bytes() != values["state_before"]  # type: ignore[union-attr]


def test_malformed_phase144_return_committed_retained(tmp_path: Path) -> None:
    values = setup(tmp_path)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    result = runtime_success(wf, 6)
    outcome = success_outcome(wf, 6)
    complete = complete_decision(wf, 6)
    # persisted_success: the returned value must be an exact
    # WorkflowProgressionDecision; at the final step the decision must be
    # workflow_complete with None next fields and last_step_succeeded, and the
    # current identity must link back to the supplied runtime result.
    bad_final: list[object] = [
        "malformed",
        replace(complete, decision="not_progressable"),
        replace(complete, workflow_id="other"),
        replace(complete, current_step_id="step-5"),
        replace(complete, current_step_index=5),
        replace(complete, current_employee_id="e9"),
        replace(complete, next_step_id="step-7"),
        replace(complete, next_step_index=7),
        replace(complete, next_employee_id="e7"),
        replace(complete, reason="next_step_available"),
        replace(complete, decision="prepare_next_step"),
    ]
    for bad in bad_final:
        def phase161(r: object, w: object, s: object, e: object) -> object:
            return _commit_phase161(values, result)

        def phase143(*_: object) -> object:
            return outcome

        def phase144(*_: object, bad: object = bad) -> object:
            return bad

        with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
            route_runtime_result_to_progression_orchestration_boundary(
                result,
                wf,
                state_path,
                events_path,
                phase161_function=phase161,
                phase143_function=phase143,
                phase144_function=phase144,
            )
        assert exc.value.detail.classification == "phase144_contract"
        assert state_path.read_bytes() == b"committed-state"
        assert events_path.read_bytes() == b"committed-events"
    # persisted_success at a non-final step: prepare_next_step must reference
    # the supplied workflow's exact next step id/index/employee.
    values7 = setup(tmp_path / "seven", steps=7, current=6)
    wf7 = values7["workflow"]
    assert isinstance(wf7, WorkflowDefinition)
    state7 = values7["state_path"]
    events7 = values7["events_path"]
    assert isinstance(state7, Path) and isinstance(events7, Path)
    result7 = runtime_success(wf7, 6)
    outcome7 = success_outcome(wf7, 6)
    prepare = WorkflowProgressionDecision(
        "prepare_next_step",
        "w",
        "step-6",
        6,
        "e6",
        "step-7",
        7,
        "e7",
        "next_step_available",
    )
    bad_prepare: list[object] = [
        "malformed",
        replace(prepare, decision="workflow_complete"),
        replace(prepare, workflow_id="other"),
        replace(prepare, current_step_id="step-5"),
        replace(prepare, current_step_index=5),
        replace(prepare, current_employee_id="e9"),
        replace(prepare, next_step_id="step-8"),
        replace(prepare, next_step_index=8),
        replace(prepare, next_employee_id="e8"),
        replace(prepare, reason="last_step_succeeded"),
    ]
    for bad in bad_prepare:
        def phase161(r: object, w: object, s: object, e: object) -> object:
            return _commit_phase161(values7, result7)

        def phase143(*_: object) -> object:
            return outcome7

        def phase144(*_: object, bad: object = bad) -> object:
            return bad

        with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
            route_runtime_result_to_progression_orchestration_boundary(
                result7,
                wf7,
                state7,
                events7,
                phase161_function=phase161,
                phase143_function=phase143,
                phase144_function=phase144,
            )
        assert exc.value.detail.classification == "phase144_contract"
        assert state7.read_bytes() == b"committed-state"
        assert events7.read_bytes() == b"committed-events"
    # persisted_failure: only the exact same classified object by identity is
    # accepted; an equal-but-not-identical outcome is a phase144_contract.
    failure = runtime_failure(wf, 6)
    failure_outcome_obj = failure_outcome(wf, 6)
    bad_failure: list[object] = [
        "malformed",
        failure_outcome(wf, 6),  # equal fields but a different object
        replace(failure_outcome_obj, workflow_id="other"),
        success_outcome(wf, 6),
    ]
    for bad in bad_failure:
        def phase161(r: object, w: object, s: object, e: object) -> object:
            return _commit_phase161(values, failure)

        def phase143(*_: object) -> object:
            return failure_outcome_obj

        def phase144(*_: object, bad: object = bad) -> object:
            return bad

        with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
            route_runtime_result_to_progression_orchestration_boundary(
                failure,
                wf,
                state_path,
                events_path,
                phase161_function=phase161,
                phase143_function=phase143,
                phase144_function=phase144,
            )
        assert exc.value.detail.classification == "phase144_contract"
        assert state_path.read_bytes() == b"committed-state"
        assert events_path.read_bytes() == b"committed-events"


# ---------------------------------------------------------------------------
# 16 / 17. apparently valid returns that mutate a committed target
# ---------------------------------------------------------------------------


def test_valid_phase143_return_mutating_target_rejected_restored_to_committed(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    outcome = success_outcome(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase144": 0}

    def phase161(r: object, w: object, s: object, e: object) -> object:
        return _commit_phase161(values, result)

    def phase143(r: object, w: object, s: object, e: object) -> object:
        assert isinstance(s, Path)
        s.write_bytes(b"mutated-state")
        return outcome

    def phase144(*_: object) -> object:
        calls["phase144"] += 1
        raise AssertionError("phase144 must not be called")

    with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase161_function=phase161,
            phase143_function=phase143,
            phase144_function=phase144,
        )
    assert exc.value.detail.classification == "committed_mutation"
    assert calls == {"phase144": 0}
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]


def test_valid_phase144_return_mutating_target_rejected_restored_to_committed(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    outcome = success_outcome(values["workflow"], 6)  # type: ignore[arg-type]
    decision = complete_decision(values["workflow"], 6)  # type: ignore[arg-type]

    def phase161(r: object, w: object, s: object, e: object) -> object:
        return _commit_phase161(values, result)

    def phase143(*_: object) -> object:
        return outcome

    def phase144(r: object, w: object, s: object, e: object) -> object:
        assert isinstance(e, Path)
        e.write_bytes(b"mutated-events")
        return decision

    with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase161_function=phase161,
            phase143_function=phase143,
            phase144_function=phase144,
        )
    assert exc.value.detail.classification == "committed_mutation"
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 18. rollback failure attempts both target restorations once, no retry
# ---------------------------------------------------------------------------


def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    outcome = success_outcome(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase143": 0, "phase144": 0}

    def phase161(r: object, w: object, s: object, e: object) -> object:
        return _commit_phase161(values, result)

    def phase143(r: object, w: object, s: object, e: object) -> object:
        calls["phase143"] += 1
        assert isinstance(s, Path) and isinstance(e, Path)
        # mutate both targets and make the events target unwritable
        s.write_bytes(b"mutated-state")
        e.unlink()
        e.mkdir()  # directory: write_bytes -> IsADirectoryError
        return outcome

    def phase144(*_: object) -> object:
        calls["phase144"] += 1
        raise AssertionError("phase144 must not be called")

    with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError) as exc:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
            phase161_function=phase161,
            phase143_function=phase143,
            phase144_function=phase144,
        )
    assert exc.value.detail.classification == "rollback_failure"
    assert calls == {"phase143": 1, "phase144": 0}
    # the state target restoration was attempted (succeeded); the events
    # target restoration was attempted and failed (directory remains)
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].is_dir()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Real-default integration regression A / B / C
# ---------------------------------------------------------------------------


def _assert_phase30_persisted_once(
    values: dict[str, object], events_before: bytes, *, success: bool
) -> None:
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert len(history.events) == 6
    final = history.state
    event = history.events[-1]
    if success:
        assert final.status == "succeeded"
        assert final.completed_step_ids == (
            "step-1", "step-2", "step-3", "step-4", "step-5", "step-6",
        )
        assert final.last_failure_category is None
        assert event.event_type == "step_succeeded"
        assert event.next_status == "succeeded"
        assert event.provider == "openai"
        assert event.output_text == "output"
    else:
        assert final.status == "failed"
        assert final.completed_step_ids == (
            "step-1", "step-2", "step-3", "step-4", "step-5",
        )
        assert final.last_failure_category == "api_error"
        assert event.event_type == "step_failed"
        assert event.next_status == "failed"
        assert event.provider == "openai"
        assert event.message == "safe failure"
    # Phase-155 predecessor provenance unchanged
    assert [item.output_text for item in history.events[:5]] == [
        "output-step-1", "", "", "", "",
    ]
    assert [item.request_id for item in history.events[:4]] == [
        "request-step-1",
        "request-step-2",
        "request-step-3",
        "request-step-4",
    ]
    assert history.events[4].request_id is None
    assert [item.provider for item in history.events[:5]] == ["openai"] * 5


def test_real_default_six_step_success_workflow_complete(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=6, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    out = route_runtime_result_to_progression_orchestration_boundary(
        result,
        values["workflow"],
        values["state_path"],
        values["events_path"],
    )
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "workflow_complete"
    assert out.reason == "last_step_succeeded"
    assert out.current_step_id == "step-6"
    assert out.current_step_index == 6
    assert out.current_employee_id == "e6"
    _assert_phase30_persisted_once(values, events_before, success=True)
    # final target bytes are the committed terminal bytes (unchanged after the boundary)
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert values["state_path"].read_bytes() == serialize_workflow_execution_state_json(  # type: ignore[union-attr]
        history.state
    ).encode("utf-8")


def test_real_default_six_step_failure_persisted_failure_identity(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=6, current=6)
    result = runtime_failure(values["workflow"], 6)  # type: ignore[arg-type]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    captured: dict[str, object] = {}
    real_phase143 = (
        route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    )

    def counting_phase143(
        r: object, w: object, s: object, e: object
    ) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
        outcome = real_phase143(r, w, s, e)
        captured["outcome"] = outcome
        return outcome

    out = route_runtime_result_to_progression_orchestration_boundary(
        result,
        values["workflow"],
        values["state_path"],
        values["events_path"],
        phase143_function=counting_phase143,
    )
    assert type(out) is PersistedExecutionOutcome
    assert out is captured["outcome"]  # exact Phase-143 object by identity
    assert out.outcome == "persisted_failure"
    assert out.failure_category == "api_error"
    assert out.current_step_id == "step-6"
    assert out.current_step_index == 6
    _assert_phase30_persisted_once(values, events_before, success=False)
    # targets remain at the committed failed terminal bytes
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert values["state_path"].read_bytes() == serialize_workflow_execution_state_json(  # type: ignore[union-attr]
        history.state
    ).encode("utf-8")


def test_real_default_seven_step_step6_success_prepare_next_step(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    out = route_runtime_result_to_progression_orchestration_boundary(
        result,
        values["workflow"],
        values["state_path"],
        values["events_path"],
    )
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "prepare_next_step"
    assert out.reason == "next_step_available"
    assert out.current_step_id == "step-6"
    assert out.current_step_index == 6
    assert out.next_step_id == "step-7"
    assert out.next_step_index == 7
    assert out.next_employee_id == "e7"
    # boundary stops at the decision: step 7 was not prepared/started/executed
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert len(history.events) == 6
    assert history.state.status == "succeeded"
    assert history.state.completed_step_ids == (
        "step-1", "step-2", "step-3", "step-4", "step-5", "step-6",
    )
    assert history.state.current_step_id == "step-6"
    # committed step-6 terminal persistence remains
    assert values["state_path"].read_bytes() == serialize_workflow_execution_state_json(  # type: ignore[union-attr]
        history.state
    ).encode("utf-8")
    assert values["events_path"].read_bytes() != events_before  # type: ignore[union-attr]


def accumulated_setup(tmp_path: Path) -> dict[str, object]:
    """Eight-step Phase-155-provenance running state at step 7.

    Mirrors the exact real-chain bytes proven by Issue #377 C: step 5 carries
    an aged ``request_id=None`` (``openai``) and step 6 (the immediate
    predecessor) also carries ``request_id=None`` (``openai``); positions 2-4
    keep their authorized empty outputs.  Position 1 keeps a normal
    predecessor event.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    wf = workflow(8)
    state = WorkflowExecutionState(
        "w",
        "running",
        wf.steps[6].id,
        7,
        wf.steps[6].employee,
        tuple(step.id for step in wf.steps[:6]),
        None,
    )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events = []
    for index in range(1, 7):
        if index in (5, 6):
            events.append(
                predecessor_event(
                    wf.steps[index - 1].id,
                    index,
                    output_text="" if index == 5 else "output",
                    request_id=None,
                )
            )
        elif index in (2, 3, 4):
            events.append(
                predecessor_event(wf.steps[index - 1].id, index, output_text="")
            )
        else:
            events.append(predecessor_event(wf.steps[index - 1].id, index))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    return {
        "workflow": wf,
        "state_path": state_path,
        "events_path": events_path,
        "state_before": state_path.read_bytes(),
        "events_before": events_path.read_bytes(),
    }


def test_exact_default_active_runtime_failure_uses_opt_in_and_succeeds(
    tmp_path: Path,
) -> None:
    """Issue #383: the exact default Phase 144 receives the private active-
    failure opt-in from the real Phase 172 route and accepts the exact
    Issue #377 C accumulated failure provenance (step5=None + step6=None,
    openai): exact persisted_failure returned, durable failed target
    preserved, exactly one failed event appended, no retry, no Phase 136
    /lower progression call.

    Phase 144 must remain the exact built-in default here: wrapping it would
    disable the opt-in (the route enables it only for the exact default
    identity).
    """
    values = accumulated_setup(tmp_path)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    result = runtime_failure(wf, 7)
    order: list[str] = []
    captured: dict[str, object] = {}
    real_phase161 = (
        route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    )
    real_phase143 = (
        route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    )

    def counting_phase161(
        r: object, w: object, s: object, e: object
    ) -> WorkflowExecutionPersistenceResult:
        order.append("phase161")
        return real_phase161(r, w, s, e)

    def counting_phase143(
        r: object, w: object, s: object, e: object
    ) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
        order.append("phase143")
        outcome = real_phase143(r, w, s, e)
        captured["outcome"] = outcome
        return outcome

    out = route_runtime_result_to_progression_orchestration_boundary(
        result,
        wf,
        values["state_path"],
        values["events_path"],
        phase161_function=counting_phase161,
        phase143_function=counting_phase143,
        # phase144_function intentionally left at the exact built-in default
    )
    assert order == ["phase161", "phase143"]
    assert type(out) is PersistedExecutionOutcome
    assert out is captured["outcome"]  # exact Phase-143 object by identity
    assert out.outcome == "persisted_failure"
    assert out.failure_category == "api_error"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-7"
    assert out.current_step_index == 7
    assert out.current_employee_id == "e7"

    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert history.state.status == "failed"
    assert history.state.current_step_id == "step-7"
    assert history.state.current_step_index == 7
    assert history.state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    assert history.state.last_failure_category == "api_error"
    assert len(history.events) == 7
    assert history.events[6].event_type == "step_failed"
    assert history.events[6].step_id == "step-7"
    assert history.events[6].provider == "openai"
    # Accumulated None provenance preserved byte-exact (positions 5 and 6).
    assert history.events[4].request_id is None
    assert history.events[4].provider == "openai"
    assert history.events[5].request_id is None
    assert history.events[5].provider == "openai"
    # Exactly one failed event appended; no retry / no further step.
    loaded_before = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    del loaded_before


def test_custom_injected_phase144_contract_remains_four_positional_args(
    tmp_path: Path,
) -> None:
    """Issue #383: a custom injected Phase 144 dependency keeps the exact
    four-positional-argument contract on the active runtime-failure route:
    exactly one call, no new keyword argument, exact stage order
    Phase161 -> Phase143 -> Phase144, canonical object identity.  A strict
    four-argument function with no ``**kwargs`` would raise TypeError if any
    keyword were forwarded; the route must therefore never forward the
    private opt-in to custom dependencies.
    """
    values = accumulated_setup(tmp_path)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    result = runtime_failure(wf, 7)
    order: list[str] = []
    captured: dict[str, object] = {}
    real_phase161 = (
        route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    )
    real_phase143 = (
        route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    )

    def phase161(
        r: object, w: object, s: object, e: object
    ) -> WorkflowExecutionPersistenceResult:
        order.append("phase161")
        assert r is result
        assert w is wf
        assert s is values["state_path"]
        assert e is values["events_path"]
        return real_phase161(r, w, s, e)

    def phase143(
        r: object, w: object, s: object, e: object
    ) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
        order.append("phase143")
        assert r is not None
        outcome = real_phase143(r, w, s, e)
        captured["outcome"] = outcome
        return outcome

    def strict_phase144(
        r: object, w: object, s: object, e: object
    ) -> object:
        # Strict four positional arguments; no **kwargs.  If the route ever
        # forwarded the private opt-in, this would raise TypeError.
        order.append("phase144")
        assert r is captured["outcome"]
        assert w is wf
        assert s is values["state_path"]
        assert e is values["events_path"]
        return r

    out = route_runtime_result_to_progression_orchestration_boundary(
        result,
        wf,
        values["state_path"],
        values["events_path"],
        phase161_function=phase161,
        phase143_function=phase143,
        phase144_function=strict_phase144,
    )
    assert order == ["phase161", "phase143", "phase144"]
    assert out is captured["outcome"]
    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure"

    # Inline subcase: a direct original persisted_failure stop remains
    # Phase143 / Phase144 zero with the injected dependencies (bindings
    # swapped for a stop input), exactly as the existing stop contract
    # requires.  Snapshot the current post-commit targets first: the main
    # flow above durably committed the failed step-7 state.
    stop_state_before = values["state_path"].read_bytes()
    stop_events_before = values["events_path"].read_bytes()
    stop = failure_outcome(wf, 7)
    stop_order: list[str] = []

    def stop_phase161(
        r: object, w: object, s: object, e: object
    ) -> object:
        stop_order.append("phase161")
        assert r is stop
        return stop

    def stop_phase143(*_: object) -> object:
        stop_order.append("phase143")
        raise AssertionError("phase143 must not be called for a stop")

    def stop_phase144(*_: object) -> object:
        stop_order.append("phase144")
        raise AssertionError("phase144 must not be called for a stop")

    out_stop = route_runtime_result_to_progression_orchestration_boundary(
        stop,
        wf,
        values["state_path"],
        values["events_path"],
        phase161_function=stop_phase161,
        phase143_function=stop_phase143,
        phase144_function=stop_phase144,
    )
    assert out_stop is stop
    assert stop_order == ["phase161"]
    assert values["state_path"].read_bytes() == stop_state_before
    assert values["events_path"].read_bytes() == stop_events_before
