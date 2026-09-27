"""Observable Phase 38 classification and routing guarantees."""

import importlib
import inspect
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeCompatibilityError,
    PersistedExecutionOutcomeRoutingCompatibilityError,
    route_persisted_execution_outcome_reentry,
)
from ai_office.engine.persisted_success_progression import (
    PersistedSuccessProgressionCompatibilityError,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
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


def outcome(**changes: object) -> PersistedExecutionOutcome:
    values: dict[str, object] = {
        "outcome": "persisted_success",
        "workflow_id": "workflow",
        "current_step_id": "first",
        "current_step_index": 1,
        "current_employee_id": "one",
        "failure_category": None,
    }
    values.update(changes)
    return PersistedExecutionOutcome(**values)  # type: ignore[arg-type]


def decision(**changes: object) -> WorkflowProgressionDecision:
    values: dict[str, object] = {
        "decision": "prepare_next_step",
        "workflow_id": "workflow",
        "current_step_id": "first",
        "current_step_index": 1,
        "current_employee_id": "one",
        "next_step_id": "second",
        "next_step_index": 2,
        "next_employee_id": "two",
        "reason": "next_step_available",
    }
    values.update(changes)
    return WorkflowProgressionDecision(**values)  # type: ignore[arg-type]


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


def test_public_contract_contains_only_three_business_inputs() -> None:
    assert list(
        inspect.signature(route_persisted_execution_outcome_reentry).parameters
    ) == ["workflow", "state_path", "events_path"]


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


def test_success_routes_once_and_preserves_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets = non_final_success_history(tmp_path)
    before = snapshot(targets)
    calls = 0
    real_progression = routing_module.decide_persisted_success_progression

    def progression(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return real_progression(*args, **kwargs)

    monkeypatch.setattr(
        routing_module, "decide_persisted_success_progression", progression
    )
    result = route_persisted_execution_outcome_reentry(
        workflow(), targets.state_path, targets.events_path
    )

    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "prepare_next_step"
    assert result.workflow_id == "workflow"
    assert result.current_step_index == 1
    assert result.next_step_id == "second"
    assert calls == 1
    assert snapshot(targets) == before


@pytest.mark.parametrize("category", ["api_error", "transport_error", "invalid_output"])
def test_persisted_failure_is_value_terminal_stop_without_progression_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, category: str
) -> None:
    targets = failure_history(tmp_path, category)
    before = snapshot(targets)

    def progression_must_not_run(*_: object, **__: object) -> object:
        pytest.fail("persisted failure must not progress")

    monkeypatch.setattr(
        routing_module, "decide_persisted_success_progression", progression_must_not_run
    )
    result = route_persisted_execution_outcome_reentry(
        workflow(), targets.state_path, targets.events_path
    )

    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    assert result.failure_category == category
    assert result.workflow_id == "workflow"
    assert result.current_step_id == "first"
    assert result.current_step_index == 1
    assert result.current_employee_id == "one"
    assert snapshot(targets) == before


def test_final_success_returns_completion_decision_without_writing(
    tmp_path: Path,
) -> None:
    targets = final_success_history(tmp_path)
    before = snapshot(targets)

    result = route_persisted_execution_outcome_reentry(
        workflow(), targets.state_path, targets.events_path
    )

    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert result.current_step_id == "second"
    assert result.current_step_index == 2
    assert result.next_step_id is None
    assert result.reason == "last_step_succeeded"
    assert snapshot(targets) == before


@pytest.mark.parametrize(
    "value, event_value",
    [
        (state(workflow_id="other"), event(workflow_id="other")),
        (
            state(current_step_id="other", completed_step_ids=("other",)),
            event(step_id="other"),
        ),
        (
            state(current_employee_id="other"),
            event(employee_id="other"),
        ),
    ],
)
def test_current_history_linkage_mismatch_fails_closed_before_progression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: WorkflowExecutionState,
    event_value: RuntimeStepEvent,
) -> None:
    targets = write_history(tmp_path, value, event_value)
    before = snapshot(targets)

    monkeypatch.setattr(
        routing_module,
        "decide_persisted_success_progression",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid current history must stop before progression"
        ),
    )
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
def test_invalid_inputs_fail_closed_before_lower_work(
    monkeypatch: pytest.MonkeyPatch,
    workflow_value: object,
    state_path: object,
    events_path: object,
    classification: str,
) -> None:
    monkeypatch.setattr(
        routing_module,
        "classify_persisted_execution_outcome_reentry",
        lambda *_args, **_kwargs: pytest.fail("invalid inputs must stop first"),
    )
    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            workflow_value, state_path, events_path
        )
    assert error.value.detail.classification == classification


def test_same_target_is_rejected_before_lower_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"target")
    monkeypatch.setattr(
        routing_module,
        "classify_persisted_execution_outcome_reentry",
        lambda *_args, **_kwargs: pytest.fail("conflicting targets must stop first"),
    )
    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(workflow(), target, target)
    assert error.value.detail.classification == "target_conflict"


@pytest.mark.parametrize(
    "returned",
    [
        object(),
        outcome(outcome="persisted_success", failure_category="api_error"),
        outcome(outcome="persisted_failure", failure_category=None),
        outcome(outcome="persisted_failure", failure_category="other"),
        outcome(workflow_id="other"),
        outcome(current_step_id="second"),
        outcome(current_step_index=2),
        outcome(current_employee_id="two"),
    ],
)
def test_malformed_classification_fails_closed_without_progression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returned: object,
) -> None:
    targets = non_final_success_history(tmp_path)
    before = snapshot(targets)

    monkeypatch.setattr(
        routing_module,
        "classify_persisted_execution_outcome_reentry",
        lambda *_args, **_kwargs: returned,
    )
    monkeypatch.setattr(
        routing_module,
        "decide_persisted_success_progression",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid classification must not progress"
        ),
    )
    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )

    assert error.value.detail.classification == "classification_contract"
    assert snapshot(targets) == before


@pytest.mark.parametrize(
    "returned",
    [
        object(),
        decision(decision="stopped_failed"),
        decision(workflow_id="other"),
        decision(current_step_id="other"),
        decision(current_step_index=2),
        decision(current_employee_id="other"),
        decision(next_step_id="other"),
        decision(next_step_index=3),
        decision(next_employee_id="other"),
        decision(reason="other"),
        decision(
            decision="workflow_complete",
            next_step_id=None,
            next_step_index=None,
            next_employee_id=None,
            reason="last_step_succeeded",
        ),
    ],
)
def test_malformed_progression_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returned: object,
) -> None:
    targets = non_final_success_history(tmp_path)
    before = snapshot(targets)
    monkeypatch.setattr(
        routing_module,
        "decide_persisted_success_progression",
        lambda *_args, **_kwargs: returned,
    )

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )

    assert error.value.detail.classification == "progression_contract"
    assert snapshot(targets) == before


@pytest.mark.parametrize("stage", ["classification", "progression"])
def test_safe_lower_error_is_preserved_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    targets = non_final_success_history(tmp_path)
    before = snapshot(targets)
    writes: list[Path] = []
    original_write_bytes = Path.write_bytes
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original_write_bytes(path, data))[1],
    )
    expected: ValueError
    if stage == "classification":
        expected = PersistedExecutionOutcomeCompatibilityError("history_data")
        monkeypatch.setattr(
            routing_module,
            "classify_persisted_execution_outcome_reentry",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(expected),
        )
    else:
        expected = PersistedSuccessProgressionCompatibilityError("history_data")
        monkeypatch.setattr(
            routing_module,
            "decide_persisted_success_progression",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(expected),
        )

    with pytest.raises(type(expected)) as error:
        route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )

    assert error.value is expected
    assert writes == []
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

    name = (
        "classify_persisted_execution_outcome_reentry"
        if stage == "classification"
        else "decide_persisted_success_progression"
    )
    monkeypatch.setattr(routing_module, name, unexpected)

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )

    assert error.value.detail.classification == "dependency_error"
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

    def mutate_and_return(*_: object, **__: object) -> object:
        mutate(changed, operation)
        return outcome() if stage == "classification" else decision()

    name = (
        "classify_persisted_execution_outcome_reentry"
        if stage == "classification"
        else "decide_persisted_success_progression"
    )
    monkeypatch.setattr(routing_module, name, mutate_and_return)

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
    original_write_bytes = Path.write_bytes

    def classify_and_delete(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        targets.events_path.unlink()
        return outcome()

    def fail_event_restore(path: Path, data: bytes) -> int:
        if path == targets.events_path:
            raise OSError("restore denied")
        return original_write_bytes(path, data)

    monkeypatch.setattr(
        routing_module,
        "classify_persisted_execution_outcome_reentry",
        classify_and_delete,
    )
    monkeypatch.setattr(Path, "write_bytes", fail_event_restore)

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )

    assert error.value.detail.classification == "dependency_rollback"
    assert calls == 1
