"""Focused Phase 127 runtime-result persistence handoff-chain tests."""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError,
    RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationError,
    WorkflowProgressionDecision,
    route_runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary,
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


class SuccessChild(StepRuntimeExecutionSuccess):
    pass


class FailureChild(StepRuntimeExecutionFailure):
    pass


class SuccessInvocationChild(ModelInvocationSuccess):
    pass


class FailureInvocationChild(ModelInvocationFailure):
    pass


class WorkflowChild(WorkflowDefinition):
    pass


class PersistenceChild(WorkflowExecutionPersistenceResult):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "e", "instructions": "two"},
                {"id": "three", "name": "Three", "employee": "e", "instructions": "three"},
                {"id": "four", "name": "Four", "employee": "e", "instructions": "four"},
            ],
        }
    )


def success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w", "three", 3, "e",
        ModelInvocationSuccess("openai", "response-three", "request-three", "completed", ("output",), "output"),
    )


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w", "three", 3, "e",
        ModelInvocationFailure("openai", "api_error", "safe failure", "request-three", 500, None, None),
    )


def predecessor_event(step_id: str, step_index: int) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded", "w", step_id, step_index, "e", "running", "succeeded",
        "openai", None, f"response-{step_id}", f"request-{step_id}", f"output-{step_id}", None,
    )


def success_terminal_event(**changes: object) -> RuntimeStepEvent:
    return replace(
        RuntimeStepEvent(
            "step_succeeded", "w", "three", 3, "e", "running", "succeeded",
            "openai", None, "response-three", "request-three", "output", None,
        ),
        **changes,
    )


def setup(tmp_path: Path, status: str = "running") -> tuple[Path, Path]:
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    if status == "running":
        state = WorkflowExecutionState("w", "running", "three", 3, "e", ("one", "two"), None)
        events = "".join(
            (
                serialize_runtime_step_event_jsonl(predecessor_event("one", 1)),
                serialize_runtime_step_event_jsonl(predecessor_event("two", 2)),
            )
        )
    elif status == "succeeded":
        state = WorkflowExecutionState("w", "succeeded", "four", 4, "e", ("one", "two", "three", "four"), None)
        events = "".join(
            (
                serialize_runtime_step_event_jsonl(predecessor_event("one", 1)),
                serialize_runtime_step_event_jsonl(predecessor_event("two", 2)),
                serialize_runtime_step_event_jsonl(predecessor_event("three", 3)),
                serialize_runtime_step_event_jsonl(predecessor_event("four", 4)),
            )
        )
    else:
        state = WorkflowExecutionState("w", "failed", "three", 3, "e", ("one", "two"), "api_error")
        failed = RuntimeStepEvent(
            "step_failed", "w", "three", 3, "e", "running", "failed", "openai",
            "api_error", None, "request-three", None, "safe failure",
        )
        events = "".join(
            (
                serialize_runtime_step_event_jsonl(predecessor_event("one", 1)),
                serialize_runtime_step_event_jsonl(predecessor_event("two", 2)),
                serialize_runtime_step_event_jsonl(failed),
            )
        )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text(events, encoding="utf-8")
    return state_path, events_path


def setup_index(tmp_path: Path, index: int) -> tuple[Path, Path, object]:
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    step_id = ("one", "two")[index - 1]
    completed = () if index == 1 else ("one",)
    state = WorkflowExecutionState("w", "running", step_id, index, "e", completed, None)
    events = "" if index == 1 else serialize_runtime_step_event_jsonl(predecessor_event("one", 1))
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text(events, encoding="utf-8")
    result = (
        StepRuntimeExecutionSuccess("w", step_id, index, "e", ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out"))
        if index == 1
        else StepRuntimeExecutionSuccess("w", step_id, index, "e", ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out"))
    )
    return state_path, events_path, result


def persist_fake(
    result: object, _workflow: object, state_path: Path, events_path: Path
) -> WorkflowExecutionPersistenceResult:
    invocation = result.invocation_result  # type: ignore[union-attr]
    current = load_workflow_execution_state(state_path)
    successful = type(result) is StepRuntimeExecutionSuccess
    next_state = WorkflowExecutionState(
        "w",
        "succeeded" if successful else "failed",
        result.step_id,  # type: ignore[union-attr]
        result.step_index,  # type: ignore[union-attr]
        result.employee_id,  # type: ignore[union-attr]
        current.completed_step_ids + ((result.step_id,) if successful else ()),  # type: ignore[union-attr]
        None if successful else invocation.category,
    )
    event = RuntimeStepEvent(
        "step_succeeded" if successful else "step_failed",
        result.workflow_id,  # type: ignore[union-attr]
        result.step_id,  # type: ignore[union-attr]
        result.step_index,  # type: ignore[union-attr]
        result.employee_id,  # type: ignore[union-attr]
        "running",
        next_state.status,
        invocation.provider,
        None if successful else invocation.category,
        invocation.response_id if successful else None,
        invocation.request_id,
        invocation.text if successful else None,
        None if successful else invocation.message,
    )
    state_bytes = serialize_workflow_execution_state_json(next_state).encode("utf-8")
    event_bytes = serialize_runtime_step_event_jsonl(event).encode("utf-8")
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(events_path.read_bytes() + event_bytes)
    return WorkflowExecutionPersistenceResult(state_path, events_path, len(state_bytes), len(event_bytes))


def call(result: object, workflow_value: object, state: object, events: object, dependency: object) -> object:
    return route_runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary(
        result, workflow_value, state, events, phase120_function=dependency  # type: ignore[arg-type]
    )


def test_public_signature_is_canonical_and_keyword_only_dependency() -> None:
    parameters = list(inspect.signature(route_runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary).parameters.values())
    assert [item.name for item in parameters[:4]] == ["result", "workflow", "state_path", "events_path"]
    assert all(item.annotation is object for item in parameters[:4])
    assert all(item.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for item in parameters[:4])
    dependency = parameters[4]
    assert dependency.kind is inspect.Parameter.KEYWORD_ONLY
    assert dependency.default is route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary


def test_source_audit_uses_only_public_phase120_dependency() -> None:
    source = Path("src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary.py").read_text(encoding="utf-8")
    assert "route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary" in source
    assert "phase113" not in source.lower()
    assert "route_runtime_result_transition_persistence_cycle_reentry_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


@pytest.mark.parametrize("result", [success(), failure()])
def test_valid_runtime_routes_delegate_four_identity_arguments_once_and_return_exact_object(tmp_path: Path, result: object) -> None:
    state, events = setup(tmp_path)
    supplied_workflow = workflow()
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert all(actual is wanted for actual, wanted in zip(args, (result, supplied_workflow, state, events), strict=True))
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(result, supplied_workflow, state, events, dependency) is expected
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_return_supplied_identity_unchanged_with_zero_calls(tmp_path: Path, kind: str) -> None:
    state, events = setup(tmp_path, "succeeded" if kind == "complete" else "failed")
    result: object = (
        WorkflowProgressionDecision("workflow_complete", "w", "four", 4, "e", None, None, None, "last_step_succeeded")
        if kind == "complete"
        else PersistedExecutionOutcome("persisted_failure", "w", "three", 3, "e", "api_error")
    )
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        pytest.fail("Phase 120 must not be called on a stop route")

    assert call(result, workflow(), state, events, dependency) is result
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("value", [object(), SimpleNamespace(workflow_id="w"), WorkflowExecutionPersistenceResult(Path("x"), Path("y"), 1, 1)])
def test_unsupported_result_types_are_rejected_before_phase120(tmp_path: Path, value: object) -> None:
    state, events = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(value, workflow(), state, events, dependency)
    assert type(caught.value) is RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError
    assert caught.value.detail.classification == "result_type" and calls == 0


@pytest.mark.parametrize("value", [SuccessChild("w", "three", 3, "e", success().invocation_result), FailureChild("w", "three", 3, "e", failure().invocation_result)])
def test_runtime_subclasses_are_rejected_before_phase120(tmp_path: Path, value: object) -> None:
    state, events = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(value, workflow(), state, events, dependency)
    assert caught.value.detail.classification == "result_type" and calls == 0


def test_workflow_subclass_is_rejected_before_phase120(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), WorkflowChild.model_validate(workflow().model_dump()), state, events, dependency)
    assert caught.value.detail.classification == "workflow_definition" and calls == 0


@pytest.mark.parametrize("index", [1, 2])
def test_step_indices_one_and_two_are_rejected_before_phase120(tmp_path: Path, index: int) -> None:
    state, events, result = setup_index(tmp_path, index)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(result, workflow(), state, events, dependency)
    assert caught.value.detail.classification == "runtime_contract" and calls == 0


@pytest.mark.parametrize("field,value", [("workflow_id", "other"), ("step_id", "other"), ("step_index", True), ("employee_id", "other")])
def test_runtime_identity_fields_are_revalidated_before_phase120(tmp_path: Path, field: str, value: object) -> None:
    state, events = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(replace(success(), **{field: value}), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "runtime_contract" and calls == 0


@pytest.mark.parametrize(
    "result,field,value",
    [
        (success(), "provider", "other"), (success(), "response_id", None), (success(), "request_id", True),
        (success(), "status", None), (success(), "text_parts", ["output"]), (success(), "text", "wrong"),
        (failure(), "provider", "other"), (failure(), "category", "unknown"), (failure(), "message", None),
        (failure(), "request_id", True), (failure(), "status_code", True), (failure(), "provider_error_type", True),
        (failure(), "provider_error_code", True),
    ],
)
def test_nested_invocation_result_contract_is_revalidated(tmp_path: Path, result: object, field: str, value: object) -> None:
    state, events = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    invalid = replace(result, invocation_result=replace(result.invocation_result, **{field: value}))  # type: ignore[union-attr]
    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(invalid, workflow(), state, events, dependency)
    assert caught.value.detail.classification == "runtime_contract" and calls == 0


@pytest.mark.parametrize(
    "result,nested",
    [
        (success(), SuccessInvocationChild("openai", "response-three", "request-three", "completed", ("output",), "output")),
        (failure(), FailureInvocationChild("openai", "api_error", "safe failure", "request-three", 500, None, None)),
        (success(), SimpleNamespace(provider="openai", response_id="response-three", request_id="request-three", status="completed", text_parts=("output",), text="output")),
        (failure(), SimpleNamespace(provider="openai", category="api_error", message="safe failure", request_id="request-three", status_code=500, provider_error_type=None, provider_error_code=None)),
    ],
)
def test_nested_invocation_result_requires_exact_model_identity(
    tmp_path: Path, result: object, nested: object
) -> None:
    state, events = setup(tmp_path)
    calls = 0
    invalid = replace(result, invocation_result=nested)  # type: ignore[union-attr]

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert type(invalid) is type(result)
    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(invalid, workflow(), state, events, dependency)
    assert caught.value.detail.classification == "runtime_contract" and calls == 0


def test_persisted_running_state_mismatch_is_rejected_before_phase120(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    state.write_text(serialize_workflow_execution_state_json(WorkflowExecutionState("w", "running", "four", 4, "e", ("one", "two", "three"), None)), encoding="utf-8")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "runtime_contract" and calls == 0


@pytest.mark.parametrize("mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"])
def test_predecessor_history_matrix_is_rejected_before_phase120(tmp_path: Path, mode: str) -> None:
    state, events = setup(tmp_path)
    first = serialize_runtime_step_event_jsonl(predecessor_event("one", 1))
    second = serialize_runtime_step_event_jsonl(predecessor_event("two", 2))
    unrelated = serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "unrelated", 99, "e", "running", "succeeded", "openai", None, "r", "q", "out", None))
    events.write_text({"duplicate": first + first, "missing": "", "reordered": second + first, "unrelated": unrelated + second, "malformed": "not-json\n", "extra": first + second + serialize_runtime_step_event_jsonl(predecessor_event("three", 3))}[mode], encoding="utf-8")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "runtime_contract" and calls == 0


def test_earlier_predecessor_exact_empty_output_delegates_once(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    events.write_text(
        serialize_runtime_step_event_jsonl(
            replace(predecessor_event("one", 1), output_text="")
        )
        + serialize_runtime_step_event_jsonl(predecessor_event("two", 2)),
        encoding="utf-8",
    )
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(success(), workflow(), state, events, dependency) is expected
    assert len(calls) == 1


def test_immediate_predecessor_exact_empty_output_delegates_once(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    events.write_text(
        serialize_runtime_step_event_jsonl(predecessor_event("one", 1))
        + serialize_runtime_step_event_jsonl(
            replace(predecessor_event("two", 2), output_text="")
        ),
        encoding="utf-8",
    )
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(success(), workflow(), state, events, dependency) is expected
    assert len(calls) == 1


def test_combined_earlier_and_immediate_empty_outputs_are_accepted(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    events.write_text(
        serialize_runtime_step_event_jsonl(
            replace(predecessor_event("one", 1), output_text="")
        )
        + serialize_runtime_step_event_jsonl(
            replace(predecessor_event("two", 2), output_text="")
        ),
        encoding="utf-8",
    )
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(success(), workflow(), state, events, dependency) is expected
    assert len(calls) == 1


@pytest.mark.parametrize("output_text", [None, 123, True])
def test_predecessor_non_string_output_text_is_rejected_before_phase120(
    tmp_path: Path, output_text: object
) -> None:
    state, events = setup(tmp_path)
    events.write_text(
        serialize_runtime_step_event_jsonl(predecessor_event("one", 1))
        + serialize_runtime_step_event_jsonl(
            replace(predecessor_event("two", 2), output_text=output_text)  # type: ignore[arg-type]
        ),
        encoding="utf-8",
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "runtime_contract" and calls == 0


@pytest.mark.parametrize("field,value", [("state_path", object()), ("events_path", object()), ("state_bytes_written", 1.5), ("event_bytes_appended", "1")])
def test_persistence_result_fields_require_exact_types_and_compensate(tmp_path: Path, field: str, value: object) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*args: object) -> object:
        return replace(persist_fake(*args), **{field: value})  # type: ignore[arg-type]

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("field", ["state_path", "events_path"])
def test_equal_but_nonidentical_persistence_targets_are_rejected_and_restored(tmp_path: Path, field: str) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*args: object) -> object:
        value = persist_fake(*args)
        return replace(value, **{field: Path(str(state if field == "state_path" else events))})

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("field", ["state_bytes_written", "event_bytes_appended"])
def test_positive_but_mismatched_persistence_byte_counts_are_rejected_and_restored(tmp_path: Path, field: str) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*args: object) -> object:
        value = persist_fake(*args)
        return replace(value, **{field: getattr(value, field) + 1})

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("state_bytes_written", 0),
        ("state_bytes_written", -1),
        ("state_bytes_written", True),
        ("event_bytes_appended", 0),
        ("event_bytes_appended", -1),
        ("event_bytes_appended", True),
    ],
)
def test_persistence_byte_counts_require_positive_exact_int(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        persisted = persist_fake(*args)
        assert type(persisted) is WorkflowExecutionPersistenceResult
        assert persisted.state_path is state and persisted.events_path is events
        return replace(persisted, **{field: value})

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("dependency_result", [object(), SimpleNamespace(state_path=Path("x"), events_path=Path("y"), state_bytes_written=1, event_bytes_appended=1)])
def test_malformed_persistence_returns_are_rejected_and_restored(tmp_path: Path, dependency_result: object) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*_: object) -> object:
        state.write_bytes(b"mutated")
        events.write_bytes(b"mutated")
        return dependency_result

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_persistence_result_subclass_is_rejected_and_restored(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*args: object) -> object:
        value = persist_fake(*args)
        return PersistenceChild(value.state_path, value.events_path, value.state_bytes_written, value.event_bytes_appended)

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_no_write_persistence_result_is_rejected_without_target_changes(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return WorkflowExecutionPersistenceResult(state, events, 1, 1)

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_extra_appended_event_bytes_are_rejected_and_restored(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*args: object) -> object:
        value = persist_fake(*args)
        events.write_bytes(events.read_bytes() + b"extra")
        return value

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_persistence_return_with_target_mutation_is_rejected_and_restored(
    tmp_path: Path, mutation: str
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        value = persist_fake(*args)
        if mutation in ("state", "both"):
            state.write_bytes(b"state-mutated")
        if mutation in ("events", "both"):
            events.write_bytes(b"events-mutated")
        return value

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_invalid_persisted_terminal_state_is_rejected_and_restored(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        persist_fake(*args)
        event_bytes = events.read_bytes()[len(before[1]):]
        invalid = WorkflowExecutionState("w", "running", "three", 3, "e", ("one", "two"), None)
        invalid_bytes = serialize_workflow_execution_state_json(invalid).encode("utf-8")
        state.write_bytes(invalid_bytes)
        returned = WorkflowExecutionPersistenceResult(state, events, len(invalid_bytes), len(event_bytes))
        assert type(returned) is WorkflowExecutionPersistenceResult
        assert returned.state_path is state and returned.events_path is events
        assert returned.state_bytes_written == len(state.read_bytes())
        assert returned.event_bytes_appended == len(event_bytes)
        return returned

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "other"),
        ("step_id", "other"),
        ("step_index", 4),
        ("employee_id", "other"),
        ("provider", "other"),
        ("request_id", "other-request"),
    ],
)
def test_invalid_persisted_terminal_event_runtime_linkage_is_rejected_and_restored(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        persist_fake(*args)
        appended = serialize_runtime_step_event_jsonl(
            success_terminal_event(**{field: value})
        ).encode("utf-8")
        events.write_bytes(before[1] + appended)
        returned = WorkflowExecutionPersistenceResult(
            state, events, len(state.read_bytes()), len(appended)
        )
        assert type(returned) is WorkflowExecutionPersistenceResult
        assert returned.state_path is state and returned.events_path is events
        assert returned.state_bytes_written == len(state.read_bytes())
        assert returned.event_bytes_appended == len(appended)
        return returned

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_invalid_persisted_terminal_event_kind_and_success_failure_linkage_is_rejected_and_restored(
    tmp_path: Path,
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        persist_fake(*args)
        appended = serialize_runtime_step_event_jsonl(
            replace(
                success_terminal_event(),
                event_type="step_failed",
                next_status="failed",
                failure_category="api_error",
                response_id=None,
                output_text=None,
                message="wrong failure linkage",
            )
        ).encode("utf-8")
        events.write_bytes(before[1] + appended)
        returned = WorkflowExecutionPersistenceResult(
            state, events, len(state.read_bytes()), len(appended)
        )
        assert type(returned) is WorkflowExecutionPersistenceResult
        assert returned.state_path is state and returned.events_path is events
        assert returned.state_bytes_written == len(state.read_bytes())
        assert returned.event_bytes_appended == len(appended)
        return returned

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_invalid_persisted_predecessor_history_is_rejected_and_restored(
    tmp_path: Path,
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        persist_fake(*args)
        appended = events.read_bytes()[len(before[1]):]
        mutated_prefix = (
            serialize_runtime_step_event_jsonl(predecessor_event("two", 1)).encode("utf-8")
            + serialize_runtime_step_event_jsonl(predecessor_event("two", 2)).encode("utf-8")
        )
        events.write_bytes(mutated_prefix + appended)
        returned = WorkflowExecutionPersistenceResult(
            state, events, len(state.read_bytes()), len(appended)
        )
        assert type(returned) is WorkflowExecutionPersistenceResult
        assert returned.state_path is state and returned.events_path is events
        assert returned.state_bytes_written == len(state.read_bytes())
        assert returned.event_bytes_appended == len(appended)
        return returned

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_safe_phase120_error_identity_is_preserved_after_compensation(tmp_path: Path, mutation: str | None) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    supplied_error = RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationError("safe detail")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"state-mutated")
        if mutation in ("events", "both"):
            events.write_bytes(b"events-mutated")
        raise supplied_error

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert type(caught.value) is RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationError
    assert caught.value is supplied_error and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_unexpected_phase120_error_is_sanitized_and_restored(tmp_path: Path, mutation: str | None) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"state-mutated")
        if mutation in ("events", "both"):
            events.write_bytes(b"events-mutated")
        raise RuntimeError("secret detail")

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert type(caught.value) is RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value) and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_malformed_return_is_compensated_without_retry(tmp_path: Path, mutation: str | None) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"state-mutated")
        if mutation in ("events", "both"):
            events.write_bytes(b"events-mutated")
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str) -> None:
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
        if failed == "both" or (failed == "state" and path == state) or (failed == "events" and path == events):
            raise OSError("rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", restore)
    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempts.count(state) == 1 and attempts.count(events) == 1 and calls == 1


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_malformed_stop_values_are_zero_call_rejected(tmp_path: Path, kind: str) -> None:
    state, events = setup(tmp_path, "succeeded" if kind == "complete" else "failed")
    result: object = (
        replace(WorkflowProgressionDecision("workflow_complete", "w", "four", 4, "e", None, None, None, "last_step_succeeded"), reason="wrong")
        if kind == "complete"
        else replace(PersistedExecutionOutcome("persisted_failure", "w", "three", 3, "e", "api_error"), failure_category="unknown")
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(result, workflow(), state, events, dependency)
    assert caught.value.detail.classification in {"completion_contract", "failure_contract"} and calls == 0


def test_direct_persisted_success_is_zero_call_rejected(tmp_path: Path) -> None:
    state, events = setup(tmp_path, "succeeded")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    result = PersistedExecutionOutcome("persisted_success", "w", "four", 4, "e", None)
    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(result, workflow(), state, events, dependency)
    assert caught.value.detail.classification == "failure_contract" and calls == 0


def test_unsupported_progression_decision_is_zero_call_rejected(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    result = WorkflowProgressionDecision(
        "prepare_next_step", "w", "three", 3, "e", "four", 4, "e", "next_step_available"
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(result, workflow(), state, events, dependency)
    assert caught.value.detail.classification == "completion_contract" and calls == 0


@pytest.mark.parametrize("target", ["state", "events"])
def test_missing_target_is_rejected_before_phase120(tmp_path: Path, target: str) -> None:
    state, events = setup(tmp_path)
    (state if target == "state" else events).unlink()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == ("state_target" if target == "state" else "event_target") and calls == 0


@pytest.mark.parametrize("target", ["state", "events"])
def test_non_regular_directory_target_is_rejected_before_phase120(
    tmp_path: Path, target: str
) -> None:
    state, events = setup(tmp_path)
    target_path = state if target == "state" else events
    target_path.unlink()
    target_path.mkdir()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == ("state_target" if target == "state" else "event_target")
    assert calls == 0


@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
@pytest.mark.parametrize("target", ["state", "events"])
def test_target_oserror_is_classified_independently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, target: str) -> None:
    state, events = setup(tmp_path)
    target_path = state if target == "state" else events
    original = getattr(Path, operation)

    def fail(path: Path, *args: object, **kwargs: object) -> object:
        if path == target_path:
            raise OSError("synthetic")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == ("state_target" if target == "state" else "event_target")


def test_equal_targets_are_rejected_before_phase120(tmp_path: Path) -> None:
    state, _ = setup(tmp_path)
    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(success(), workflow(), state, state, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "target_conflict"
