"""Tests for Phase 34 running-state persistence reentry."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PreparedRunningStateReentryCompatibilityError,
    PreparedStepExecutionStart,
    persist_prepared_running_state_reentry,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    WorkflowExecutionPersistenceTargets,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "Description",
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


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "two",
            "name": "Two",
            "role": "Role",
            "instructions": "employee",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def succeeded() -> WorkflowExecutionState:
    return WorkflowExecutionState(
        "workflow", "succeeded", "first", 1, "one", ("first",), None
    )


def start() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "employee", "b", ("tool",)),
        WorkflowExecutionState(
            "workflow", "running", "second", 2, "two", ("first",), None
        ),
    )


def write_history(tmp_path: Path) -> WorkflowExecutionPersistenceTargets:
    targets = WorkflowExecutionPersistenceTargets(
        tmp_path / "state.json", tmp_path / "events.jsonl"
    )
    targets.state_path.write_text(serialize_workflow_execution_state_json(succeeded()))
    targets.events_path.write_text(
        serialize_runtime_step_event_jsonl(
            RuntimeStepEvent(
                "step_succeeded",
                "workflow",
                "first",
                1,
                "one",
                "running",
                "succeeded",
                "openai",
                None,
                "response",
                "request",
                "output",
                None,
            )
        )
    )
    return targets


def test_persists_once_returns_exact_result_and_keeps_events(tmp_path: Path) -> None:
    targets = write_history(tmp_path)
    before = targets.events_path.read_bytes()
    calls = 0

    def persist(
        value: PreparedStepExecutionStart, path: Path
    ) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        contents = serialize_workflow_execution_state_json(value.running_state).encode()
        path.write_bytes(contents)
        return RunningStatePersistenceResult(len(contents))

    result = persist_prepared_running_state_reentry(
        workflow(),
        employee(),
        targets.state_path,
        targets.events_path,
        start(),
        persistence_function=persist,
    )
    assert result.state_bytes_written == len(targets.state_path.read_bytes())
    assert calls == 1
    assert targets.events_path.read_bytes() == before


@pytest.mark.parametrize(
    "changed",
    [
        lambda value: object(),
        lambda value: replace(
            value, running_state=replace(value.running_state, current_step_id="other")
        ),
        lambda value: replace(value, request=replace(value.request, model="other")),
    ],
)
def test_invalid_start_rejects_before_persistence(
    tmp_path: Path, changed: object
) -> None:
    targets = write_history(tmp_path)
    calls = 0

    def unexpected(*_args: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PreparedRunningStateReentryCompatibilityError):
        persist_prepared_running_state_reentry(
            workflow(),
            employee(),
            targets.state_path,
            targets.events_path,
            changed(start()),
            persistence_function=unexpected,
        )  # type: ignore[operator,arg-type]
    assert calls == 0


@pytest.mark.parametrize(
    "result", [RunningStatePersistenceResult(0), RunningStatePersistenceResult(True)]
)
def test_invalid_result_rejected_after_one_call(
    tmp_path: Path, result: RunningStatePersistenceResult
) -> None:
    targets = write_history(tmp_path)
    calls = 0

    def persist(*_args: object) -> object:
        nonlocal calls
        calls += 1
        return result

    with pytest.raises(PreparedRunningStateReentryCompatibilityError) as caught:
        persist_prepared_running_state_reentry(
            workflow(),
            employee(),
            targets.state_path,
            targets.events_path,
            start(),
            persistence_function=persist,
        )  # type: ignore[arg-type]
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda path, original: path.write_bytes(original + b"append"),
        lambda path, original: path.write_bytes(original[:1]),
        lambda path, original: path.write_bytes(b"replacement"),
        lambda path, original: path.unlink(),
    ],
)
def test_event_mutation_is_restored_after_one_call(
    tmp_path: Path, mutate: object
) -> None:
    targets = write_history(tmp_path)
    before = targets.events_path.read_bytes()
    calls = 0

    def persist(
        value: PreparedStepExecutionStart, path: Path
    ) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        contents = serialize_workflow_execution_state_json(value.running_state).encode()
        path.write_bytes(contents)
        mutate(targets.events_path, before)  # type: ignore[operator]
        return RunningStatePersistenceResult(len(contents))

    with pytest.raises(PreparedRunningStateReentryCompatibilityError) as caught:
        persist_prepared_running_state_reentry(
            workflow(),
            employee(),
            targets.state_path,
            targets.events_path,
            start(),
            persistence_function=persist,
        )
    assert caught.value.detail.classification == "event_immutability"
    assert calls == 1
    assert targets.events_path.read_bytes() == before
    assert targets.state_path.read_text() == serialize_workflow_execution_state_json(
        start().running_state
    )


def test_event_restore_failure_has_safe_classification(
    tmp_path: Path, monkeypatch
) -> None:
    targets = write_history(tmp_path)

    def persist(
        value: PreparedStepExecutionStart, path: Path
    ) -> RunningStatePersistenceResult:
        contents = serialize_workflow_execution_state_json(value.running_state).encode()
        path.write_bytes(contents)
        targets.events_path.unlink()
        monkeypatch.setattr(
            Path, "write_bytes", lambda *_: (_ for _ in ()).throw(OSError())
        )
        return RunningStatePersistenceResult(len(contents))

    with pytest.raises(PreparedRunningStateReentryCompatibilityError) as caught:
        persist_prepared_running_state_reentry(
            workflow(),
            employee(),
            targets.state_path,
            targets.events_path,
            start(),
            persistence_function=persist,
        )
    assert caught.value.detail.classification == "event_rollback"
    assert str(targets.events_path) not in str(caught.value)
