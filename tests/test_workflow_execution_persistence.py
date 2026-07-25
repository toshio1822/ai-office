"""Tests for compensatable workflow state and event persistence."""

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import ai_office.storage.workflow_execution_persistence as persistence
from ai_office.invocation import ModelInvocationSuccess
from ai_office.runtime import (
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    WorkflowExecutionTransition,
    transition_workflow_execution_from_step_result,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceError,
    WorkflowExecutionPersistenceInputError,
    WorkflowExecutionPersistenceRollbackError,
    WorkflowExecutionPersistenceTargets,
    build_runtime_step_event_dict,
    build_workflow_execution_state_dict,
    persist_workflow_execution_transition,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def transition() -> WorkflowExecutionTransition:
    previous = WorkflowExecutionState(
        "workflow", "running", "step", 1, "employee", ("first", "first"), None
    )
    result = StepRuntimeExecutionSuccess(
        "workflow",
        "step",
        1,
        "employee",
        ModelInvocationSuccess(
            "provider", "response", "request", "completed", ("日本語",), "日本語"
        ),
    )
    return transition_workflow_execution_from_step_result(previous, result)


def targets(tmp_path: Path) -> WorkflowExecutionPersistenceTargets:
    return WorkflowExecutionPersistenceTargets(
        state_path=tmp_path / "workflow-state.json",
        events_path=tmp_path / "runtime-events.jsonl",
    )


def test_targets_and_result_models_are_immutable() -> None:
    value = targets(Path("/tmp"))
    with pytest.raises(FrozenInstanceError):
        value.state_path = Path("other")  # type: ignore[misc]


def test_deterministic_serializers_preserve_safe_values_and_key_order() -> None:
    value = transition()

    state_dict = build_workflow_execution_state_dict(value.next_state)
    event_dict = build_runtime_step_event_dict(value.event)
    state_json = serialize_workflow_execution_state_json(value.next_state)
    event_jsonl = serialize_runtime_step_event_jsonl(value.event)

    assert list(state_dict) == [
        "workflow_id",
        "status",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "completed_step_ids",
        "last_failure_category",
    ]
    assert state_dict["completed_step_ids"] == ["first", "first", "step"]
    assert state_dict["last_failure_category"] is None
    assert list(event_dict) == [
        "event_type",
        "workflow_id",
        "step_id",
        "step_index",
        "employee_id",
        "previous_status",
        "next_status",
        "provider",
        "failure_category",
        "response_id",
        "request_id",
        "output_text",
        "message",
    ]
    assert state_json.endswith("\n")
    assert event_jsonl.endswith("\n")
    assert event_jsonl.count("\n") == 1
    assert json.loads(state_json) == state_dict
    assert json.loads(event_jsonl) == event_dict
    assert "日本語" in event_jsonl
    assert "\\u" not in event_jsonl


def test_success_writes_state_and_exactly_one_event_and_reports_byte_counts(
    tmp_path: Path,
) -> None:
    value = transition()
    value_targets = targets(tmp_path)

    result = persist_workflow_execution_transition(value, value_targets)

    expected_state = serialize_workflow_execution_state_json(value.next_state).encode()
    expected_event = serialize_runtime_step_event_jsonl(value.event).encode()
    assert value_targets.state_path.read_bytes() == expected_state
    assert value_targets.events_path.read_bytes() == expected_event
    assert result.state_path == value_targets.state_path
    assert result.events_path == value_targets.events_path
    assert result.state_bytes_written == len(expected_state)
    assert result.event_bytes_appended == len(expected_event)
    assert value.next_state == transition().next_state
    with pytest.raises(FrozenInstanceError):
        result.state_bytes_written = 0  # type: ignore[misc]


def test_repeated_calls_replace_state_and_append_identical_event_records(
    tmp_path: Path,
) -> None:
    value = transition()
    value_targets = targets(tmp_path)
    old_state = b"old-state\x00"
    old_events = b'{"old":true}\n'
    value_targets.state_path.write_bytes(old_state)
    value_targets.events_path.write_bytes(old_events)

    persist_workflow_execution_transition(value, value_targets)
    persist_workflow_execution_transition(value, value_targets)

    record = serialize_runtime_step_event_jsonl(value.event).encode()
    assert (
        value_targets.state_path.read_bytes()
        == serialize_workflow_execution_state_json(value.next_state).encode()
    )
    assert value_targets.events_path.read_bytes() == old_events + record + record


@pytest.mark.parametrize(
    "invalid_transition",
    [
        lambda value: replace(value, event=replace(value.event, workflow_id="other")),
        lambda value: replace(value, event=replace(value.event, step_id="other")),
        lambda value: replace(value, event=replace(value.event, next_status="failed")),
    ],
)
def test_transition_mismatch_fails_before_mutation(
    tmp_path: Path,
    invalid_transition: object,
) -> None:
    value_targets = targets(tmp_path)
    value_targets.state_path.write_bytes(b"state-before")
    value_targets.events_path.write_bytes(b"events-before")

    with pytest.raises(WorkflowExecutionPersistenceInputError):
        persist_workflow_execution_transition(
            invalid_transition(transition()),  # type: ignore[operator]
            value_targets,
        )

    assert value_targets.state_path.read_bytes() == b"state-before"
    assert value_targets.events_path.read_bytes() == b"events-before"


def test_same_target_and_directory_targets_fail_before_mutation(tmp_path: Path) -> None:
    path = tmp_path / "same"
    with pytest.raises(WorkflowExecutionPersistenceInputError):
        persist_workflow_execution_transition(
            transition(),
            WorkflowExecutionPersistenceTargets(path, path),
        )
    with pytest.raises(WorkflowExecutionPersistenceInputError):
        persist_workflow_execution_transition(
            transition(),
            WorkflowExecutionPersistenceTargets(tmp_path, tmp_path / "events"),
        )


def test_capture_failure_changes_neither_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value_targets = targets(tmp_path)
    value_targets.state_path.write_bytes(b"state-before")
    value_targets.events_path.write_bytes(b"events-before")

    def fail_capture(_: Path) -> persistence._OriginalTarget:
        raise OSError("synthetic")

    monkeypatch.setattr(persistence, "_capture_original_target", fail_capture)

    with pytest.raises(WorkflowExecutionPersistenceError):
        persist_workflow_execution_transition(transition(), value_targets)

    assert value_targets.state_path.read_bytes() == b"state-before"
    assert value_targets.events_path.read_bytes() == b"events-before"


def test_second_original_target_capture_failure_changes_neither_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value_targets = targets(tmp_path)
    value_targets.state_path.write_bytes(b"state-before")
    value_targets.events_path.write_bytes(b"events-before")
    original_capture = persistence._capture_original_target
    calls = 0

    def fail_second_capture(path: Path) -> persistence._OriginalTarget:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic")
        return original_capture(path)

    monkeypatch.setattr(persistence, "_capture_original_target", fail_second_capture)

    with pytest.raises(WorkflowExecutionPersistenceError):
        persist_workflow_execution_transition(transition(), value_targets)

    assert value_targets.state_path.read_bytes() == b"state-before"
    assert value_targets.events_path.read_bytes() == b"events-before"


def test_state_serialization_failure_changes_neither_target(tmp_path: Path) -> None:
    value = transition()
    invalid_index = object()
    invalid_transition = replace(
        value,
        previous_state=replace(value.previous_state, current_step_index=invalid_index),
        next_state=replace(value.next_state, current_step_index=invalid_index),
        event=replace(value.event, step_index=invalid_index),
    )
    value_targets = targets(tmp_path)
    value_targets.state_path.write_bytes(b"state-before")
    value_targets.events_path.write_bytes(b"events-before")

    with pytest.raises(TypeError):
        persist_workflow_execution_transition(invalid_transition, value_targets)

    assert value_targets.state_path.read_bytes() == b"state-before"
    assert value_targets.events_path.read_bytes() == b"events-before"


def test_event_append_failure_restores_exact_original_binary_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value_targets = targets(tmp_path)
    old_state = b"state-before\x00"
    old_events = b"events-before\xff"
    value_targets.state_path.write_bytes(old_state)
    value_targets.events_path.write_bytes(old_events)

    def fail_append(path: Path, contents: bytes) -> None:
        with path.open("ab") as event_file:
            event_file.write(contents[:3])
        raise OSError("synthetic append failure")

    monkeypatch.setattr(persistence, "_append_event_bytes", fail_append)

    with pytest.raises(WorkflowExecutionPersistenceError) as error:
        persist_workflow_execution_transition(transition(), value_targets)

    assert str(error.value) == "workflow execution persistence failed"
    assert value_targets.state_path.read_bytes() == old_state
    assert value_targets.events_path.read_bytes() == old_events
    assert not (tmp_path / ".workflow-state.json.tmp").exists()


def test_absent_targets_are_removed_when_event_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value_targets = targets(tmp_path)

    def fail_append(path: Path, contents: bytes) -> None:
        path.write_bytes(contents)
        raise OSError("synthetic")

    monkeypatch.setattr(persistence, "_append_event_bytes", fail_append)

    with pytest.raises(WorkflowExecutionPersistenceError):
        persist_workflow_execution_transition(transition(), value_targets)

    assert not value_targets.state_path.exists()
    assert not value_targets.events_path.exists()


def test_state_replace_failure_restores_both_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value_targets = targets(tmp_path)
    value_targets.state_path.write_bytes(b"state-before")
    value_targets.events_path.write_bytes(b"events-before")

    def fail_replace(path: Path, _: bytes) -> None:
        path.write_bytes(b"partial-state")
        raise OSError("synthetic")

    monkeypatch.setattr(persistence, "_replace_state_bytes", fail_replace)

    with pytest.raises(WorkflowExecutionPersistenceError):
        persist_workflow_execution_transition(transition(), value_targets)

    assert value_targets.state_path.read_bytes() == b"state-before"
    assert value_targets.events_path.read_bytes() == b"events-before"


def test_rollback_attempts_both_targets_and_raises_distinct_safe_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value_targets = targets(tmp_path)
    value_targets.state_path.write_bytes(b"state-before")
    value_targets.events_path.write_bytes(b"events-before")
    restored_paths: list[Path] = []
    original_restore = persistence._restore_target

    def fail_append(path: Path, contents: bytes) -> None:
        path.write_bytes(contents)
        raise OSError("primary")

    def partly_fail_restore(path: Path, original: persistence._OriginalTarget) -> None:
        restored_paths.append(path)
        if path == value_targets.events_path:
            raise OSError("rollback events")
        original_restore(path, original)

    monkeypatch.setattr(persistence, "_append_event_bytes", fail_append)
    monkeypatch.setattr(persistence, "_restore_target", partly_fail_restore)

    with pytest.raises(WorkflowExecutionPersistenceRollbackError) as error:
        persist_workflow_execution_transition(transition(), value_targets)

    assert str(error.value) == "workflow execution persistence rollback failed"
    assert restored_paths == [value_targets.events_path, value_targets.state_path]
    assert error.value.primary_failure.operation == "persistence"
    assert error.value.rollback_failures == (
        persistence.WorkflowExecutionPersistenceFailureDetail("restore_events"),
    )
    assert value_targets.state_path.read_bytes() == b"state-before"
    assert value_targets.events_path.read_bytes() != b"events-before"
