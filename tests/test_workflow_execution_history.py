"""Tests for strict, read-only workflow history loading."""

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionDataError,
    WorkflowExecutionHistoryInconsistencyError,
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceTargets,
    build_runtime_step_event_dict,
    build_workflow_execution_state_dict,
    load_workflow_execution_history,
    parse_runtime_step_event,
    parse_workflow_execution_state,
)


def targets(tmp_path: Path) -> WorkflowExecutionPersistenceTargets:
    return WorkflowExecutionPersistenceTargets(
        state_path=tmp_path / "workflow-state.json",
        events_path=tmp_path / "runtime-events.jsonl",
    )


def succeeded_state() -> WorkflowExecutionState:
    return WorkflowExecutionState(
        "workflow", "succeeded", "step", 1, "employee", ("old", "step"), None
    )


def succeeded_event() -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "workflow",
        "step",
        1,
        "employee",
        "running",
        "succeeded",
        "provider",
        None,
        "response",
        None,
        "日本語",
        None,
    )


def failed_state(category: str) -> WorkflowExecutionState:
    return WorkflowExecutionState(
        "workflow", "failed", "step", 1, "employee", ("old", "old"), category  # type: ignore[arg-type]
    )


def failed_event(category: str) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_failed",
        "workflow",
        "step",
        1,
        "employee",
        "running",
        "failed",
        "provider",
        category,  # type: ignore[arg-type]
        None,
        None,
        None,
        "safe failure",
    )


def write_history(
    value: WorkflowExecutionPersistenceTargets,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> None:
    value.state_path.write_text(json.dumps(build_workflow_execution_state_dict(state)))
    value.events_path.write_text(
        "".join(
            json.dumps(build_runtime_step_event_dict(event)) + "\n" for event in events
        )
    )


def test_loads_immutable_succeeded_history_with_unicode_and_empty_output(
    tmp_path: Path,
) -> None:
    value = targets(tmp_path)
    event = replace(succeeded_event(), output_text="")
    write_history(value, succeeded_state(), (event,))

    loaded = load_workflow_execution_history(value)

    assert loaded == LoadedWorkflowExecutionHistory(succeeded_state(), (event,))
    assert loaded.events[0].output_text == ""
    with pytest.raises(FrozenInstanceError):
        loaded.state = succeeded_state()  # type: ignore[misc]
    assert load_workflow_execution_history(value) == loaded


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
def test_loads_every_failed_category(category: str, tmp_path: Path) -> None:
    value = targets(tmp_path)
    state = failed_state(category)
    event = failed_event(category)
    write_history(value, state, (event,))

    assert load_workflow_execution_history(value) == LoadedWorkflowExecutionHistory(
        state, (event,)
    )


def test_preserves_event_and_completed_step_order_and_duplicates(
    tmp_path: Path,
) -> None:
    value = targets(tmp_path)
    first = succeeded_event()
    state = WorkflowExecutionState(
        "workflow", "succeeded", "step", 1, "employee", ("old", "old", "step"), None
    )
    write_history(value, state, (first, first))

    loaded = load_workflow_execution_history(value)

    assert loaded.events == (first, first)
    assert loaded.state.completed_step_ids == ("old", "old", "step")


@pytest.mark.parametrize("separator", ["\u2028", "\u2029"])
def test_success_output_text_with_unicode_line_separator_is_preserved(
    separator: str, tmp_path: Path
) -> None:
    value = targets(tmp_path)
    event = replace(succeeded_event(), output_text=f"before{separator}after")
    write_history(value, succeeded_state(), (event,))

    assert load_workflow_execution_history(value).events == (event,)


def test_failure_message_with_unicode_line_separator_is_preserved(
    tmp_path: Path,
) -> None:
    value = targets(tmp_path)
    state = failed_state("api_error")
    event = replace(failed_event("api_error"), message="before\u2028after")
    write_history(value, state, (event,))

    assert load_workflow_execution_history(value).events == (event,)


def test_lf_delimited_events_preserve_order(tmp_path: Path) -> None:
    value = targets(tmp_path)
    first = replace(succeeded_event(), output_text="first")
    final = replace(succeeded_event(), output_text="final")
    write_history(value, succeeded_state(), (first, final))

    assert load_workflow_execution_history(value).events == (first, final)


def test_partial_final_event_record_without_lf_is_rejected(tmp_path: Path) -> None:
    value = targets(tmp_path)
    value.state_path.write_text(json.dumps(build_workflow_execution_state_dict(succeeded_state())))
    value.events_path.write_text(json.dumps(build_runtime_step_event_dict(succeeded_event())))

    with pytest.raises(WorkflowExecutionDataError):
        load_workflow_execution_history(value)


def test_blank_event_record_is_rejected(tmp_path: Path) -> None:
    value = targets(tmp_path)
    value.state_path.write_text(json.dumps(build_workflow_execution_state_dict(succeeded_state())))
    value.events_path.write_text(
        json.dumps(build_runtime_step_event_dict(succeeded_event())) + "\n\n"
    )

    with pytest.raises(WorkflowExecutionDataError):
        load_workflow_execution_history(value)


def test_allows_empty_event_log_only_for_nonterminal_state(tmp_path: Path) -> None:
    value = targets(tmp_path)
    state = WorkflowExecutionState("workflow", "ready", "step", 1, "employee", (), None)
    write_history(value, state, ())

    assert load_workflow_execution_history(value) == LoadedWorkflowExecutionHistory(
        state, ()
    )


@pytest.mark.parametrize("missing", ["state_path", "events_path"])
def test_missing_target_is_a_safe_load_error(tmp_path: Path, missing: str) -> None:
    value = targets(tmp_path)
    if missing == "state_path":
        value.events_path.write_text("")
    else:
        value.state_path.write_text("{}")

    with pytest.raises(WorkflowExecutionLoadError, match="could not be loaded"):
        load_workflow_execution_history(value)


def test_directory_and_same_path_targets_are_rejected(tmp_path: Path) -> None:
    value = targets(tmp_path)
    value.state_path.mkdir()
    value.events_path.write_text("")
    with pytest.raises(WorkflowExecutionLoadError):
        load_workflow_execution_history(value)
    with pytest.raises(WorkflowExecutionLoadError):
        load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(value.events_path, value.events_path)
        )


def test_regular_file_symlink_targets_are_accepted(tmp_path: Path) -> None:
    actual = targets(tmp_path)
    write_history(actual, succeeded_state(), (succeeded_event(),))
    linked = WorkflowExecutionPersistenceTargets(
        state_path=tmp_path / "state-link.json",
        events_path=tmp_path / "events-link.jsonl",
    )
    linked.state_path.symlink_to(actual.state_path)
    linked.events_path.symlink_to(actual.events_path)

    assert load_workflow_execution_history(linked) == LoadedWorkflowExecutionHistory(
        succeeded_state(), (succeeded_event(),)
    )


@pytest.mark.parametrize("target_name", ["state_path", "events_path"])
def test_invalid_utf8_is_data_error(tmp_path: Path, target_name: str) -> None:
    value = targets(tmp_path)
    write_history(value, succeeded_state(), (succeeded_event(),))
    getattr(value, target_name).write_bytes(b"\xff")

    with pytest.raises(WorkflowExecutionDataError, match="persisted data is invalid"):
        load_workflow_execution_history(value)


@pytest.mark.parametrize("contents", ["", " \n\t "])
def test_empty_or_whitespace_state_is_rejected(contents: str, tmp_path: Path) -> None:
    value = targets(tmp_path)
    value.state_path.write_text(contents)
    value.events_path.write_text("")

    with pytest.raises(WorkflowExecutionDataError):
        load_workflow_execution_history(value)


@pytest.mark.parametrize(
    "contents",
    [
        "{",
        '{"workflow_id":"one","workflow_id":"two"}',
        "[]",
        "null",
    ],
)
def test_malformed_duplicate_or_non_object_state_is_rejected(
    contents: str, tmp_path: Path
) -> None:
    value = targets(tmp_path)
    value.state_path.write_text(contents)
    value.events_path.write_text("")

    with pytest.raises(WorkflowExecutionDataError):
        load_workflow_execution_history(value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.pop("workflow_id"),
        lambda data: data.update(unexpected=True),
        lambda data: data.update(status="unknown"),
        lambda data: data.update(last_failure_category="unknown"),
        lambda data: data.update(current_step_index=True),
        lambda data: data.update(current_step_index=0),
        lambda data: data.update(completed_step_ids="step"),
        lambda data: data.update(completed_step_ids=["step", 1]),
    ],
)
def test_invalid_state_fields_are_rejected(tmp_path: Path, mutate: object) -> None:
    value = targets(tmp_path)
    data = build_workflow_execution_state_dict(succeeded_state())
    mutate(data)  # type: ignore[operator]
    value.state_path.write_text(json.dumps(data))
    value.events_path.write_text("")

    with pytest.raises(WorkflowExecutionDataError):
        load_workflow_execution_history(value)


@pytest.mark.parametrize(
    "contents",
    [
        "{\n",
        '{"event_type":"step_succeeded","event_type":"step_failed"}\n',
        "\n",
        "{}\n\n",
        "[]\n",
    ],
)
def test_malformed_duplicate_blank_or_non_object_event_is_rejected(
    contents: str, tmp_path: Path
) -> None:
    value = targets(tmp_path)
    value.state_path.write_text(json.dumps(build_workflow_execution_state_dict(succeeded_state())))
    value.events_path.write_text(contents)

    with pytest.raises(WorkflowExecutionDataError):
        load_workflow_execution_history(value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.pop("event_type"),
        lambda data: data.update(unexpected=True),
        lambda data: data.update(event_type="other"),
        lambda data: data.update(previous_status="ready"),
        lambda data: data.update(next_status="failed"),
        lambda data: data.update(step_index=False),
        lambda data: data.update(provider=""),
        lambda data: data.update(request_id=1),
        lambda data: data.update(response_id=None),
        lambda data: data.update(output_text=None),
        lambda data: data.update(message="unexpected"),
    ],
)
def test_invalid_success_event_fields_and_semantics_are_rejected(
    mutate: object,
) -> None:
    data = build_runtime_step_event_dict(succeeded_event())
    mutate(data)  # type: ignore[operator]

    with pytest.raises(WorkflowExecutionDataError):
        parse_runtime_step_event(data)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(failure_category=None),
        lambda data: data.update(message=None),
        lambda data: data.update(response_id="response"),
        lambda data: data.update(output_text="output"),
    ],
)
def test_invalid_failure_event_semantics_are_rejected(mutate: object) -> None:
    data = build_runtime_step_event_dict(failed_event("api_error"))
    mutate(data)  # type: ignore[operator]

    with pytest.raises(WorkflowExecutionDataError):
        parse_runtime_step_event(data)


@pytest.mark.parametrize(
    "state,event",
    [
        (succeeded_state(), failed_event("api_error")),
        (failed_state("api_error"), succeeded_event()),
        (failed_state("transport_error"), failed_event("api_error")),
    ],
)
def test_terminal_state_and_event_must_agree(
    state: WorkflowExecutionState, event: RuntimeStepEvent, tmp_path: Path
) -> None:
    value = targets(tmp_path)
    write_history(value, state, (event,))

    with pytest.raises(WorkflowExecutionHistoryInconsistencyError):
        load_workflow_execution_history(value)


@pytest.mark.parametrize(
    "event",
    [
        replace(succeeded_event(), workflow_id="other"),
        replace(succeeded_event(), step_id="other"),
        replace(succeeded_event(), step_index=2),
    ],
)
def test_cross_file_identity_mismatch_is_safe(
    event: RuntimeStepEvent, tmp_path: Path
) -> None:
    value = targets(tmp_path)
    write_history(value, succeeded_state(), (event,))

    with pytest.raises(WorkflowExecutionHistoryInconsistencyError) as caught:
        load_workflow_execution_history(value)

    text = str(caught.value)
    assert "other" not in text
    assert "provider" not in text
    assert str(value.state_path) not in text


def test_succeeded_state_requires_final_completed_step(tmp_path: Path) -> None:
    value = targets(tmp_path)
    state = WorkflowExecutionState(
        "workflow", "succeeded", "step", 1, "employee", (), None
    )
    write_history(value, state, (succeeded_event(),))

    with pytest.raises(WorkflowExecutionHistoryInconsistencyError):
        load_workflow_execution_history(value)


def test_pure_state_parser_preserves_null_and_rejects_coercion() -> None:
    data = build_workflow_execution_state_dict(succeeded_state())
    assert parse_workflow_execution_state(data).last_failure_category is None
    data["current_step_index"] = "1"
    with pytest.raises(WorkflowExecutionDataError):
        parse_workflow_execution_state(data)
