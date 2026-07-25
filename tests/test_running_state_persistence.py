"""Tests for explicit state-only running persistence."""

import json

import pytest

from ai_office.engine import PreparedStepExecutionStart
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceError,
    RunningStatePersistenceInputError,
    RunningStatePersistenceRollbackError,
    persist_prepared_running_state,
)


def start() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "system", "task", ()),
        WorkflowExecutionState(
            "workflow", "running", "step", 2, "employee", ("old", "old"), None
        ),
    )


def test_persists_only_exact_running_state(tmp_path) -> None:
    path = tmp_path / "state.json"
    assert persist_prepared_running_state(start(), path).state_bytes_written > 0
    assert json.loads(path.read_text())["status"] == "running"
    assert not (tmp_path / "runtime-events.jsonl").exists()


def test_replaces_existing_and_creates_missing_deterministically(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_bytes(b"old")
    persist_prepared_running_state(start(), path)
    first = path.read_bytes()
    persist_prepared_running_state(start(), path)
    assert path.read_bytes() == first
    missing = tmp_path / "missing.json"
    persist_prepared_running_state(start(), missing)
    assert missing.read_bytes() == first


def test_directory_target_is_rejected(tmp_path) -> None:
    with pytest.raises(RunningStatePersistenceInputError):
        persist_prepared_running_state(start(), tmp_path)


def test_write_failure_preserves_original(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state.json"
    path.write_bytes(b"old")
    monkeypatch.setattr(
        "ai_office.storage.running_state_persistence._replace_state_bytes",
        lambda *_: (_ for _ in ()).throw(OSError()),
    )
    with pytest.raises(RunningStatePersistenceError):
        persist_prepared_running_state(start(), path)
    assert path.read_bytes() == b"old"


def test_verification_failure_restores_existing_target(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state.json"
    path.write_bytes(b"old")
    monkeypatch.setattr(
        "ai_office.storage.running_state_persistence._replace_state_bytes",
        lambda path, _: path.write_bytes(b"wrong"),
    )
    with pytest.raises(RunningStatePersistenceError):
        persist_prepared_running_state(start(), path)
    assert path.read_bytes() == b"old"


def test_verification_failure_removes_new_target(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state.json"
    monkeypatch.setattr(
        "ai_office.storage.running_state_persistence._replace_state_bytes",
        lambda path, _: path.write_bytes(b"wrong"),
    )
    with pytest.raises(RunningStatePersistenceError):
        persist_prepared_running_state(start(), path)
    assert not path.exists()


def test_rollback_failure_has_safe_classification(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state.json"
    monkeypatch.setattr(
        "ai_office.storage.running_state_persistence._replace_state_bytes",
        lambda path, _: path.write_bytes(b"wrong"),
    )
    monkeypatch.setattr(
        "ai_office.storage.running_state_persistence._restore_target",
        lambda *_: (_ for _ in ()).throw(OSError()),
    )
    with pytest.raises(RunningStatePersistenceRollbackError) as caught:
        persist_prepared_running_state(start(), path)
    assert caught.value.detail.operation == "rollback"


@pytest.mark.parametrize(
    "state",
    [
        WorkflowExecutionState(
            "workflow", "succeeded", "step", 2, "employee", (), None
        ),
        WorkflowExecutionState(
            "workflow", "running", "step", 2, "employee", (), "api_error"
        ),
    ],
)
def test_rejects_invalid_running_state(tmp_path, state) -> None:
    value = PreparedStepExecutionStart(start().request, state)
    with pytest.raises(RunningStatePersistenceInputError):
        persist_prepared_running_state(value, tmp_path / "state.json")
