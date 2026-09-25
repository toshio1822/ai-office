"""Behavioral tests for the Phase 172 runtime-result orchestration boundary.

The boundary owns composition and compensation policy while the Phase 161,
Phase 143, and Phase 144 facades own their existing responsibilities.  These
tests exercise the real default composition for normal requests.  Fault tests
patch the facade names resolved by the orchestration module so they can prove
safe failure, compensation, and no-retry behavior without making internal
stage injection part of the public API.
"""

# ruff: noqa: E501,E701,E702,F401,I001

from dataclasses import replace
from pathlib import Path

import pytest

import ai_office.engine.runtime_result_to_progression_orchestration_boundary as orchestration_module
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_runtime_result_to_progression_orchestration_boundary,
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
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
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
    """Create a Phase-155-provenance running state at ``current``."""
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
    state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
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


def complete_decision(
    wf: WorkflowDefinition, index: int
) -> WorkflowProgressionDecision:
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


def _history(values: dict[str, object]):
    return load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"],
            values["events_path"],  # type: ignore[arg-type]
        )
    )


def _make_direct_stop_compatible(values: dict[str, object]) -> None:
    """Use non-aged predecessor provenance for a direct terminal stop input."""
    events_path = values["events_path"]
    assert isinstance(events_path, Path)
    history = _history(values)
    events = list(history.events)
    events[-1] = replace(
        events[-1],
        request_id="request-immediate-predecessor",
        output_text="output-immediate-predecessor",
    )
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )


def _capture_real_persistence(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, bytes],
) -> None:
    real = orchestration_module.route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary

    def counted(*args: object, **kwargs: object) -> object:
        value = real(*args, **kwargs)
        state_path = args[2]
        events_path = args[3]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        captured["state"] = state_path.read_bytes()
        captured["events"] = events_path.read_bytes()
        return value

    monkeypatch.setattr(
        orchestration_module,
        "route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        counted,
    )


def test_real_default_runtime_success_completes_and_commits_once(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=6, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]

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
    history = _history(values)
    assert len(history.events) == 6
    assert history.state.status == "succeeded"
    assert history.state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    assert history.events[-1].event_type == "step_succeeded"
    assert history.events[-1].output_text == "output"


def test_real_default_nonfinal_success_stops_at_prepare_next_step(
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
    history = _history(values)
    assert len(history.events) == 6
    assert history.state.status == "succeeded"
    assert history.state.current_step_id == "step-6"
    assert values["events_path"].read_bytes() != events_before  # type: ignore[union-attr]


def test_real_default_runtime_failure_returns_persisted_failure_and_commits(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=6, current=6)
    result = runtime_failure(values["workflow"], 6)  # type: ignore[arg-type]

    out = route_runtime_result_to_progression_orchestration_boundary(
        result,
        values["workflow"],
        values["state_path"],
        values["events_path"],
    )

    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure"
    assert out.failure_category == "api_error"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-6"
    assert out.current_step_index == 6
    assert out.current_employee_id == "e6"
    history = _history(values)
    assert history.state.status == "failed"
    assert history.state.last_failure_category == "api_error"
    assert history.events[-1].event_type == "step_failed"
    assert history.events[-1].message == "safe failure"


def accumulated_setup(tmp_path: Path) -> dict[str, object]:
    """Create the bounded accumulated aged-None provenance from Issue #383."""
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
    state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
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


def test_active_runtime_failure_keeps_aged_none_compatibility(tmp_path: Path) -> None:
    values = accumulated_setup(tmp_path)
    result = runtime_failure(values["workflow"], 7)  # type: ignore[arg-type]

    out = route_runtime_result_to_progression_orchestration_boundary(
        result,
        values["workflow"],
        values["state_path"],
        values["events_path"],
    )

    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure"
    history = _history(values)
    assert history.state.status == "failed"
    assert history.state.current_step_index == 7
    assert history.events[4].request_id is None
    assert history.events[5].request_id is None
    assert history.events[6].event_type == "step_failed"
    assert len(history.events) == 7


def test_stop_inputs_are_identity_preserving_read_only_and_skip_later_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success_values = setup(tmp_path / "success", steps=6, current=6)
    _make_direct_stop_compatible(success_values)
    success_result = runtime_success(success_values["workflow"], 6)  # type: ignore[arg-type]
    success_stop = route_runtime_result_to_progression_orchestration_boundary(
        success_result,
        success_values["workflow"],
        success_values["state_path"],
        success_values["events_path"],
    )
    assert type(success_stop) is WorkflowProgressionDecision
    success_state = success_values["state_path"].read_bytes()  # type: ignore[union-attr]
    success_events = success_values["events_path"].read_bytes()  # type: ignore[union-attr]

    failure_values = setup(tmp_path / "failure", steps=6, current=6)
    _make_direct_stop_compatible(failure_values)
    failure_result = runtime_failure(failure_values["workflow"], 6)  # type: ignore[arg-type]
    failure_stop = route_runtime_result_to_progression_orchestration_boundary(
        failure_result,
        failure_values["workflow"],
        failure_values["state_path"],
        failure_values["events_path"],
    )
    assert type(failure_stop) is PersistedExecutionOutcome
    failure_state = failure_values["state_path"].read_bytes()  # type: ignore[union-attr]
    failure_events = failure_values["events_path"].read_bytes()  # type: ignore[union-attr]

    def must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("a valid stop must not reclassify or progress")

    monkeypatch.setattr(
        orchestration_module,
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        must_not_run,
    )
    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        must_not_run,
    )

    out_success = route_runtime_result_to_progression_orchestration_boundary(
        success_stop,
        success_values["workflow"],
        success_values["state_path"],
        success_values["events_path"],
    )
    out_failure = route_runtime_result_to_progression_orchestration_boundary(
        failure_stop,
        failure_values["workflow"],
        failure_values["state_path"],
        failure_values["events_path"],
    )
    assert out_success is success_stop
    assert out_failure is failure_stop
    assert success_values["state_path"].read_bytes() == success_state  # type: ignore[union-attr]
    assert success_values["events_path"].read_bytes() == success_events  # type: ignore[union-attr]
    assert failure_values["state_path"].read_bytes() == failure_state  # type: ignore[union-attr]
    assert failure_values["events_path"].read_bytes() == failure_events  # type: ignore[union-attr]


def test_persistence_safe_failure_restores_precommit_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    safe = Phase161Error("safe persistence failure")
    calls = {"persistence": 0, "classification": 0, "progression": 0}

    def persist(*args: object, **kwargs: object) -> object:
        calls["persistence"] += 1
        state_path = args[2]
        events_path = args[3]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        state_path.write_bytes(b"partial-state")
        events_path.write_bytes(b"partial-events")
        raise safe

    def later(*args: object, **kwargs: object) -> object:
        calls["classification"] += 1
        calls["progression"] += 1
        raise AssertionError("later stage must not run")

    monkeypatch.setattr(
        orchestration_module,
        "route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        persist,
    )
    monkeypatch.setattr(
        orchestration_module,
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        later,
    )
    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        later,
    )

    with pytest.raises(Phase161Error) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert caught.value is safe
    assert calls == {"persistence": 1, "classification": 0, "progression": 0}
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


def test_persistence_unexpected_failure_is_safe_and_restores_precommit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    calls = 0

    def persist(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        state_path = args[2]
        events_path = args[3]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        state_path.write_bytes(b"partial-state")
        events_path.write_bytes(b"partial-events")
        raise RuntimeError("secret provider payload")

    monkeypatch.setattr(
        orchestration_module,
        "route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        persist,
    )

    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert caught.value.detail.classification == "dependency_error"
    assert "secret" not in str(caught.value)
    assert calls == 1
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


def test_malformed_persistence_result_fails_closed_without_later_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    calls = {"classification": 0, "progression": 0}
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    malformed = WorkflowExecutionPersistenceResult(state_path, events_path, 0, 1)

    def persist(*args: object, **kwargs: object) -> object:
        return malformed

    def classification(*args: object, **kwargs: object) -> object:
        calls["classification"] += 1
        raise AssertionError("classification must not run")

    def progression(*args: object, **kwargs: object) -> object:
        calls["progression"] += 1
        raise AssertionError("progression must not run")

    monkeypatch.setattr(
        orchestration_module,
        "route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        persist,
    )
    monkeypatch.setattr(
        orchestration_module,
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        classification,
    )
    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        progression,
    )

    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result, values["workflow"], state_path, events_path
        )
    assert caught.value.detail.classification == "phase161_contract"
    assert calls == {"classification": 0, "progression": 0}
    assert state_path.read_bytes() == values["state_before"]
    assert events_path.read_bytes() == values["events_before"]


def test_classification_safe_failure_preserves_committed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    committed: dict[str, bytes] = {}
    _capture_real_persistence(monkeypatch, committed)
    safe = Phase143Error("safe classification failure")
    calls = {"classification": 0, "progression": 0}

    def classification(*args: object, **kwargs: object) -> object:
        calls["classification"] += 1
        raise safe

    def progression(*args: object, **kwargs: object) -> object:
        calls["progression"] += 1
        raise AssertionError("progression must not run")

    monkeypatch.setattr(
        orchestration_module,
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        classification,
    )
    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        progression,
    )

    with pytest.raises(Phase143Error) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert caught.value is safe
    assert calls == {"classification": 1, "progression": 0}
    assert committed["state"] != values["state_before"]
    assert committed["events"] != values["events_before"]
    assert values["state_path"].read_bytes() == committed["state"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == committed["events"]  # type: ignore[union-attr]


def test_classification_unexpected_mutation_restores_committed_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    committed: dict[str, bytes] = {}
    _capture_real_persistence(monkeypatch, committed)
    calls = {"classification": 0, "progression": 0}

    def classification(*args: object, **kwargs: object) -> object:
        calls["classification"] += 1
        state_path = args[2]
        events_path = args[3]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        state_path.write_bytes(b"classification-mutation")
        events_path.write_bytes(b"classification-events")
        raise RuntimeError("untrusted classification error")

    def progression(*args: object, **kwargs: object) -> object:
        calls["progression"] += 1
        raise AssertionError("progression must not run")

    monkeypatch.setattr(
        orchestration_module,
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        classification,
    )
    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        progression,
    )

    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert caught.value.detail.classification == "dependency_error"
    assert calls == {"classification": 1, "progression": 0}
    assert values["state_path"].read_bytes() == committed["state"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == committed["events"]  # type: ignore[union-attr]
    assert values["state_path"].read_bytes() != values["state_before"]  # type: ignore[union-attr]


def test_malformed_classification_result_preserves_committed_and_skips_progression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    committed: dict[str, bytes] = {}
    _capture_real_persistence(monkeypatch, committed)
    calls = 0

    def classification(*args: object, **kwargs: object) -> object:
        return "malformed-classification"

    def progression(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("progression must not run")

    monkeypatch.setattr(
        orchestration_module,
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        classification,
    )
    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        progression,
    )

    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert caught.value.detail.classification == "phase143_contract"
    assert calls == 0
    assert values["state_path"].read_bytes() == committed["state"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == committed["events"]  # type: ignore[union-attr]


def test_valid_classification_mutation_is_compensated_and_not_progressed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    wf = values["workflow"]
    result = runtime_success(wf, 6)  # type: ignore[arg-type]
    committed: dict[str, bytes] = {}
    _capture_real_persistence(monkeypatch, committed)
    progression_calls = 0

    def classification(*args: object, **kwargs: object) -> object:
        state_path = args[2]
        assert isinstance(state_path, Path)
        state_path.write_bytes(b"unexpected-classification-mutation")
        return success_outcome(wf, 6)

    def progression(*args: object, **kwargs: object) -> object:
        nonlocal progression_calls
        progression_calls += 1
        raise AssertionError("progression must not run after mutation")

    monkeypatch.setattr(
        orchestration_module,
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        classification,
    )
    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        progression,
    )

    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result, wf, values["state_path"], values["events_path"]
        )
    assert caught.value.detail.classification == "committed_mutation"
    assert progression_calls == 0
    assert values["state_path"].read_bytes() == committed["state"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == committed["events"]  # type: ignore[union-attr]


def test_progression_safe_failure_preserves_committed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    committed: dict[str, bytes] = {}
    _capture_real_persistence(monkeypatch, committed)
    safe = Phase144Error("safe progression failure")
    calls = 0

    def progression(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise safe

    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        progression,
    )

    with pytest.raises(Phase144Error) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert caught.value is safe
    assert calls == 1
    assert values["state_path"].read_bytes() == committed["state"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == committed["events"]  # type: ignore[union-attr]


def test_progression_unexpected_mutation_is_safe_and_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    committed: dict[str, bytes] = {}
    _capture_real_persistence(monkeypatch, committed)
    calls = 0

    def progression(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        state_path = args[2]
        events_path = args[3]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        state_path.write_bytes(b"progression-mutation")
        events_path.write_bytes(b"progression-events")
        raise RuntimeError("unsafe progression error")

    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        progression,
    )

    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert caught.value.detail.classification == "dependency_error"
    assert calls == 1
    assert values["state_path"].read_bytes() == committed["state"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == committed["events"]  # type: ignore[union-attr]


def test_malformed_progression_result_preserves_committed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    committed: dict[str, bytes] = {}
    _capture_real_persistence(monkeypatch, committed)

    def progression(*args: object, **kwargs: object) -> object:
        return "malformed-progression"

    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        progression,
    )

    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result,
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert caught.value.detail.classification == "phase144_contract"
    assert values["state_path"].read_bytes() == committed["state"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == committed["events"]  # type: ignore[union-attr]


def test_valid_progression_mutation_is_compensated_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    wf = values["workflow"]
    result = runtime_success(wf, 6)  # type: ignore[arg-type]
    committed: dict[str, bytes] = {}
    _capture_real_persistence(monkeypatch, committed)
    calls = 0

    def progression(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        events_path = args[3]
        assert isinstance(events_path, Path)
        events_path.write_bytes(b"unexpected-progression-mutation")
        return complete_decision(wf, 6)

    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        progression,
    )

    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result, wf, values["state_path"], values["events_path"]
        )
    assert caught.value.detail.classification == "committed_mutation"
    assert calls == 1
    assert values["state_path"].read_bytes() == committed["state"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == committed["events"]  # type: ignore[union-attr]


def test_rollback_failure_is_safe_and_does_not_retry_any_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    wf = values["workflow"]
    result = runtime_success(wf, 6)  # type: ignore[arg-type]
    committed: dict[str, bytes] = {}
    _capture_real_persistence(monkeypatch, committed)
    calls = {"classification": 0, "progression": 0}

    def classification(*args: object, **kwargs: object) -> object:
        calls["classification"] += 1
        state_path = args[2]
        events_path = args[3]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        state_path.write_bytes(b"classification-mutation")
        events_path.unlink()
        events_path.mkdir()
        return success_outcome(wf, 6)

    def progression(*args: object, **kwargs: object) -> object:
        calls["progression"] += 1
        raise AssertionError("progression must not run")

    monkeypatch.setattr(
        orchestration_module,
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        classification,
    )
    monkeypatch.setattr(
        orchestration_module,
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        progression,
    )

    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            result, wf, values["state_path"], values["events_path"]
        )
    assert caught.value.detail.classification == "rollback_failure"
    assert calls == {"classification": 1, "progression": 0}
    assert values["state_path"].read_bytes() == committed["state"]  # type: ignore[union-attr]
    assert values["events_path"].is_dir()  # type: ignore[union-attr]


def test_public_error_types_expose_only_safe_classification() -> None:
    assert issubclass(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError,
        RuntimeResultToProgressionOrchestrationBoundaryError,
    )
    assert issubclass(RuntimeResultToProgressionOrchestrationBoundaryError, ValueError)
    detail = RuntimeResultToProgressionOrchestrationBoundaryFailureDetail(
        "dependency_error"
    )
    error = RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError(
        "dependency_error"
    )
    assert detail.classification == "dependency_error"
    assert error.detail.classification == "dependency_error"
    assert "dependency_error" not in str(error)
