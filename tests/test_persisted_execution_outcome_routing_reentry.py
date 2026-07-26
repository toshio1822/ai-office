"""Tests for the Phase 38 read-only routing boundary."""

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


def targets(tmp_path: Path) -> tuple[Path, Path]:
    state, events = tmp_path / "state", tmp_path / "events"
    state.write_bytes(b"state")
    events.write_bytes(b"events")
    return state, events


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


def test_success_routes_once_and_returns_same_decision(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    supplied, expected, calls = outcome(), decision(), [0, 0]

    def classify(*_: object) -> PersistedExecutionOutcome:
        calls[0] += 1
        return supplied

    def progress(*_: object) -> WorkflowProgressionDecision:
        calls[1] += 1
        return expected

    assert (
        route_persisted_execution_outcome_reentry(
            supplied,
            workflow(),
            state,
            events,
            classification_function=classify,
            progression_function=progress,
        )
        is expected
    )
    assert calls == [1, 1]


@pytest.mark.parametrize(
    "category",
    [
        "api_error",
        "transport_error",
        "invalid_response",
        "invalid_output",
        "invalid_request",
        "approval_required",
    ],
)
def test_failure_returns_same_outcome_without_progression(
    tmp_path: Path, category: str
) -> None:
    state, events = targets(tmp_path)
    supplied, calls = outcome(outcome="persisted_failure", failure_category=category), 0

    def classify(*_: object) -> PersistedExecutionOutcome:
        return supplied

    def progress(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_persisted_execution_outcome_reentry(
            supplied,
            workflow(),
            state,
            events,
            classification_function=classify,
            progression_function=progress,
        )
        is supplied
    )
    assert calls == 0


def test_rejects_mismatched_reclassification_before_progression(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def progress(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            outcome(),
            workflow(),
            state,
            events,
            classification_function=lambda *_: outcome(current_step_id="other"),
            progression_function=progress,
        )
    assert error.value.detail.classification == "classification_contract"
    assert calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_id": "other"},
        {"current_step_id": "other"},
        {"current_step_index": 3},
        {"current_employee_id": "other"},
    ],
)
def test_rejects_supplied_outcome_outside_workflow_safely(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events = targets(tmp_path)
    calls = [0, 0]

    def classify(*_: object) -> PersistedExecutionOutcome:
        calls[0] += 1
        raise AssertionError

    def progress(*_: object) -> WorkflowProgressionDecision:
        calls[1] += 1
        raise AssertionError

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            outcome(**changes),
            workflow(),
            state,
            events,
            classification_function=classify,
            progression_function=progress,
        )
    assert error.value.detail.classification == "outcome_contract"
    assert calls == [0, 0]


def test_rejects_reclassified_outcome_outside_workflow_safely(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def progress(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            outcome(),
            workflow(),
            state,
            events,
            classification_function=lambda *_: outcome(current_step_index=99),
            progression_function=progress,
        )
    assert error.value.detail.classification == "classification_contract"
    assert calls == 0


def test_restores_changed_target_and_rejects(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())

    def classify(*_: object) -> PersistedExecutionOutcome:
        events.write_bytes(b"changed")
        return outcome()

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            outcome(), workflow(), state, events, classification_function=classify
        )
    assert error.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_final_success_returns_same_workflow_complete_decision(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    supplied = outcome(
        current_step_id="second", current_step_index=2, current_employee_id="two"
    )
    expected = decision(
        decision="workflow_complete",
        current_step_id="second",
        current_step_index=2,
        current_employee_id="two",
        next_step_id=None,
        next_step_index=None,
        next_employee_id=None,
        reason="last_step_succeeded",
    )
    assert (
        route_persisted_execution_outcome_reentry(
            supplied,
            workflow(),
            state,
            events,
            classification_function=lambda *_: supplied,
            progression_function=lambda *_: expected,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("supplied", "definition", "state_value", "events_value", "classify", "progress"),
    [
        (object(), None, None, None, None, None),
        (None, object(), None, None, None, None),
        (None, None, 1, None, None, None),
        (None, None, None, 1, None, None),
        (None, None, "same", "same", None, None),
        (None, None, None, None, 1, None),
        (None, None, None, None, None, 1),
    ],
)
def test_prevalidation_rejects_without_dependencies_or_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supplied: object,
    definition: object,
    state_value: object,
    events_value: object,
    classify: object,
    progress: object,
) -> None:
    state, events = targets(tmp_path)
    calls, writes = [0, 0], []
    original = Path.write_bytes

    def record(path: Path, contents: bytes) -> int:
        if path in {state, events}:
            writes.append(path)
        return original(path, contents)

    monkeypatch.setattr(Path, "write_bytes", record)

    def classification(*_: object) -> PersistedExecutionOutcome:
        calls[0] += 1
        raise AssertionError

    def progression(*_: object) -> WorkflowProgressionDecision:
        calls[1] += 1
        raise AssertionError

    actual_outcome = outcome() if supplied is None else supplied
    actual_workflow = workflow() if definition is None else definition
    actual_state = state if state_value is None else state_value
    actual_events = events if events_value is None else events_value
    if state_value == "same":
        actual_state = actual_events = state
    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError):
        route_persisted_execution_outcome_reentry(
            actual_outcome,
            actual_workflow,
            actual_state,
            actual_events,
            classification_function=classification if classify is None else classify,  # type: ignore[arg-type]
            progression_function=progression if progress is None else progress,  # type: ignore[arg-type]
        )
    assert calls == [0, 0]
    assert writes == []


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
def test_phase37_contract_rejections_do_not_call_phase31(
    tmp_path: Path, returned: object
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def progress(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError):
        route_persisted_execution_outcome_reentry(
            outcome(),
            workflow(),
            state,
            events,
            classification_function=lambda *_: returned,
            progression_function=progress,  # type: ignore[arg-type]
        )
    assert calls == 0


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
def test_phase31_contract_rejections(tmp_path: Path, returned: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            outcome(),
            workflow(),
            state,
            events,
            classification_function=lambda *_: outcome(),
            progression_function=lambda *_: returned,  # type: ignore[arg-type]
        )
    assert error.value.detail.classification == "progression_contract"


@pytest.mark.parametrize("phase", ["classification", "progression"])
def test_safe_dependency_error_is_same_object_and_unchanged_targets(
    tmp_path: Path, phase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, events = targets(tmp_path)
    writes = []
    original = Path.write_bytes
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original(path, data))[1],
    )
    expected = (
        PersistedExecutionOutcomeCompatibilityError("history_data")
        if phase == "classification"
        else PersistedSuccessProgressionCompatibilityError("history_data")
    )
    with pytest.raises(type(expected)) as error:
        route_persisted_execution_outcome_reentry(
            outcome(),
            workflow(),
            state,
            events,
            classification_function=(lambda *_: (_ for _ in ()).throw(expected))
            if phase == "classification"
            else (lambda *_: outcome()),
            progression_function=(lambda *_: (_ for _ in ()).throw(expected)),
        )
    assert error.value is expected
    assert writes == []


@pytest.mark.parametrize("operation", ["replace", "truncate", "append", "delete"])
@pytest.mark.parametrize("target", ["state", "events"])
def test_phase31_target_mutations_are_restored(
    tmp_path: Path, operation: str, target: str
) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    changed = state if target == "state" else events

    def progress(*_: object) -> WorkflowProgressionDecision:
        if operation == "replace":
            changed.write_bytes(b"replacement")
        elif operation == "truncate":
            changed.write_bytes(b"")
        elif operation == "append":
            changed.write_bytes(changed.read_bytes() + b"append")
        else:
            changed.unlink()
        return decision()

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            outcome(),
            workflow(),
            state,
            events,
            classification_function=lambda *_: outcome(),
            progression_function=progress,
        )
    assert error.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_unexpected_dependency_error_is_safe_and_not_retried(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def classify(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret /path and provider output")

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            outcome(), workflow(), state, events, classification_function=classify
        )
    assert error.value.detail.classification == "dependency_error"
    assert "secret" not in str(error.value)
    assert calls == 1


def test_restoration_failure_is_safely_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, events = targets(tmp_path)
    original = Path.write_bytes

    def fail(path: Path, data: bytes) -> int:
        if path == events:
            raise OSError
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail)

    def classify(*_: object) -> PersistedExecutionOutcome:
        events.unlink()
        return outcome()

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            outcome(), workflow(), state, events, classification_function=classify
        )
    assert error.value.detail.classification == "dependency_rollback"
