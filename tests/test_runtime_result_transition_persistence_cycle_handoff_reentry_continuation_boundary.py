"""Phase 120 runtime-result transition persistence cycle handoff reentry continuation tests."""

# ruff: noqa: E501

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError,
    RuntimeResultTransitionPersistenceCycleReentryContinuationError,
    WorkflowProgressionDecision,
    route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class SuccessSubclass(StepRuntimeExecutionSuccess):
    pass


class FailureSubclass(StepRuntimeExecutionFailure):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class PersistenceResultSubclass(WorkflowExecutionPersistenceResult):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "step", "name": "S", "employee": "e", "instructions": "do"}
            ],
        }
    )


def step_one_success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w", "step", 1, "e",
        ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out"),
    )


def success() -> StepRuntimeExecutionSuccess:
    return continuation_success()


def continuation_success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w", "two", 2, "e",
        ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out"),
    )


def step_one_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w", "step", 1, "e",
        ModelInvocationFailure("openai", "api_error", "safe", "q", 500, None, None),
    )


def failure() -> StepRuntimeExecutionFailure:
    return continuation_failure()


def continuation_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w", "two", 2, "e",
        ModelInvocationFailure("openai", "api_error", "safe", "q", 500, None, None),
    )


def setup_one_step(tmp_path: Path, status: str = "running") -> tuple[Path, Path]:
    state = WorkflowExecutionState(
        "w", status, "step", 1, "e",
        ("step",) if status == "succeeded" else (),
        None if status != "failed" else "api_error",
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    if status == "running":
        events_path.write_text("")
    else:
        event = RuntimeStepEvent(
            "step_succeeded" if status == "succeeded" else "step_failed",
            "w", "step", 1, "e", "running", status, "openai",
            None if status == "succeeded" else "api_error",
            "r" if status == "succeeded" else None, "q",
            "out" if status == "succeeded" else None,
            None if status == "succeeded" else "safe",
        )
        events_path.write_text(serialize_runtime_step_event_jsonl(event))
    return state_path, events_path


def setup(tmp_path: Path, status: str = "running") -> tuple[Path, Path]:
    if status == "running":
        return setup_two_step_history(tmp_path, "valid")
    return setup_one_step(tmp_path, status)


def two_step_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "e", "instructions": "two"},
            ],
        }
    )


def predecessor_event(step_id: str, step_index: int) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded", "w", step_id, step_index, "e", "running", "succeeded",
        "openai", None, f"response-{step_id}", f"request-{step_id}", "output", None
    )


def setup_two_step_history(tmp_path: Path, mode: str) -> tuple[Path, Path]:
    state = WorkflowExecutionState("w", "running", "two", 2, "e", ("one",), None)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    first = serialize_runtime_step_event_jsonl(predecessor_event("one", 1))
    second = serialize_runtime_step_event_jsonl(predecessor_event("two", 2))
    events_path.write_text({"valid": first, "missing": "", "duplicate": first + first, "reordered": second + first}[mode])
    return state_path, events_path


def persist_fake(
    result: object, _workflow: object, state: Path, events: Path
) -> WorkflowExecutionPersistenceResult:
    invocation = result.invocation_result  # type: ignore[union-attr]
    ok = type(result) is StepRuntimeExecutionSuccess
    current_state = load_workflow_execution_state(state)
    next_state = WorkflowExecutionState(
        "w",
        "succeeded" if ok else "failed",
        result.step_id,  # type: ignore[union-attr]
        result.step_index,  # type: ignore[union-attr]
        result.employee_id,  # type: ignore[union-attr]
        current_state.completed_step_ids + ((result.step_id,) if ok else ()),  # type: ignore[union-attr]
        None if ok else invocation.category,
    )
    event = RuntimeStepEvent(
        "step_succeeded" if ok else "step_failed",
        "w",
        result.step_id,  # type: ignore[union-attr]
        result.step_index,  # type: ignore[union-attr]
        result.employee_id,  # type: ignore[union-attr]
        "running",
        next_state.status,
        "openai",
        None if ok else invocation.category,
        invocation.response_id if ok else None,
        invocation.request_id,
        invocation.text if ok else None,
        None if ok else invocation.message,
    )
    state_bytes = serialize_workflow_execution_state_json(next_state).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    state.write_bytes(state_bytes)
    events.write_bytes(events.read_bytes() + event_bytes)
    return WorkflowExecutionPersistenceResult(
        state, events, len(state_bytes), len(event_bytes)
    )


def call(
    result: object,
    workflow_value: object,
    state: object,
    events: object,
    dependency: object,
) -> object:
    return (
        route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary(
            result, workflow_value, state, events, phase113_function=dependency
        )
    )  # type: ignore[arg-type]


def test_public_signature_is_canonical_and_keyword_only_dependency() -> None:
    signature = inspect.signature(
        route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary
    )
    assert tuple(signature.parameters) == (
        "result", "workflow", "state_path", "events_path", "phase113_function"
    )
    assert all(
        signature.parameters[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for name in ("result", "workflow", "state_path", "events_path")
    )
    assert all(
        signature.parameters[name].annotation is object
        for name in ("result", "workflow", "state_path", "events_path")
    )
    dependency = signature.parameters["phase113_function"]
    assert dependency.kind is inspect.Parameter.KEYWORD_ONLY
    from ai_office.engine.runtime_result_transition_persistence_cycle_reentry_continuation_boundary import (
        route_runtime_result_transition_persistence_cycle_reentry_continuation_boundary,
    )
    assert dependency.default is route_runtime_result_transition_persistence_cycle_reentry_continuation_boundary
def test_production_module_references_only_the_public_phase113_dependency() -> None:
    import ai_office.engine.runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary as module
    source = inspect.getsource(module)
    assert (
        "route_runtime_result_transition_persistence_cycle_reentry_continuation_boundary"
        in source
    )
    assert "phase106" not in source.lower()
    assert "runtime_result_transition_persistence_cycle_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


@pytest.mark.parametrize("result", [step_one_success(), step_one_failure()])
def test_step_one_runtime_routes_reject_before_dependency(
    tmp_path: Path, result: object
) -> None:
    state, events = setup_one_step(tmp_path)
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(result, two_step_workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "runtime_contract"
@pytest.mark.parametrize("result", [continuation_success(), continuation_failure()])
def test_valid_routes_delegate_once_and_return_exact_dependency_object(
    tmp_path: Path, result: object
) -> None:
    state, events = setup_two_step_history(tmp_path, "valid")
    supplied_workflow = two_step_workflow()
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def phase64(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert all(
            actual is wanted
            for actual, wanted in zip(
                args, (result, supplied_workflow, state, events), strict=True
            )
        )
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(result, supplied_workflow, state, events, phase64) is expected
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_return_identity_without_dependency(
    tmp_path: Path, kind: str
) -> None:
    state, events = setup(tmp_path, "succeeded" if kind == "complete" else "failed")
    result: object = (
        WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            "step",
            1,
            "e",
            None,
            None,
            None,
            "last_step_succeeded",
        )
        if kind == "complete"
        else PersistedExecutionOutcome(
            "persisted_failure", "w", "step", 1, "e", "api_error"
        )
    )
    before = state.read_bytes(), events.read_bytes()
    assert (
        call(result, workflow(), state, events, lambda *_: pytest.fail("called"))
        is result
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "result,classification",
    [
        (replace(WorkflowProgressionDecision("workflow_complete", "w", "step", 1, "e", None, None, None, "last_step_succeeded"), decision="prepare_next_step"), "completion_contract"),
        (replace(WorkflowProgressionDecision("workflow_complete", "w", "step", 1, "e", None, None, None, "last_step_succeeded"), reason="wrong"), "completion_contract"),
        (replace(PersistedExecutionOutcome("persisted_failure", "w", "step", 1, "e", "api_error"), failure_category="unknown"), "failure_contract"),
        (replace(PersistedExecutionOutcome("persisted_failure", "w", "step", 1, "e", "api_error"), current_step_index=True), "failure_contract"),
    ],
)
def test_malformed_stop_and_unsupported_progression_are_zero_call(
    tmp_path: Path, result: object, classification: str
) -> None:
    state, events = setup(tmp_path, "succeeded" if type(result) is WorkflowProgressionDecision else "failed")
    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError) as caught:
        call(result, workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == classification


@pytest.mark.parametrize(
    "value",
    [
        object(),
        SimpleNamespace(workflow_id="w"),
        PersistenceResultSubclass(Path("x"), Path("y"), 1, 1),
    ],
)
def test_invalid_result_types_reject_before_dependency(
    tmp_path: Path, value: object
) -> None:
    state, events = setup(tmp_path)
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(value, two_step_workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "result_type"


@pytest.mark.parametrize(
    "value",
    [SuccessSubclass(**success().__dict__), FailureSubclass(**failure().__dict__)],
)
def test_runtime_subclasses_reject_before_dependency(
    tmp_path: Path, value: object
) -> None:
    state, events = setup(tmp_path)
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(value, two_step_workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "result_type"


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "wrong"),
        ("step_id", "wrong"),
        ("step_index", True),
        ("employee_id", "wrong"),
    ],
)
def test_runtime_result_fields_are_revalidated(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = setup(tmp_path)
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(
            replace(success(), **{field: value}),
            two_step_workflow(),
            state,
            events,
            lambda *_: pytest.fail("called"),
        )
    assert caught.value.detail.classification == "runtime_contract"


def test_running_state_and_predecessor_history_are_revalidated(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    state.write_text(
        serialize_workflow_execution_state_json(
            WorkflowExecutionState("w", "succeeded", "step", 1, "e", ("step",), None)
        )
    )
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(success(), two_step_workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "runtime_contract"


@pytest.mark.parametrize("mode", ["missing", "duplicate", "reordered"])
def test_missing_duplicate_and_reordered_predecessor_history_is_rejected(
    tmp_path: Path, mode: str
) -> None:
    state, events = setup_two_step_history(tmp_path, mode)
    result = StepRuntimeExecutionSuccess(
        "w", "two", 2, "e", ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out")
    )
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(result, two_step_workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "runtime_contract"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value, state, events: replace(value, state_path=Path("wrong")),
        lambda value, state, events: replace(value, events_path=Path("wrong")),
        lambda value, state, events: replace(value, state_bytes_written=True),
        lambda value, state, events: replace(value, event_bytes_appended=True),
    ],
)
def test_persistence_result_paths_and_byte_counts_are_exact(
    tmp_path: Path, mutator: object
) -> None:
    state, events = setup(tmp_path)

    def dependency(*args: object) -> object:
        value = persist_fake(*args)  # type: ignore[arg-type]
        return mutator(value, state, events)  # type: ignore[operator]

    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("field,delta", [("state_bytes_written", 1), ("event_bytes_appended", 1)])
def test_positive_but_mismatched_byte_count_is_rejected(
    tmp_path: Path, field: str, delta: int
) -> None:
    state, events = setup(tmp_path)

    def dependency(*args: object) -> object:
        value = persist_fake(*args)  # type: ignore[arg-type]
        return replace(value, **{field: getattr(value, field) + delta})

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("field", ["state_path", "events_path"])
def test_equal_but_nonidentical_persistence_path_is_rejected(
    tmp_path: Path, field: str
) -> None:
    state, events = setup(tmp_path)

    def dependency(*args: object) -> object:
        value = persist_fake(*args)  # type: ignore[arg-type]
        replacement = Path(str(state if field == "state_path" else events))
        return replace(value, **{field: replacement})

    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize(
    "result,field,value",
    [
        (success(), "provider", "other"),
        (success(), "response_id", None),
        (success(), "request_id", True),
        (success(), "status", None),
        (success(), "text_parts", ["out"]),
        (success(), "text", "wrong"),
        (failure(), "provider", "other"),
        (failure(), "category", "unknown"),
        (failure(), "message", None),
        (failure(), "request_id", True),
        (failure(), "status_code", True),
        (failure(), "provider_error_type", True),
        (failure(), "provider_error_code", True),
    ],
)
def test_runtime_payload_fields_and_exact_types_are_revalidated(
    tmp_path: Path, result: object, field: str, value: object
) -> None:
    state, events = setup(tmp_path)
    invalid = replace(
        result,
        invocation_result=replace(result.invocation_result, **{field: value}),  # type: ignore[union-attr]
    )
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(invalid, two_step_workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "runtime_contract"


def test_direct_persisted_success_is_rejected(tmp_path: Path) -> None:
    state, events = setup(tmp_path, "succeeded")
    result = PersistedExecutionOutcome("persisted_success", "w", "step", 1, "e", None)
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(result, workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "failure_contract"


def test_extra_history_event_is_rejected_and_restored(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*args: object) -> object:
        value = persist_fake(*args)  # type: ignore[arg-type]
        events.write_bytes(events.read_bytes() + events.read_bytes())
        return value

    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("suffix", [b"x", b"\n"])
def test_extra_appended_event_bytes_are_rejected_and_restored(
    tmp_path: Path, suffix: bytes
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*args: object) -> object:
        value = persist_fake(*args)  # type: ignore[arg-type]
        events.write_bytes(events.read_bytes() + suffix)
        return value

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("variant", ["missing", "reordered", "unrelated"])
def test_terminal_event_shape_is_required_and_dependency_is_not_called(
    tmp_path: Path, variant: str
) -> None:
    state, events = setup(tmp_path, "succeeded")
    terminal = RuntimeStepEvent(
        "step_succeeded", "w", "step", 1, "e", "running", "succeeded",
        "openai", None, "r", "q", "out", None
    )
    if variant == "missing":
        events.write_bytes(b"")
    elif variant == "reordered":
        events.write_bytes(serialize_runtime_step_event_jsonl(terminal).encode() + events.read_bytes())
    else:
        events.write_bytes(serialize_runtime_step_event_jsonl(replace(terminal, step_id="other")).encode())
    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError) as caught:
        call(WorkflowProgressionDecision("workflow_complete", "w", "step", 1, "e", None, None, None, "last_step_succeeded"), workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("field,value", [("workflow_id", "other"), ("current_step_id", "other"), ("current_employee_id", "other"), ("current_step_index", 2)])
def test_terminal_result_mismatch_is_zero_call(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = setup(tmp_path, "succeeded")
    result = replace(
        WorkflowProgressionDecision("workflow_complete", "w", "step", 1, "e", None, None, None, "last_step_succeeded"),
        **{field: value},
    )
    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError) as caught:
        call(result, workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "completion_contract"


@pytest.mark.parametrize("name", ["workflow", "state_path", "events_path"])
def test_top_level_and_target_contracts_reject_before_dependency(
    tmp_path: Path, name: str
) -> None:
    state, events = setup(tmp_path)
    if name == "workflow":
        with pytest.raises(
            RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
        ) as caught:
            call(
                success(),
                WorkflowSubclass.model_validate(workflow().model_dump()),
                state,
                events,
                lambda *_: pytest.fail("called"),
            )
        assert caught.value.detail.classification == "workflow_definition"
        return
    path = state if name == "state_path" else events
    path.unlink()
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(success(), two_step_workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == (
        "state_target" if name == "state_path" else "event_target"
    )


@pytest.mark.parametrize("target", ["state", "events"])
def test_directory_target_is_rejected_before_dependency(
    tmp_path: Path, target: str
) -> None:
    state, events = setup(tmp_path)
    path = state if target == "state" else events
    path.unlink()
    path.mkdir()
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(success(), two_step_workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == (
        "state_target" if target == "state" else "event_target"
    )


@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
@pytest.mark.parametrize("target", ["state", "events"])
def test_target_oserror_is_classified_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, target: str
) -> None:
    state, events = setup(tmp_path)
    original = getattr(Path, operation)
    target_path = state if target == "state" else events

    def fail(path: Path, *args: object, **kwargs: object) -> object:
        if path == target_path:
            raise OSError("synthetic")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(success(), two_step_workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == (
        "state_target" if target == "state" else "event_target"
    )


@pytest.mark.parametrize(
    "dependency_result",
    [object(), WorkflowExecutionPersistenceResult(Path("wrong"), Path("wrong"), 1, 1)],
)
def test_malformed_persistence_return_is_rejected_and_restored(
    tmp_path: Path, dependency_result: object
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*_: object) -> object:
        state.write_bytes(b"mutated")
        events.write_bytes(b"mutated")
        return dependency_result

    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_dependency_no_write_is_rejected_without_mutating_targets(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return WorkflowExecutionPersistenceResult(state, events, 1, 1)

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


def test_invalid_transition_is_compensated(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*_: object) -> object:
        state.write_bytes(
            serialize_workflow_execution_state_json(
                WorkflowExecutionState("w", "failed", "step", 1, "e", (), "api_error")
            ).encode()
        )
        return WorkflowExecutionPersistenceResult(
            state, events, len(state.read_bytes()), 1
        )

    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_safe_error_identity_and_unexpected_error_sanitization(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    safe = RuntimeResultTransitionPersistenceCycleReentryContinuationError("private")
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleReentryContinuationError
    ) as caught:
        call(
            success(), two_step_workflow(), state, events, lambda *_: (_ for _ in ()).throw(safe)
        )
    assert caught.value is safe
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(
            success(),
            two_step_workflow(),
            state,
            events,
            lambda *_: (_ for _ in ()).throw(RuntimeError("private")),
        )
    assert (
        caught.value.detail.classification == "dependency_error"
        and "private" not in str(caught.value)
    )


def test_safe_error_identity_is_preserved_after_successful_compensation(
    tmp_path: Path,
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = RuntimeResultTransitionPersistenceCycleReentryContinuationError(
        "private"
    )

    def dependency(*_: object) -> object:
        state.write_bytes(b"mutated")
        events.write_bytes(b"mutated")
        raise safe

    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleReentryContinuationError
    ) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value is safe
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_safe_error_compensates_each_mutation_shape_and_calls_once(
    tmp_path: Path, target: str
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = RuntimeResultTransitionPersistenceCycleReentryContinuationError("private")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            state.write_bytes(b"state mutation")
        if target in {"events", "both"}:
            events.write_bytes(b"event mutation")
        raise safe

    with pytest.raises(RuntimeResultTransitionPersistenceCycleReentryContinuationError) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value is safe
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("field,value", [("state_path", object()), ("events_path", object()), ("state_bytes_written", 1.5), ("event_bytes_appended", "1")])
def test_persistence_result_fields_require_exact_builtin_types(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = setup(tmp_path)

    def dependency(*args: object) -> object:
        result = persist_fake(*args)  # type: ignore[arg-type]
        return replace(result, **{field: value})

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_mutations_are_compensated_and_dependency_is_called_once(
    tmp_path: Path, target: str
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            state.write_bytes(b"mutated")
        if target in {"events", "both"}:
            events.write_bytes(b"mutated")
        return object()

    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ):
        call(success(), two_step_workflow(), state, events, dependency)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failures_attempt_both_targets_and_do_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    state, events = setup(tmp_path)
    original_write = Path.write_bytes
    attempts: list[Path] = []
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        original_write(state, b"mutated")
        original_write(events, b"mutated")
        return object()

    def restore(path: Path, data: bytes) -> int:
        attempts.append(path)
        if (
            failed == "both"
            or (failed == "state" and path == state)
            or (failed == "events" and path == events)
        ):
            raise OSError("rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", restore)
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(success(), two_step_workflow(), state, events, dependency)
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempts.count(state) == 1 and attempts.count(events) == 1 and calls == 1


def multi_step_workflow(count: int) -> WorkflowDefinition:
    step_ids = tuple(f"s{index}" for index in range(1, count + 1))
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": step_id,
                    "name": step_id.capitalize(),
                    "employee": "e",
                    "instructions": step_id,
                }
                for step_id in step_ids
            ],
        }
    )


def running_success(step_id: str, step_index: int) -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        step_id,
        step_index,
        "e",
        ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out"),
    )


def running_failure(step_id: str, step_index: int) -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w",
        step_id,
        step_index,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", "q", 500, None, None),
    )


def predecessor_event_custom(
    step_id: str, step_index: int, output_text: object
) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        step_index,
        "e",
        "running",
        "succeeded",
        "openai",
        None,
        f"response-{step_id}",
        f"request-{step_id}",
        output_text,  # type: ignore[arg-type]
        None,
    )


def setup_multi_history(
    tmp_path: Path, outputs: tuple[object, ...]
) -> tuple[Path, Path]:
    state = WorkflowExecutionState(
        "w",
        "running",
        f"s{len(outputs) + 1}",
        len(outputs) + 1,
        "e",
        tuple(f"s{index}" for index in range(1, len(outputs) + 1)),
        None,
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(
                predecessor_event_custom(f"s{index}", index, output)
            )
            for index, output in enumerate(outputs, 1)
        )
    )
    return state_path, events_path


def persist_fake_running(
    result: object, _workflow: object, state: Path, events: Path
) -> WorkflowExecutionPersistenceResult:
    invocation = result.invocation_result  # type: ignore[union-attr]
    ok = type(result) is StepRuntimeExecutionSuccess
    current_state = load_workflow_execution_state(state)
    next_state = WorkflowExecutionState(
        "w",
        "succeeded" if ok else "failed",
        result.step_id,  # type: ignore[union-attr]
        result.step_index,  # type: ignore[union-attr]
        result.employee_id,  # type: ignore[union-attr]
        current_state.completed_step_ids + ((result.step_id,) if ok else ()),  # type: ignore[union-attr]
        None if ok else invocation.category,
    )
    event = RuntimeStepEvent(
        "step_succeeded" if ok else "step_failed",
        "w",
        result.step_id,  # type: ignore[union-attr]
        result.step_index,  # type: ignore[union-attr]
        result.employee_id,  # type: ignore[union-attr]
        "running",
        next_state.status,
        "openai",
        None if ok else invocation.category,
        invocation.response_id if ok else None,
        invocation.request_id,
        invocation.text if ok else None,
        None if ok else invocation.message,
    )
    state_bytes = serialize_workflow_execution_state_json(next_state).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    state.write_bytes(state_bytes)
    events.write_bytes(events.read_bytes() + event_bytes)
    return WorkflowExecutionPersistenceResult(
        state, events, len(state_bytes), len(event_bytes)
    )


def test_earlier_predecessor_empty_output_delegates_once_to_phase113(
    tmp_path: Path,
) -> None:
    state, events = setup_multi_history(tmp_path, ("", "output"))
    result = running_success("s3", 3)
    supplied_workflow = multi_step_workflow(3)
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def phase113(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert all(
            actual is wanted
            for actual, wanted in zip(
                args, (result, supplied_workflow, state, events), strict=True
            )
        )
        expected = persist_fake_running(*args)  # type: ignore[arg-type]
        return expected

    assert call(result, supplied_workflow, state, events, phase113) is expected
    assert len(calls) == 1


def test_immediate_predecessor_empty_output_delegates_once_to_phase113(
    tmp_path: Path,
) -> None:
    state, events = setup_multi_history(tmp_path, ("output", ""))
    result = running_failure("s3", 3)
    supplied_workflow = multi_step_workflow(3)
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def phase113(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert all(
            actual is wanted
            for actual, wanted in zip(
                args, (result, supplied_workflow, state, events), strict=True
            )
        )
        expected = persist_fake_running(*args)  # type: ignore[arg-type]
        return expected

    assert call(result, supplied_workflow, state, events, phase113) is expected
    assert len(calls) == 1


def test_multiple_earlier_and_immediate_empty_outputs_delegate_once_to_phase113(
    tmp_path: Path,
) -> None:
    state, events = setup_multi_history(tmp_path, ("", "", ""))
    result = running_success("s4", 4)
    supplied_workflow = multi_step_workflow(4)
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def phase113(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert all(
            actual is wanted
            for actual, wanted in zip(
                args, (result, supplied_workflow, state, events), strict=True
            )
        )
        expected = persist_fake_running(*args)  # type: ignore[arg-type]
        return expected

    assert call(result, supplied_workflow, state, events, phase113) is expected
    assert len(calls) == 1


@pytest.mark.parametrize("value", [None, 123, True])
def test_predecessor_output_text_non_string_is_rejected_before_phase113(
    tmp_path: Path, value: object
) -> None:
    state, events = setup_multi_history(tmp_path, ("output", value))
    before = state.read_bytes(), events.read_bytes()
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        call(
            running_success("s3", 3),
            multi_step_workflow(3),
            state,
            events,
            lambda *_: pytest.fail("called"),
        )
    assert caught.value.detail.classification == "runtime_contract"
    assert (state.read_bytes(), events.read_bytes()) == before
