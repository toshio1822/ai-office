"""Observable Phase 38 shared-history routing guarantees."""

import importlib
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeCompatibilityError,
    PersistedExecutionOutcomeRoutingCompatibilityError,
    route_persisted_execution_outcome_reentry,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceTargets,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

routing_module = importlib.import_module(
    "ai_office.engine.persisted_execution_outcome_routing_reentry"
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "test",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "one",
                    "instructions": "a",
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "two",
                    "instructions": "b",
                },
            ],
        }
    )


def state(**changes: object) -> WorkflowExecutionState:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "status": "succeeded",
        "current_step_id": "first",
        "current_step_index": 1,
        "current_employee_id": "one",
        "completed_step_ids": ("first",),
        "last_failure_category": None,
    }
    values.update(changes)
    return WorkflowExecutionState(**values)  # type: ignore[arg-type]


def event(**changes: object) -> RuntimeStepEvent:
    values: dict[str, object] = {
        "event_type": "step_succeeded",
        "workflow_id": "workflow",
        "step_id": "first",
        "step_index": 1,
        "employee_id": "one",
        "previous_status": "running",
        "next_status": "succeeded",
        "provider": "openai",
        "failure_category": None,
        "response_id": "response",
        "request_id": "request",
        "output_text": "output",
        "message": None,
    }
    values.update(changes)
    return RuntimeStepEvent(**values)  # type: ignore[arg-type]


def write_history(
    tmp_path: Path, value: WorkflowExecutionState, *events: RuntimeStepEvent
) -> WorkflowExecutionPersistenceTargets:
    targets = WorkflowExecutionPersistenceTargets(
        tmp_path / "state.json", tmp_path / "events.jsonl"
    )
    targets.state_path.write_text(serialize_workflow_execution_state_json(value))
    targets.events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events)
    )
    return targets


def non_final_success_history(tmp_path: Path) -> WorkflowExecutionPersistenceTargets:
    return write_history(tmp_path, state(), event())


def final_success_history(tmp_path: Path) -> WorkflowExecutionPersistenceTargets:
    return write_history(
        tmp_path,
        state(
            current_step_id="second",
            current_step_index=2,
            current_employee_id="two",
            completed_step_ids=("first", "second"),
        ),
        event(),
        event(step_id="second", step_index=2, employee_id="two"),
    )


def failure_history(
    tmp_path: Path, category: str = "api_error"
) -> WorkflowExecutionPersistenceTargets:
    return write_history(
        tmp_path,
        state(status="failed", completed_step_ids=(), last_failure_category=category),
        event(
            event_type="step_failed",
            next_status="failed",
            failure_category=category,
            response_id=None,
            output_text=None,
            message="safe",
        ),
    )


def snapshot(targets: WorkflowExecutionPersistenceTargets) -> tuple[bytes, bytes]:
    return targets.state_path.read_bytes(), targets.events_path.read_bytes()


def mutate(path: Path, operation: str) -> None:
    if operation == "replace":
        path.write_bytes(b"replacement")
    elif operation == "truncate":
        path.write_bytes(b"")
    elif operation == "append":
        path.write_bytes(path.read_bytes() + b"append")
    else:
        path.unlink()


@pytest.mark.parametrize(
    "args, kwargs",
    [
        ((object(), object(), object(), object()), {}),
        ((object(), object(), object()), {"outcome": object()}),
        ((object(), object(), object()), {"classification_function": object()}),
        ((object(), object(), object()), {"progression_function": object()}),
    ],
)
def test_removed_public_inputs_and_dependency_keywords_are_rejected(
    args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    with pytest.raises(TypeError):
        route_persisted_execution_outcome_reentry(*args, **kwargs)


def test_success_classification_and_progression_share_one_loaded_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets = non_final_success_history(tmp_path)
    real_loader = routing_module.load_workflow_execution_history
    real_classifier = routing_module.classify_loaded_persisted_execution_outcome
    real_decision = routing_module.decide_workflow_progression
    loaded: list[object] = []
    classified: list[object] = []
    progressed: list[object] = []

    def load(value: object) -> object:
        history = real_loader(value)
        loaded.append(history)
        return history

    def classify(definition: object, history: object) -> object:
        classified.append(history)
        return real_classifier(definition, history)

    def decide(definition: object, history: object) -> object:
        progressed.append(history)
        return real_decision(definition, history)

    monkeypatch.setattr(routing_module, "load_workflow_execution_history", load)
    monkeypatch.setattr(
        routing_module, "classify_loaded_persisted_execution_outcome", classify
    )
    monkeypatch.setattr(routing_module, "decide_workflow_progression", decide)

    before = snapshot(targets)
    result = route_persisted_execution_outcome_reentry(
        workflow(), targets.state_path, targets.events_path
    )

    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "prepare_next_step"
    assert len(loaded) == 1
    assert classified == progressed == loaded
    assert snapshot(targets) == before


def test_final_success_loads_once_and_stops_at_completion(tmp_path: Path) -> None:
    targets = final_success_history(tmp_path)
    calls = 0
    real_loader = routing_module.load_workflow_execution_history

    def load(value: object) -> object:
        nonlocal calls
        calls += 1
        return real_loader(value)

    original = snapshot(targets)
    routing_module.load_workflow_execution_history = load
    try:
        result = route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )
    finally:
        routing_module.load_workflow_execution_history = real_loader

    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert calls == 1
    assert snapshot(targets) == original


@pytest.mark.parametrize("category", ["api_error", "transport_error", "invalid_output"])
def test_persisted_failure_is_terminal_without_progression_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, category: str
) -> None:
    targets = failure_history(tmp_path, category)
    before = snapshot(targets)
    real_decision = routing_module.decide_workflow_progression

    def progression_must_not_run(*_: object, **__: object) -> object:
        pytest.fail("persisted failure must not progress")

    monkeypatch.setattr(
        routing_module, "decide_workflow_progression", progression_must_not_run
    )
    result = route_persisted_execution_outcome_reentry(
        workflow(), targets.state_path, targets.events_path
    )
    monkeypatch.setattr(
        routing_module, "decide_workflow_progression", real_decision
    )

    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    assert result.failure_category == category
    assert snapshot(targets) == before


@pytest.mark.parametrize("status", ["ready", "running"])
def test_ready_and_running_histories_fail_closed_without_progression(
    tmp_path: Path, status: str
) -> None:
    targets = write_history(
        tmp_path,
        state(status=status, completed_step_ids=()),
    )
    before = snapshot(targets)

    with pytest.raises(PersistedExecutionOutcomeCompatibilityError):
        route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )

    assert snapshot(targets) == before


@pytest.mark.parametrize(
    "value, event_value",
    [
        (state(workflow_id="other"), event(workflow_id="other")),
        (
            state(current_step_id="other", completed_step_ids=("other",)),
            event(step_id="other"),
        ),
        (state(current_employee_id="other"), event(employee_id="other")),
    ],
)
def test_current_history_linkage_mismatch_fails_closed(
    tmp_path: Path,
    value: WorkflowExecutionState,
    event_value: RuntimeStepEvent,
) -> None:
    targets = write_history(tmp_path, value, event_value)
    before = snapshot(targets)

    with pytest.raises(PersistedExecutionOutcomeCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )

    assert error.value.detail.classification == "workflow_identity"
    assert snapshot(targets) == before


@pytest.mark.parametrize(
    "workflow_value, state_path, events_path, classification",
    [
        (object(), Path("state"), Path("events"), "workflow_definition"),
        (workflow(), object(), Path("events"), "state_target"),
        (workflow(), Path("state"), object(), "event_target"),
    ],
)
def test_invalid_inputs_fail_closed_before_history_work(
    workflow_value: object,
    state_path: object,
    events_path: object,
    classification: str,
) -> None:
    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            workflow_value, state_path, events_path
        )
    assert error.value.detail.classification == classification


def test_same_target_is_rejected_before_history_work(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"target")

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(workflow(), target, target)

    assert error.value.detail.classification == "target_conflict"


def test_phase38_keeps_only_minimum_classification_route_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets = non_final_success_history(tmp_path)
    real_classifier = routing_module.classify_loaded_persisted_execution_outcome
    malformed_identity = PersistedExecutionOutcome(
        outcome="persisted_success",
        workflow_id="other",
        current_step_id="other",
        current_step_index=99,
        current_employee_id="other",
        failure_category="not-phase-37-owned",  # type: ignore[arg-type]
    )

    def classify(definition: object, history: object) -> object:
        real_classifier(definition, history)
        return malformed_identity

    monkeypatch.setattr(
        routing_module, "classify_loaded_persisted_execution_outcome", classify
    )
    result = route_persisted_execution_outcome_reentry(
        workflow(), targets.state_path, targets.events_path
    )

    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "prepare_next_step"


@pytest.mark.parametrize("contents", [b"bad", b"\xff"])
def test_corrupt_history_fails_closed_without_leaking_details(
    tmp_path: Path, contents: bytes
) -> None:
    state_path = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    state_path.write_bytes(contents)
    events_path.write_text("")

    with pytest.raises(WorkflowExecutionLoadError) as error:
        route_persisted_execution_outcome_reentry(workflow(), state_path, events_path)

    assert str(state_path) not in str(error.value)


def test_safe_lower_error_is_preserved_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets = non_final_success_history(tmp_path)
    before = snapshot(targets)
    expected = WorkflowExecutionLoadError("safe")

    def fail_loader(_: object) -> object:
        raise expected

    monkeypatch.setattr(routing_module, "load_workflow_execution_history", fail_loader)

    with pytest.raises(type(expected)) as error:
        route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )

    assert error.value is expected
    assert snapshot(targets) == before


@pytest.mark.parametrize("stage", ["classification", "progression"])
def test_unexpected_lower_error_is_sanitized_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    targets = non_final_success_history(tmp_path)
    before = snapshot(targets)
    calls = 0

    def unexpected(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret provider output and /private/path")

    if stage == "classification":
        monkeypatch.setattr(
            routing_module, "load_workflow_execution_history", unexpected
        )
    else:
        monkeypatch.setattr(
            routing_module, "decide_workflow_progression", unexpected
        )

    with pytest.raises(
        (
            PersistedExecutionOutcomeCompatibilityError,
            PersistedExecutionOutcomeRoutingCompatibilityError,
        )
    ) as error:
        route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )

    assert "secret" not in str(error.value)
    assert "/private/path" not in str(error.value)
    assert calls == 1
    assert snapshot(targets) == before


@pytest.mark.parametrize("stage", ["classification", "progression"])
@pytest.mark.parametrize("target_name", ["state", "events"])
@pytest.mark.parametrize("operation", ["replace", "truncate", "append", "delete"])
def test_lower_mutation_is_rejected_and_compensated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    target_name: str,
    operation: str,
) -> None:
    targets = non_final_success_history(tmp_path)
    before = snapshot(targets)
    changed = targets.state_path if target_name == "state" else targets.events_path

    if stage == "classification":
        real_loader = routing_module.load_workflow_execution_history

        def load(value: object) -> object:
            history = real_loader(value)
            mutate(changed, operation)
            return history

        monkeypatch.setattr(routing_module, "load_workflow_execution_history", load)
    else:
        real_decision = routing_module.decide_workflow_progression

        def decide(definition: object, history: object) -> object:
            result = real_decision(definition, history)
            mutate(changed, operation)
            return result

        monkeypatch.setattr(routing_module, "decide_workflow_progression", decide)

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )

    assert error.value.detail.classification == "dependency_error"
    assert snapshot(targets) == before


def test_rollback_failure_is_safely_classified_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets = non_final_success_history(tmp_path)
    calls = 0
    real_loader = routing_module.load_workflow_execution_history
    original_write_bytes = Path.write_bytes

    def load(value: object) -> object:
        nonlocal calls
        calls += 1
        history = real_loader(value)
        targets.events_path.unlink()
        return history

    def fail_event_restore(path: Path, data: bytes) -> int:
        if path == targets.events_path:
            raise OSError("restore denied")
        return original_write_bytes(path, data)

    monkeypatch.setattr(routing_module, "load_workflow_execution_history", load)
    monkeypatch.setattr(Path, "write_bytes", fail_event_restore)

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )

    assert error.value.detail.classification == "dependency_rollback"
    assert calls == 1
