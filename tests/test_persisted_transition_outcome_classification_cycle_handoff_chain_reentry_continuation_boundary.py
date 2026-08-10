"""Focused tests for the Phase 128 persisted-transition classification handoff."""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
import json
from dataclasses import replace
from pathlib import Path
from pathlib import Path as _TestPath
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationCompatibilityError,
    PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationError,
    WorkflowProgressionDecision,
    route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary,
    route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class PersistenceChild(WorkflowExecutionPersistenceResult):
    pass


class OutcomeChild(PersistedExecutionOutcome):
    pass


class WorkflowChild(WorkflowDefinition):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "a", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "b", "instructions": "two"},
                {"id": "three", "name": "Three", "employee": "c", "instructions": "three"},
                {"id": "four", "name": "Four", "employee": "d", "instructions": "four"},
            ],
        }
    )


def predecessor_event(step_id: str, step_index: int, employee: str) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        step_index,
        employee,
        "running",
        "succeeded",
        "openai",
        None,
        f"response-{step_id}",
        f"request-{step_id}",
        f"output-{step_id}",
        None,
    )


def terminal_event(status: str = "succeeded", **changes: object) -> RuntimeStepEvent:
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "w",
        "three",
        3,
        "c",
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response-three" if status == "succeeded" else None,
        "request-three",
        "output-three" if status == "succeeded" else None,
        None if status == "succeeded" else "safe failure",
    )
    return replace(event, **changes)


def setup(
    tmp_path: Path, status: str = "succeeded"
) -> tuple[Path, Path, WorkflowExecutionPersistenceResult, WorkflowDefinition, bytes, bytes]:
    supplied_workflow = workflow()
    state = WorkflowExecutionState(
        "w",
        status,
        "three",
        3,
        "c",
        ("one", "two", "three") if status == "succeeded" else ("one", "two"),
        None if status == "succeeded" else "api_error",
    )
    events = (
        predecessor_event("one", 1, "a"),
        predecessor_event("two", 2, "b"),
        terminal_event(status),
    )
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = "".join(serialize_runtime_step_event_jsonl(event) for event in events).encode("utf-8")
    terminal_bytes = serialize_runtime_step_event_jsonl(events[-1]).encode("utf-8")
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(
        state_path,
        events_path,
        len(state_bytes),
        len(terminal_bytes),
    )
    return state_path, events_path, result, supplied_workflow, state_bytes, event_bytes


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete", "w", "four", 4, "d", None, None, None, "last_step_succeeded"
    )


def failure_stop() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "w", "three", 3, "c", "api_error")


def expected_outcome(status: str) -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        "three",
        3,
        "c",
        None if status == "succeeded" else "api_error",
    )


def call(result: object, supplied_workflow: object, state: object, events: object, dependency: object) -> object:
    return route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary(
        result,
        supplied_workflow,
        state,
        events,
        phase121_function=dependency,
    )


def rewrite_event(events: Path, index: int, **changes: object) -> None:
    lines = events.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[index])
    payload.update(changes)
    lines[index] = json.dumps(payload, separators=(",", ":"))
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")


def rewrite_state(state: Path, **changes: object) -> None:
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload.update(changes)
    state.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def assert_rejected(
    result: object,
    supplied_workflow: object,
    state: object,
    events: object,
    classification: str,
    dependency: object,
) -> None:
    with pytest.raises(
        PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationCompatibilityError
    ) as caught:
        call(result, supplied_workflow, state, events, dependency)
    assert type(caught.value) is PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationCompatibilityError
    assert caught.value.detail.classification == classification


def test_public_signature_and_default_identity() -> None:
    signature = inspect.signature(
        route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary
    )
    parameters = list(signature.parameters.values())
    assert [item.name for item in parameters[:4]] == ["result", "workflow", "state_path", "events_path"]
    assert [item.kind for item in parameters[:4]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 4
    assert all(item.annotation is object for item in parameters[:4])
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[4].name == "phase121_function"
    assert parameters[4].default is route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary


def test_source_audit_uses_only_public_phase121_dependency() -> None:
    source = Path(
        "src/ai_office/engine/persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert "route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary" in source
    assert "phase114" not in source.lower()
    assert "route_persisted_transition_outcome_classification_cycle_reentry_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_valid_routes_delegate_once_in_canonical_identity_order_and_return_exact_outcome(
    tmp_path: Path, status: str
) -> None:
    state, events, result, supplied_workflow, before_state, before_events = setup(tmp_path, status)
    expected = expected_outcome(status)
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        assert all(actual is wanted for actual, wanted in zip(args, (result, supplied_workflow, state, events), strict=True))
        return expected

    assert call(result, supplied_workflow, state, events, dependency) is expected
    assert calls == [(result, supplied_workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "bad",
    [
        SimpleNamespace(),
        object(),
        WorkflowProgressionDecision("prepare_next_step", "w", "three", 3, "c", "four", 4, "d", "next_step_available"),
    ],
)
def test_unsupported_result_types_are_rejected_before_phase121(
    tmp_path: Path, bad: object
) -> None:
    state, events, _, supplied_workflow, *_ = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    classification = "completion_contract" if type(bad) is WorkflowProgressionDecision else "result_type"
    assert_rejected(bad, supplied_workflow, state, events, classification, dependency)
    assert calls == 0


def test_persistence_result_subclass_and_attribute_substitute_are_rejected_before_phase121(
    tmp_path: Path,
) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path)
    bad_values = [
        PersistenceChild(result.state_path, result.events_path, result.state_bytes_written, result.event_bytes_appended),
        SimpleNamespace(
            state_path=state,
            events_path=events,
            state_bytes_written=result.state_bytes_written,
            event_bytes_appended=result.event_bytes_appended,
        ),
    ]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    for bad in bad_values:
        assert_rejected(bad, supplied_workflow, state, events, "result_type", dependency)
    assert calls == 0


def test_exact_workflow_and_workflow_step_models_are_required(tmp_path: Path) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path)
    substitute = SimpleNamespace(
        id="w", name="W", description="D", steps=supplied_workflow.steps
    )
    subclass = WorkflowChild.model_validate(supplied_workflow.model_dump())
    malformed_step = supplied_workflow.model_copy(
        update={"steps": [SimpleNamespace(**supplied_workflow.steps[0].model_dump()), *supplied_workflow.steps[1:]]}
    )
    for bad in (substitute, subclass, malformed_step):
        assert_rejected(result, bad, state, events, "workflow_definition", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize(
    ("field", "bad"),
    [("id", 1), ("name", 1), ("description", 1), ("steps", tuple())],
)
def test_workflow_builtin_fields_are_revalidated(
    tmp_path: Path, field: str, bad: object
) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path)
    malformed = WorkflowDefinition.model_construct(
        **(supplied_workflow.model_dump() | {field: bad})
    )
    assert_rejected(result, malformed, state, events, "workflow_definition", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("field", ["id", "name", "employee", "instructions"])
def test_workflow_step_builtin_fields_are_revalidated(tmp_path: Path, field: str) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path)
    step = supplied_workflow.steps[0].model_copy(update={field: 1})
    malformed = supplied_workflow.model_copy(update={"steps": [step, *supplied_workflow.steps[1:]]})
    assert_rejected(result, malformed, state, events, "workflow_definition", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("state_path", Path("different-state")),
        ("events_path", Path("different-events")),
        ("state_bytes_written", True),
        ("state_bytes_written", 0),
        ("state_bytes_written", -1),
        ("state_bytes_written", 1.0),
        ("event_bytes_appended", True),
        ("event_bytes_appended", 0),
        ("event_bytes_appended", -1),
        ("event_bytes_appended", 1.0),
    ],
)
def test_persistence_result_identity_type_and_positive_counts_are_exact(
    tmp_path: Path, field: str, bad: object
) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path)
    bad_result = replace(result, **{field: bad})
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected_outcome("succeeded")

    assert_rejected(bad_result, supplied_workflow, state, events, "persistence_contract", dependency)
    assert calls == 0


@pytest.mark.parametrize("field", ["state_bytes_written", "event_bytes_appended"])
def test_positive_but_mismatched_persistence_byte_counts_are_rejected_before_phase121(
    tmp_path: Path, field: str
) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path)
    bad_result = replace(result, **{field: getattr(result, field) + 1})
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected_outcome("succeeded")

    assert_rejected(bad_result, supplied_workflow, state, events, "persistence_contract", dependency)
    assert calls == 0


@pytest.mark.parametrize("index", [1, 2])
def test_current_step_index_must_be_at_least_three(tmp_path: Path, index: int) -> None:
    supplied_workflow = workflow()
    current = supplied_workflow.steps[index - 1]
    state_model = WorkflowExecutionState(
        "w", "succeeded", current.id, index, current.employee,
        tuple(step.id for step in supplied_workflow.steps[:index]), None,
    )
    prior = tuple(
        predecessor_event(step.id, position, step.employee)
        for position, step in enumerate(supplied_workflow.steps[: index - 1], 1)
    )
    terminal = RuntimeStepEvent(
        "step_succeeded", "w", current.id, index, current.employee,
        "running", "succeeded", "openai", None, "response", "request", "output", None,
    )
    state_bytes = serialize_workflow_execution_state_json(state_model).encode("utf-8")
    event_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in (*prior, terminal)
    ).encode("utf-8")
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(state_bytes)
    events.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(
        state, events, len(state_bytes), len(serialize_runtime_step_event_jsonl(terminal).encode("utf-8"))
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected_outcome("succeeded")

    assert_rejected(result, supplied_workflow, state, events, "persistence_contract", dependency)
    assert calls == 0


@pytest.mark.parametrize(
    "mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"]
)
def test_terminal_and_predecessor_history_matrix_rejects_before_phase121(
    tmp_path: Path, mode: str
) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path)
    lines = events.read_text(encoding="utf-8").splitlines()
    if mode == "duplicate":
        events.write_text("\n".join([lines[0], lines[0], lines[1], lines[2]]) + "\n", encoding="utf-8")
    elif mode == "missing":
        events.write_bytes(b"")
    elif mode == "reordered":
        events.write_text("\n".join([lines[1], lines[0], lines[2]]) + "\n", encoding="utf-8")
    elif mode == "unrelated":
        payload = json.loads(lines[1])
        payload["step_id"] = "xxx"
        lines[1] = json.dumps(payload, separators=(",", ":"))
        events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif mode == "malformed":
        events.write_bytes(b"{not-json}\n")
    else:
        events.write_text("\n".join(lines + [lines[2]]) + "\n", encoding="utf-8")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected_outcome("succeeded")

    assert_rejected(result, supplied_workflow, state, events, "terminal_contract", dependency)
    assert calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_id": "x"},
        {"step_id": "xxx"},
        {"step_index": 4},
        {"employee_id": "x"},
        {"provider": "other"},
        {"request_id": 123},
        {"response_id": None},
        {"output_text": None},
        {"event_type": "step_failed", "next_status": "failed"},
        {"next_status": "failed"},
        {"failure_category": "api_error"},
    ],
)
def test_invalid_terminal_success_event_linkage_is_rejected_before_phase121(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path, "succeeded")
    rewrite_event(events, 2, **changes)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected_outcome("succeeded")

    classification = "persistence_contract" if changes == {"provider": "other"} else "terminal_contract"
    assert_rejected(result, supplied_workflow, state, events, classification, dependency)
    assert calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_id": "x"},
        {"step_id": "xxx"},
        {"step_index": 4},
        {"employee_id": "x"},
        {"provider": "other"},
        {"request_id": 123},
        {"failure_category": None},
        {"message": None},
        {"event_type": "step_succeeded", "next_status": "succeeded"},
        {"next_status": "succeeded"},
    ],
)
def test_invalid_terminal_failure_event_linkage_is_rejected_before_phase121(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path, "failed")
    rewrite_event(events, 2, **changes)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected_outcome("failed")

    classification = "persistence_contract" if changes == {"provider": "other"} else "terminal_contract"
    assert_rejected(result, supplied_workflow, state, events, classification, dependency)
    assert calls == 0


def test_mismatched_persisted_terminal_state_is_rejected_before_phase121(tmp_path: Path) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path, "succeeded")
    failed_state = WorkflowExecutionState("w", "failed", "three", 3, "c", ("one", "two"), "api_error")
    state.write_text(serialize_workflow_execution_state_json(failed_state), encoding="utf-8")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected_outcome("succeeded")

    assert_rejected(result, supplied_workflow, state, events, "terminal_contract", dependency)
    assert calls == 0


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_exact_outcome_success_and_failure_are_accepted_by_identity(
    tmp_path: Path, status: str
) -> None:
    state, events, result, supplied_workflow, before_state, before_events = setup(tmp_path, status)
    expected = expected_outcome(status)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    returned = call(result, supplied_workflow, state, events, dependency)
    assert returned is expected
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "bad",
    [
        OutcomeChild("persisted_success", "w", "three", 3, "c", None),
        SimpleNamespace(outcome="persisted_success"),
        WorkflowProgressionDecision("workflow_complete", "w", "four", 4, "d", None, None, None, "last_step_succeeded"),
        object(),
    ],
)
def test_dependency_outcome_must_be_exact_supported_model(tmp_path: Path, bad: object) -> None:
    state, events, result, supplied_workflow, before_state, before_events = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    assert_rejected(result, supplied_workflow, state, events, "outcome_contract", dependency)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("outcome", "persisted_failure"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", 4),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("failure_category", "api_error"),
    ],
)
def test_dependency_outcome_linkage_and_exact_fields_are_revalidated(
    tmp_path: Path, field: str, bad: object
) -> None:
    state, events, result, supplied_workflow, before_state, before_events = setup(tmp_path)
    malformed = replace(expected_outcome("succeeded"), **{field: bad})
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return malformed

    assert_rejected(result, supplied_workflow, state, events, "outcome_contract", dependency)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_phase121_error_identity_is_preserved_after_compensation(
    tmp_path: Path, mutation: str
) -> None:
    state, events, result, supplied_workflow, before_state, before_events = setup(tmp_path)
    safe = PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationError("safe")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        raise safe

    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationError) as caught:
        call(result, supplied_workflow, state, events, dependency)
    assert caught.value is safe
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_phase121_error_is_sanitized_and_compensated(
    tmp_path: Path, mutation: str
) -> None:
    state, events, result, supplied_workflow, before_state, before_events = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        raise RuntimeError("secret detail")

    with pytest.raises(
        PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationCompatibilityError
    ) as caught:
        call(result, supplied_workflow, state, events, dependency)
    assert type(caught.value) is PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationCompatibilityError
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_normal_outcome_after_target_mutation_is_rejected_and_compensated(
    tmp_path: Path, mutation: str
) -> None:
    state, events, result, supplied_workflow, before_state, before_events = setup(tmp_path)
    expected = expected_outcome("succeeded")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        return expected

    assert_rejected(result, supplied_workflow, state, events, "outcome_contract", dependency)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("failed_targets", [{"state"}, {"events"}, {"state", "events"}])
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_targets: set[str]
) -> None:
    state, events, result, supplied_workflow, before_state, before_events = setup(tmp_path)
    original_write = _TestPath.write_bytes
    restoration_attempts: list[str] = []
    armed = False

    def write(path: Path, data: bytes) -> int:
        nonlocal armed
        if armed and data == before_state:
            restoration_attempts.append("state")
            if "state" in failed_targets:
                raise OSError("state rollback detail")
        if armed and data == before_events:
            restoration_attempts.append("events")
            if "events" in failed_targets:
                raise OSError("events rollback detail")
        return original_write(path, data)

    monkeypatch.setattr(_TestPath, "write_bytes", write)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls, armed
        calls += 1
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        armed = True
        return expected_outcome("succeeded")

    assert_rejected(result, supplied_workflow, state, events, "dependency_rollback", dependency)
    assert calls == 1
    assert set(restoration_attempts) == {"state", "events"}


def test_stop_routes_are_identity_preserving_and_zero_call(tmp_path: Path) -> None:
    complete_dir = tmp_path / "complete"
    complete_dir.mkdir()
    complete_state, complete_events, _, supplied_workflow, _, _ = setup(complete_dir)
    # The setup helper uses the continuation terminal; construct the final completed stop separately.
    final_state = WorkflowExecutionState("w", "succeeded", "four", 4, "d", ("one", "two", "three", "four"), None)
    final_events = (
        predecessor_event("one", 1, "a"),
        predecessor_event("two", 2, "b"),
        terminal_event("succeeded"),
        RuntimeStepEvent("step_succeeded", "w", "four", 4, "d", "running", "succeeded", "openai", None, "response-four", "request-four", "output-four", None),
    )
    complete_state.write_bytes(serialize_workflow_execution_state_json(final_state).encode())
    complete_events.write_bytes("".join(serialize_runtime_step_event_jsonl(event) for event in final_events).encode())
    before_complete_state, before_complete_events = complete_state.read_bytes(), complete_events.read_bytes()
    failed_dir = tmp_path / "failed"
    failed_dir.mkdir()
    failed_state, failed_events, _, _, before_failed_state, before_failed_events = setup(failed_dir, "failed")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        pytest.fail("Phase 121 must not be called on stop routes")

    completion_value = completion()
    failure_value = failure_stop()
    assert call(completion_value, supplied_workflow, complete_state, complete_events, dependency) is completion_value
    assert call(failure_value, supplied_workflow, failed_state, failed_events, dependency) is failure_value
    assert calls == 0
    assert (complete_state.read_bytes(), complete_events.read_bytes()) == (before_complete_state, before_complete_events)
    assert (failed_state.read_bytes(), failed_events.read_bytes()) == (before_failed_state, before_failed_events)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("decision", "prepare_next_step"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", True),
        ("current_step_index", 3),
        ("current_employee_id", "other"),
        ("next_step_id", "four"),
        ("next_step_index", 4),
        ("next_employee_id", "d"),
        ("reason", "wrong"),
    ],
)
def test_malformed_completion_is_rejected_with_zero_dependency_calls(
    tmp_path: Path, field: str, bad: object
) -> None:
    state, events, _, supplied_workflow, *_ = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    value = replace(completion(), **{field: bad})
    assert_rejected(value, supplied_workflow, state, events, "completion_contract", dependency)
    assert calls == 0


@pytest.mark.parametrize("value", [PersistedExecutionOutcome("persisted_success", "w", "three", 3, "c", None), failure_stop()])
def test_malformed_or_unsupported_failure_stop_is_rejected_zero_call(
    tmp_path: Path, value: PersistedExecutionOutcome
) -> None:
    state, events, _, supplied_workflow, *_ = setup(tmp_path, "failed")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    if value.outcome == "persisted_failure":
        value = replace(value, failure_category=None)
    assert_rejected(value, supplied_workflow, state, events, "failure_contract", dependency)
    assert calls == 0


@pytest.mark.parametrize("target", ["state", "events"])
def test_missing_and_directory_targets_are_rejected_before_phase121(
    tmp_path: Path, target: str
) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path)
    selected = state if target == "state" else events
    selected.unlink()
    assert_rejected(result, supplied_workflow, state, events, f"{target[:-1] if target == 'events' else target}_target", lambda *_: pytest.fail("called"))
    selected.mkdir()
    assert_rejected(result, supplied_workflow, state, events, f"{target[:-1] if target == 'events' else target}_target", lambda *_: pytest.fail("called"))


def test_same_target_conflict_and_noncallable_dependency_are_zero_call(tmp_path: Path) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path)
    conflict = replace(result, events_path=state, event_bytes_appended=result.event_bytes_appended)
    assert_rejected(conflict, supplied_workflow, state, state, "target_conflict", lambda *_: pytest.fail("called"))
    assert_rejected(result, supplied_workflow, state, events, "persistence_contract", None)


@pytest.mark.parametrize("target", ["state", "events"])
def test_target_read_and_is_file_oserrors_are_safely_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path)
    selected = state if target == "state" else events
    original_is_file = _TestPath.is_file

    def raising_is_file(path: Path) -> bool:
        if path is selected:
            raise OSError("target detail")
        return original_is_file(path)

    monkeypatch.setattr(_TestPath, "is_file", raising_is_file)
    assert_rejected(result, supplied_workflow, state, events, f"{target[:-1] if target == 'events' else target}_target", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("target", ["state", "events"])
def test_target_read_bytes_oserrors_are_safely_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    state, events, result, supplied_workflow, *_ = setup(tmp_path)
    selected = state if target == "state" else events
    original_read_bytes = _TestPath.read_bytes

    def raising_read_bytes(path: Path) -> bytes:
        if path is selected:
            raise OSError("read detail")
        return original_read_bytes(path)

    monkeypatch.setattr(_TestPath, "read_bytes", raising_read_bytes)
    assert_rejected(result, supplied_workflow, state, events, f"{target[:-1] if target == 'events' else target}_target", lambda *_: pytest.fail("called"))


def test_empty_success_terminal_output_delegates_to_injected_phase121_once(tmp_path: Path) -> None:
    state, events, result, supplied_workflow, before_state, _ = setup(tmp_path, "succeeded")
    empty_terminal = replace(terminal_event("succeeded"), output_text="")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (
            predecessor_event("one", 1, "a"),
            predecessor_event("two", 2, "b"),
            empty_terminal,
        )
    )
    events.write_bytes(event_bytes)
    result = replace(
        result,
        event_bytes_appended=len(serialize_runtime_step_event_jsonl(empty_terminal).encode("utf-8")),
    )
    expected = expected_outcome("succeeded")
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    returned = call(result, supplied_workflow, state, events, dependency)
    assert returned is expected
    assert calls == [(result, supplied_workflow, state, events)]
    assert state.read_bytes() == before_state
    assert events.read_bytes() == event_bytes
