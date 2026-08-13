"""Focused tests for the Phase 107 cycle continuation boundary."""

# ruff: noqa: E501

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
    PersistedOutcomeClassificationDispatchContinuationError,
    PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError,
    WorkflowProgressionDecision,
    route_persisted_outcome_classification_dispatch_continuation_boundary,
    route_persisted_transition_outcome_classification_cycle_continuation_boundary,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def _setup(tmp_path: Path, status: str = "succeeded"):
    workflow = WorkflowDefinition.model_validate(
        {"id": "w", "name": "W", "description": "D", "steps": [
            {"id": "one", "name": "One", "employee": "a", "instructions": "x"},
        ]}
    )
    state_model = WorkflowExecutionState(
        "w", status, "one", 1, "a", ("one",) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error",
    )
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed", "w", "one", 1,
        "a", "running", status, "openai", None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None, "request",
        "out" if status == "succeeded" else None, None if status == "succeeded" else "safe",
    )
    state_bytes = serialize_workflow_execution_state_json(state_model).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(state_bytes)
    events.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(state, events, len(state_bytes), len(event_bytes))
    return state, events, result, workflow, state_bytes, event_bytes


def _completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded")


def _failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "w", "one", 1, "a", "api_error")


_SIX_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_BAD_SENTINEL = object()


def _setup_phase155(
    tmp_path: Path,
    status: str,
    *,
    step_index: int = 6,
    terminal_output: str = "output-six",
    bad_predecessor_output: object = _BAD_SENTINEL,
):
    workflow = WorkflowDefinition.model_validate(
        {"id": "w", "name": "W", "description": "D", "steps": [
            {"id": step_id, "name": step_id.capitalize(), "employee": step_id[0], "instructions": step_id}
            for step_id in _SIX_STEP_IDS
        ]}
    )
    completed = tuple(_SIX_STEP_IDS) if status == "succeeded" else tuple(_SIX_STEP_IDS[: step_index - 1])
    current_step_id = _SIX_STEP_IDS[step_index - 1]
    state_model = WorkflowExecutionState(
        "w", status, current_step_id, step_index, current_step_id[0], completed,
        None if status == "succeeded" else "api_error",
    )
    events = [
        RuntimeStepEvent("step_succeeded", "w", step_id, position, step_id[0], "running", "succeeded",
                         "other", None, f"response-{step_id}", None, "", None)
        for position, step_id in enumerate(_SIX_STEP_IDS[: step_index - 1], 1)
    ]
    if status == "succeeded":
        events.append(RuntimeStepEvent("step_succeeded", "w", current_step_id, step_index, current_step_id[0],
                                       "running", "succeeded", "openai", None, f"response-{current_step_id}",
                                       f"request-{current_step_id}", terminal_output, None))
    else:
        events.append(RuntimeStepEvent("step_failed", "w", current_step_id, step_index, current_step_id[0],
                                       "running", "failed", "openai", "api_error", None,
                                       f"request-{current_step_id}", None, "safe failure"))
    state_bytes = serialize_workflow_execution_state_json(state_model).encode()
    event_bytes = "".join(serialize_runtime_step_event_jsonl(event) for event in events).encode()
    if bad_predecessor_output is not _BAD_SENTINEL:
        lines = event_bytes.decode().splitlines()
        payload = json.loads(lines[0])
        payload["output_text"] = bad_predecessor_output
        lines[0] = json.dumps(payload, separators=(",", ":"))
        event_bytes = ("\n".join(lines) + "\n").encode()
    state, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(
        state, events_path, len(state_bytes), len(serialize_runtime_step_event_jsonl(events[-1]).encode())
    )
    return state, events_path, result, workflow, state_bytes, event_bytes


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_phase155_six_step_history_fallback_accepts_and_delegates_once(
    tmp_path: Path, status: str
) -> None:
    state, events, result, workflow, before_state, before_events = _setup_phase155(tmp_path, status)
    expected = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w", "six", 6, "s", None if status == "succeeded" else "api_error",
    )
    calls: list[tuple[object, ...]] = []
    def seam(*args: object) -> object:
        calls.append(args)
        return expected
    returned = route_persisted_transition_outcome_classification_cycle_continuation_boundary(
        result, workflow, state, events, phase100_function=seam
    )
    assert returned is expected
    assert calls == [(result, workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase155_fallback_rejects_none_predecessor_output_before_phase100(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup_phase155(tmp_path, "succeeded", bad_predecessor_output=None)
    calls = 0
    def seam(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError) as caught:
        route_persisted_transition_outcome_classification_cycle_continuation_boundary(
            result, workflow, state, events, phase100_function=seam
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0


def test_phase155_fallback_rejects_non_string_predecessor_output_before_phase100(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup_phase155(tmp_path, "succeeded", bad_predecessor_output=1)
    calls = 0
    def seam(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError) as caught:
        route_persisted_transition_outcome_classification_cycle_continuation_boundary(
            result, workflow, state, events, phase100_function=seam
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0


def test_phase155_fallback_requires_current_step_index_at_least_six(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup_phase155(tmp_path, "succeeded", step_index=5)
    calls = 0
    def seam(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError) as caught:
        route_persisted_transition_outcome_classification_cycle_continuation_boundary(
            result, workflow, state, events, phase100_function=seam
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0


def test_phase155_fallback_rejects_empty_terminal_success_output_at_final_index(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup_phase155(tmp_path, "succeeded", terminal_output="")
    calls = 0
    def seam(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError) as caught:
        route_persisted_transition_outcome_classification_cycle_continuation_boundary(
            result, workflow, state, events, phase100_function=seam
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0


def test_public_signature_and_default_identity() -> None:
    signature = inspect.signature(route_persisted_transition_outcome_classification_cycle_continuation_boundary)
    parameters = list(signature.parameters.values())
    assert [p.name for p in parameters[:4]] == ["result", "workflow", "state_path", "events_path"]
    assert [p.kind for p in parameters[:4]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 4
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[4].name == "phase100_function"
    assert parameters[4].default is route_persisted_outcome_classification_dispatch_continuation_boundary


def test_implementation_does_not_reference_phase_private_internals() -> None:
    source = _TestPath(
        "src/ai_office/engine/persisted_transition_outcome_classification_cycle_continuation_boundary.py"
    ).read_text()
    assert "_phase100" not in source
    assert "_phase93" not in source
    assert "route_persisted_outcome_classification_dispatch_continuation_boundary" in source


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_valid_routes_call_phase100_once_and_return_exact_object(tmp_path: Path, status: str) -> None:
    state, events, result, workflow, before_state, before_events = _setup(tmp_path, status)
    expected = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w", "one", 1, "a", None if status == "succeeded" else "api_error",
    )
    calls: list[tuple[object, ...]] = []
    def fake(*args: object) -> object:
        calls.append(args)
        return expected
    returned = route_persisted_transition_outcome_classification_cycle_continuation_boundary(
        result, workflow, state, events, phase100_function=fake
    )
    assert returned is expected
    assert calls == [(result, workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("bad", [SimpleNamespace(), object()])
def test_exact_result_model_and_path_identity_rejection(tmp_path: Path, bad: object) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError) as caught:
        route_persisted_transition_outcome_classification_cycle_continuation_boundary(bad, workflow, state, events)
    assert caught.value.detail.classification == "result_type"
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError):
        route_persisted_transition_outcome_classification_cycle_continuation_boundary(
            result, workflow, Path(str(state)), events
        )


def test_result_fields_and_positive_counts_are_exact(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    for field, value in (("state_path", Path(str(state))), ("events_path", Path(str(events))),
                         ("state_bytes_written", True), ("event_bytes_appended", 1.0),
                         ("state_bytes_written", 0)):
        bad = replace(result, **{field: value})
        with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError) as caught:
            route_persisted_transition_outcome_classification_cycle_continuation_boundary(bad, workflow, state, events)
        assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("value", [_completion(), _failure()])
def test_stop_routes_are_unchanged_and_zero_call(tmp_path: Path, value: object) -> None:
    status = "succeeded" if type(value) is WorkflowProgressionDecision else "failed"
    state, events, _, workflow, before_state, before_events = _setup(tmp_path, status)
    calls = 0
    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()
    assert route_persisted_transition_outcome_classification_cycle_continuation_boundary(
        value, workflow, state, events, phase100_function=fake
    ) is value
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_invalid_stop_and_direct_success_are_rejected_without_call(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    calls = 0
    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return _failure()
    for value in (
        replace(_completion(), current_step_index=2),
        replace(_failure(), failure_category="wrong"),
        PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", None),
        WorkflowProgressionDecision("next_step", "w", "one", 1, "a", "two", 2, "b", "x"),
    ):
        with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError):
            route_persisted_transition_outcome_classification_cycle_continuation_boundary(value, workflow, state, events, phase100_function=fake)
    assert calls == 0


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
def test_dependency_paths_compensate_and_do_not_retry(tmp_path: Path, mutation: str, kind: str) -> None:
    state, events, result, workflow, before_state, before_events = _setup(tmp_path)
    calls = 0
    safe = PersistedOutcomeClassificationDispatchContinuationError("dependency_error")
    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"mutated-state")
        if mutation in ("events", "both"):
            events.write_bytes(b"mutated-events")
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("boom")
        return object()
    with pytest.raises(Exception) as caught:
        route_persisted_transition_outcome_classification_cycle_continuation_boundary(result, workflow, state, events, phase100_function=fake)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
    if kind == "safe":
        assert caught.value is safe
    else:
        assert getattr(caught.value, "detail", None).classification in {"dependency_error", "outcome_contract"}


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_outcome_after_dependency_mutation_is_rejected_and_restored(
    tmp_path: Path, mutation: str
) -> None:
    state, events, result, workflow, before_state, before_events = _setup(tmp_path)
    expected = PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", None)
    calls = 0
    restored: set[str] = set()
    original_write = Path.write_bytes

    def write(path: Path, data: bytes) -> int:
        if data == before_state and path == state:
            restored.add("state")
        if data == before_events and path == events:
            restored.add("events")
        return original_write(path, data)

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        return expected

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "write_bytes", write)
    try:
        with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError) as caught:
            route_persisted_transition_outcome_classification_cycle_continuation_boundary(
                result, workflow, state, events, phase100_function=fake
            )
        assert caught.value.detail.classification == "outcome_contract"
    finally:
        monkeypatch.undo()
    assert calls == 1
    assert restored == {"state", "events"}
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_valid_outcome_mutation_rollback_failure_attempts_both_targets_without_retry(
    tmp_path: Path,
) -> None:
    state, events, result, workflow, before_state, before_events = _setup(tmp_path)
    expected = PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", None)
    calls = 0
    restoration_attempts: list[str] = []
    original_write = Path.write_bytes

    def write(path: Path, data: bytes) -> int:
        if data == before_state:
            restoration_attempts.append("state")
            raise OSError("state restore failed")
        if data == before_events:
            restoration_attempts.append("events")
        return original_write(path, data)

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        return expected

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "write_bytes", write)
    try:
        with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError) as caught:
            route_persisted_transition_outcome_classification_cycle_continuation_boundary(
                result, workflow, state, events, phase100_function=fake
            )
        assert caught.value.detail.classification == "dependency_rollback"
    finally:
        monkeypatch.undo()
    assert calls == 1
    assert restoration_attempts == ["state", "events"]


def test_outcome_subclass_and_substitute_rejected(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    class Sub(PersistedExecutionOutcome):
        pass
    for value in (Sub("persisted_success", "w", "one", 1, "a", None), SimpleNamespace(outcome="persisted_success")):
        with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError) as caught:
            route_persisted_transition_outcome_classification_cycle_continuation_boundary(result, workflow, state, events, phase100_function=lambda *_: value)
        assert caught.value.detail.classification == "outcome_contract"


def test_unrelated_event_bytes_are_not_accepted(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    payload = json.loads(events.read_text())
    payload["step_id"] = "unrelated"
    events.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError):
        route_persisted_transition_outcome_classification_cycle_continuation_boundary(result, workflow, state, events)

# Strict Phase 107 matrices below intentionally mirror the Phase 106/100 focused
# suites.  Keeping these direct prevents a permissive predecessor/dependency from
# accidentally widening this public boundary.
def _assert_rejected(value, workflow, state, events, expected, dependency=lambda *_: pytest.fail("dependency called")):
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError) as caught:
        route_persisted_transition_outcome_classification_cycle_continuation_boundary(
            value, workflow, state, events, phase100_function=dependency
        )
    assert caught.value.detail.classification == expected


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("state_path", "state.json"), ("events_path", "events.jsonl"),
        ("state_bytes_written", True), ("state_bytes_written", 1.0),
        ("state_bytes_written", 0), ("state_bytes_written", -1),
        ("event_bytes_appended", True), ("event_bytes_appended", 1.0),
        ("event_bytes_appended", 0), ("event_bytes_appended", -1),
    ],
)
def test_every_persistence_result_field_has_an_exact_builtin_type_and_value(
    tmp_path: Path, field: str, bad: object
) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    _assert_rejected(replace(result, **{field: bad}), workflow, state, events, "persistence_contract")


def test_exact_workflow_and_step_models_are_required(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    class WorkflowSubclass(WorkflowDefinition):
        pass
    substitute = SimpleNamespace(id="w", name="W", description="D", steps=workflow.steps)
    subclass = WorkflowSubclass.model_validate(workflow.model_dump())
    bad_step = WorkflowDefinition.model_construct(
        id="w", name="W", description="D", steps=[SimpleNamespace(**workflow.steps[0].model_dump())]
    )
    for bad in (substitute, subclass, bad_step):
        _assert_rejected(result, bad, state, events, "workflow_definition")


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("id", 1), ("name", 1), ("description", 1), ("steps", tuple()),
    ],
)
def test_workflow_builtin_field_types_are_revalidated(
    tmp_path: Path, field: str, bad: object
) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    malformed = WorkflowDefinition.model_construct(**(workflow.model_dump() | {field: bad}))
    _assert_rejected(result, malformed, state, events, "workflow_definition")


@pytest.mark.parametrize("field", ["id", "name", "employee", "instructions"])
def test_step_builtin_field_types_are_revalidated(tmp_path: Path, field: str) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    step = workflow.steps[0].model_copy(update={field: 1})
    malformed = workflow.model_copy(update={"steps": [step]})
    _assert_rejected(result, malformed, state, events, "workflow_definition")


@pytest.mark.parametrize("mode", ["missing", "duplicate", "reordered", "malformed", "unrelated"])
def test_terminal_history_strict_matrix_rejects_before_dependency(tmp_path: Path, mode: str) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    line = events.read_bytes()
    if mode == "missing":
        events.write_bytes(b"")
    elif mode == "duplicate":
        events.write_bytes(line + line)
    elif mode == "reordered":
        # A leading unrelated terminal record also proves canonical ordering is checked.
        payload = json.loads(line)
        payload["step_id"] = "other"
        events.write_text(json.dumps(payload, separators=(",", ":")) + "\n" + line.decode())
    elif mode == "malformed":
        events.write_bytes(b"{not-json}\n")
    else:
        payload = json.loads(line)
        payload["workflow_id"] = "other"
        events.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    _assert_rejected(result, workflow, state, events, "terminal_contract")


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("outcome", "other"), ("outcome", 1),
        ("workflow_id", "other"), ("workflow_id", 1),
        ("current_step_id", "other"), ("current_step_id", 1),
        ("current_step_index", 2), ("current_step_index", True),
        ("current_employee_id", "other"), ("current_employee_id", 1),
        ("failure_category", "api_error"), ("failure_category", 1),
    ],
)
def test_every_dependency_outcome_field_type_and_value_is_matched(
    tmp_path: Path, field: str, bad: object
) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    valid = PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", None)
    malformed = replace(valid, **{field: bad})
    _assert_rejected(result, workflow, state, events, "outcome_contract", lambda *_: malformed)


def test_failed_result_requires_exact_persisted_failure_all_fields(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path, "failed")
    expected = PersistedExecutionOutcome("persisted_failure", "w", "one", 1, "a", "api_error")
    assert route_persisted_transition_outcome_classification_cycle_continuation_boundary(
        result, workflow, state, events, phase100_function=lambda *_: expected
    ) is expected
    for malformed in (
        replace(expected, outcome="persisted_success"),
        replace(expected, workflow_id="other"),
        replace(expected, current_step_id="other"),
        replace(expected, current_step_index=True),
        replace(expected, current_employee_id="other"),
        replace(expected, failure_category=None),
    ):
        _assert_rejected(result, workflow, state, events, "outcome_contract", lambda *_, m=malformed: m)


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_target_oserrors_are_classified_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, operation: str
) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    selected = state if target == "state" else events
    original = getattr(_TestPath, operation)
    def raising(self):
        if self is selected:
            raise OSError("unsafe detail")
        return original(self)
    monkeypatch.setattr(_TestPath, operation, raising)
    _assert_rejected(result, workflow, state, events, f"{target[:-1] if target == 'events' else target}_target")


@pytest.mark.parametrize("target", ["state", "events"])
def test_missing_and_nonregular_targets_are_rejected(
    tmp_path: Path, target: str
) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    selected = state if target == "state" else events
    selected.unlink()
    _assert_rejected(result, workflow, state, events, "state_target" if target == "state" else "event_target")
    selected.mkdir()
    _assert_rejected(result, workflow, state, events, "state_target" if target == "state" else "event_target")


def test_same_target_conflict_is_rejected(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    conflict = replace(result, events_path=state, event_bytes_appended=len(state.read_bytes()))
    _assert_rejected(conflict, workflow, state, state, "target_conflict")


@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
def test_unchanged_dependency_failures_preserve_bytes_and_error_contract(
    tmp_path: Path, kind: str
) -> None:
    state, events, result, workflow, before_state, before_events = _setup(tmp_path)
    safe = PersistedOutcomeClassificationDispatchContinuationError("dependency_error")
    def dependency(*_):
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("secret")
        return object()
    with pytest.raises(Exception) as caught:
        route_persisted_transition_outcome_classification_cycle_continuation_boundary(
            result, workflow, state, events, phase100_function=dependency
        )
    assert caught.value is safe if kind == "safe" else isinstance(caught.value, PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError)
    if kind != "safe":
        assert caught.value.detail.classification == ("dependency_error" if kind == "unexpected" else "outcome_contract")
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_rollback_failure_matrix_attempts_both_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    state, events, result, workflow, before_state, before_events = _setup(tmp_path)
    writes = []
    original = _TestPath.write_bytes
    armed = False
    def write(self, data):
        writes.append((self, data))
        if armed and data in (before_state, before_events):
            raise OSError("rollback detail")
        return original(self, data)
    monkeypatch.setattr(_TestPath, "write_bytes", write)
    def dependency(*_):
        nonlocal armed
        if mutation in ("state", "both"):
            original(state, b"changed-state")
        if mutation in ("events", "both"):
            original(events, b"changed-events")
        armed = True
        raise RuntimeError("primary detail")
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleContinuationCompatibilityError) as caught:
        route_persisted_transition_outcome_classification_cycle_continuation_boundary(
            result, workflow, state, events, phase100_function=dependency
        )
    assert caught.value.detail.classification == "dependency_rollback"
    restored_paths = {path for path, data in writes if data in (before_state, before_events)}
    assert restored_paths == {state, events}

class _PersistenceResultSubclass(WorkflowExecutionPersistenceResult):
    pass


@pytest.mark.parametrize("bad", [
    _PersistenceResultSubclass(Path("s"), Path("e"), 1, 1),
    SimpleNamespace(state_path=Path("s"), events_path=Path("e"), state_bytes_written=1, event_bytes_appended=1),
])
def test_persistence_result_subclass_and_attribute_substitute_reject_before_phase100(
    tmp_path: Path, bad: object
) -> None:
    state, events, _, workflow, *_ = _setup(tmp_path)
    calls = 0
    def dependency(*_):
        nonlocal calls
        calls += 1
    _assert_rejected(bad, workflow, state, events, "result_type", dependency)
    assert calls == 0


@pytest.mark.parametrize("bad", [None, 1, object()])
def test_dependency_model_must_be_callable_before_any_call(tmp_path: Path, bad: object) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    _assert_rejected(result, workflow, state, events, "persistence_contract", bad)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("decision", 1), ("decision", "next_step"),
        ("workflow_id", 1), ("workflow_id", "other"),
        ("current_step_id", 1), ("current_step_id", "other"),
        ("current_step_index", True), ("current_step_index", 2),
        ("current_employee_id", 1), ("current_employee_id", "other"),
        ("next_step_id", "two"), ("next_step_index", 2),
        ("next_employee_id", "b"), ("reason", 1), ("reason", "wrong"),
    ],
)
def test_workflow_complete_stop_every_field_type_and_value_is_exact(
    tmp_path: Path, field: str, bad: object
) -> None:
    state, events, _, workflow, *_ = _setup(tmp_path)
    _assert_rejected(replace(_completion(), **{field: bad}), workflow, state, events, "completion_contract")


@pytest.mark.parametrize("field", [
    "outcome", "workflow_id", "current_step_id", "current_step_index",
    "current_employee_id", "failure_category",
])
def test_persisted_failure_stop_each_field_is_exact(tmp_path: Path, field: str) -> None:
    state, events, _, workflow, *_ = _setup(tmp_path, "failed")
    bad_values = {
        "outcome": "persisted_success", "workflow_id": "other", "current_step_id": "other",
        "current_step_index": True, "current_employee_id": "other", "failure_category": None,
    }
    _assert_rejected(replace(_failure(), **{field: bad_values[field]}), workflow, state, events, "failure_contract")
