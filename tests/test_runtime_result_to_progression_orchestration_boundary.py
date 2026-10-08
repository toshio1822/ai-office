"""Observable terminal persistence and post-commit routing behavior."""

# ruff: noqa: E501,E701,E702,F401,I001

from dataclasses import replace
from pathlib import Path

import pytest

import ai_office.engine.persisted_execution_outcome_routing_reentry as phase38_module
import ai_office.engine.runtime_result_to_progression_orchestration_boundary as orchestration_module
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeRoutingError,
    WorkflowProgressionCompatibilityError,
    WorkflowProgressionDecision,
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcomeCompatibilityError,
    PersistedExecutionOutcomeError,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError,
    RuntimeResultToProgressionOrchestrationBoundaryError,
    RuntimeResultToProgressionOrchestrationBoundaryFailureDetail,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.runtime.executed_step_transition_persistence import (
    ExecutedStepTransitionPersistenceError,
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
                    "id": f"step-{index}",
                    "name": f"Step {index}",
                    "employee": f"e{index}",
                    "instructions": f"step-{index}",
                }
                for index in range(1, steps + 1)
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
    """Create a Phase-155-compatible running state at ``current``."""
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


def _history(values: dict[str, object]):
    return load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"],
            values["events_path"],  # type: ignore[arg-type]
        )
    )


def _capture_committed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, bytes],
) -> None:
    real = orchestration_module.persist_executed_step_transition

    def counted(*args: object, **kwargs: object) -> object:
        value = real(*args, **kwargs)
        state_path = args[1]
        events_path = args[2]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        captured["state"] = state_path.read_bytes()
        captured["events"] = events_path.read_bytes()
        return value

    monkeypatch.setattr(
        orchestration_module,
        "persist_executed_step_transition",
        counted,
    )


def test_runtime_success_final_commits_once_and_completes(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=6, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    before_events = _history(values).events

    out = route_runtime_result_to_progression_orchestration_boundary(
        result, values["workflow"], values["state_path"], values["events_path"]
    )

    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "workflow_complete"
    assert out.reason == "last_step_succeeded"
    history = _history(values)
    assert len(history.events) == len(before_events) + 1
    assert history.state.status == "succeeded"
    assert history.state.completed_step_ids[-1] == "step-6"
    assert history.events[-1].event_type == "step_succeeded"
    assert history.events[-1].output_text == "output"


def test_runtime_success_nonfinal_commits_once_and_prepares_next_value(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    before_events = _history(values).events

    out = route_runtime_result_to_progression_orchestration_boundary(
        result, values["workflow"], values["state_path"], values["events_path"]
    )

    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "prepare_next_step"
    assert out.current_step_index == 6
    assert out.next_step_id == "step-7"
    assert out.next_step_index == 7
    assert out.next_employee_id == "e7"
    assert out.reason == "next_step_available"
    history = _history(values)
    assert len(history.events) == len(before_events) + 1
    assert history.state.status == "succeeded"
    assert history.events[-1].event_type == "step_succeeded"


def test_runtime_failure_commits_once_and_stops_without_progression(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=6, current=6)
    result = runtime_failure(values["workflow"], 6)  # type: ignore[arg-type]
    before_events = _history(values).events

    out = route_runtime_result_to_progression_orchestration_boundary(
        result, values["workflow"], values["state_path"], values["events_path"]
    )

    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure"
    assert out.failure_category == "api_error"
    history = _history(values)
    assert len(history.events) == len(before_events) + 1
    assert history.state.status == "failed"
    assert history.state.last_failure_category == "api_error"
    assert history.events[-1].event_type == "step_failed"
    assert history.events[-1].message == "safe failure"


def test_active_runtime_failure_preserves_bounded_aged_none_provenance(
    tmp_path: Path,
) -> None:
    values = accumulated_setup(tmp_path)
    result = runtime_failure(values["workflow"], 7)  # type: ignore[arg-type]

    out = route_runtime_result_to_progression_orchestration_boundary(
        result, values["workflow"], values["state_path"], values["events_path"]
    )

    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure"
    history = _history(values)
    assert history.state.status == "failed"
    assert history.state.current_step_index == 7
    assert history.events[4].request_id is None
    assert history.events[5].request_id is None
    assert history.events[6].event_type == "step_failed"


def test_invalid_active_provenance_fails_before_durable_commit(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=6, current=6)
    events_path = values["events_path"]
    assert isinstance(events_path, Path)
    history = _history(values)
    events = list(history.events)
    events[3] = replace(events[3], request_id=None)
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    events_before = events_path.read_bytes()

    with pytest.raises(
        (PersistedExecutionOutcomeError, ExecutedStepTransitionPersistenceError)
    ):
        route_runtime_result_to_progression_orchestration_boundary(
            runtime_success(values["workflow"], 6),
            values["workflow"],
            values["state_path"],
            events_path,
        )
    assert values["state_path"].read_bytes() == state_before  # type: ignore[union-attr]
    assert events_path.read_bytes() == events_before


@pytest.mark.parametrize(
    "stop",
    [
        WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            "step-6",
            6,
            "e6",
            None,
            None,
            None,
            "last_step_succeeded",
        ),
        PersistedExecutionOutcome(
            "persisted_failure",
            "w",
            "step-6",
            6,
            "e6",
            "api_error",
        ),
    ],
    ids=["workflow_complete", "persisted_failure"],
)
def test_stop_inputs_fail_closed_before_terminal_persistence_or_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop: WorkflowProgressionDecision | PersistedExecutionOutcome,
) -> None:
    values = setup(tmp_path, steps=6, current=6)
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    persistence_calls = 0
    phase38_calls = 0

    def persistence_must_not_run(*args: object, **kwargs: object) -> object:
        nonlocal persistence_calls
        persistence_calls += 1
        raise AssertionError("terminal persistence must not run for a stop value")

    monkeypatch.setattr(
        orchestration_module,
        "persist_executed_step_transition",
        persistence_must_not_run,
    )

    def phase38_must_not_run(*args: object, **kwargs: object) -> object:
        nonlocal phase38_calls
        phase38_calls += 1
        raise AssertionError("persisted routing must not run for a stop value")

    monkeypatch.setattr(
        orchestration_module,
        "route_persisted_execution_outcome_reentry",
        phase38_must_not_run,
    )

    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            stop,
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )

    assert caught.value.detail.classification == "result_type"
    assert persistence_calls == 0
    assert phase38_calls == 0
    assert values["state_path"].read_bytes() == state_before  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == events_before  # type: ignore[union-attr]


def test_terminal_persistence_failure_restores_bytes_and_does_not_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    calls = 0

    def failing_persistence(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        state_path, events_path = args[1], args[2]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        state_path.write_bytes(b"partial-state")
        events_path.write_bytes(b"partial-events")
        raise RuntimeError("secret persistence detail")

    monkeypatch.setattr(
        orchestration_module, "persist_executed_step_transition", failing_persistence
    )
    with pytest.raises(RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError):
        route_runtime_result_to_progression_orchestration_boundary(
            runtime_success(values["workflow"], 6),
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert calls == 1
    assert values["state_path"].read_bytes() == state_before  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == events_before  # type: ignore[union-attr]


def test_post_commit_classification_error_preserves_committed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    committed: dict[str, bytes] = {}
    _capture_committed_snapshot(monkeypatch, committed)
    calls = 0

    def failing_classification(workflow_value: object, history: object) -> object:
        nonlocal calls
        calls += 1
        state_path = values["state_path"]
        events_path = values["events_path"]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        state_path.write_bytes(b"post-commit-classification-mutation")
        events_path.write_bytes(b"post-commit-classification-events")
        raise PersistedExecutionOutcomeCompatibilityError("history_data")

    monkeypatch.setattr(
        phase38_module,
        "classify_loaded_persisted_execution_outcome",
        failing_classification,
    )
    with pytest.raises(PersistedExecutionOutcomeError):
        route_runtime_result_to_progression_orchestration_boundary(
            runtime_success(values["workflow"], 6),
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert calls == 1
    assert values["state_path"].read_bytes() == committed["state"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == committed["events"]  # type: ignore[union-attr]
    assert values["state_path"].read_bytes() != values["state_before"]  # type: ignore[union-attr]


def test_post_commit_progression_error_preserves_committed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    committed: dict[str, bytes] = {}
    _capture_committed_snapshot(monkeypatch, committed)
    calls = 0

    def failing_progression(workflow_value: object, history: object) -> object:
        nonlocal calls
        calls += 1
        state_path = values["state_path"]
        events_path = values["events_path"]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        state_path.write_bytes(b"post-commit-progression-mutation")
        events_path.write_bytes(b"post-commit-progression-events")
        raise WorkflowProgressionCompatibilityError("current_step_identity")

    monkeypatch.setattr(
        phase38_module,
        "decide_workflow_progression",
        failing_progression,
    )
    with pytest.raises(PersistedExecutionOutcomeRoutingError):
        route_runtime_result_to_progression_orchestration_boundary(
            runtime_success(values["workflow"], 6),
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert calls == 1
    assert values["state_path"].read_bytes() == committed["state"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == committed["events"]  # type: ignore[union-attr]


def test_malformed_persisted_route_result_fails_closed_after_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    monkeypatch.setattr(
        orchestration_module,
        "route_persisted_execution_outcome_reentry",
        lambda *args, **kwargs: "malformed-route-result",
    )

    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            runtime_success(values["workflow"], 6),
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert caught.value.detail.classification == "routing_contract"
    history = _history(values)
    assert history.state.status == "succeeded"
    assert history.events[-1].event_type == "step_succeeded"


def test_unexpected_persisted_route_error_is_safe_and_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    calls = 0

    def unexpected(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret provider payload")

    monkeypatch.setattr(
        orchestration_module, "route_persisted_execution_outcome_reentry", unexpected
    )
    with pytest.raises(
        RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError
    ) as caught:
        route_runtime_result_to_progression_orchestration_boundary(
            runtime_success(values["workflow"], 6),
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
    assert caught.value.detail.classification == "dependency_error"
    assert "secret" not in str(caught.value)
    assert calls == 1
    history = _history(values)
    assert history.state.status == "succeeded"
    assert history.events[-1].event_type == "step_succeeded"


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
