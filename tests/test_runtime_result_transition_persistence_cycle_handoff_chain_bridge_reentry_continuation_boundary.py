"""Focused Phase 134 runtime-result persistence bridge tests."""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError,
    WorkflowProgressionDecision,
    route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationError,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationError,
)
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
)
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
from ai_office.storage.running_state_persistence import RunningStatePersistenceResult


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


class StepChild(WorkflowStepDefinition):
    pass


class PersistenceChild(WorkflowExecutionPersistenceResult):
    pass


class IntChild(int):
    pass


class DecisionChild(WorkflowProgressionDecision):
    pass


class OutcomeChild(PersistedExecutionOutcome):
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


def predecessor_event(
    step_id: str,
    step_index: int,
    provider: object = "openai",
    **changes: object,
) -> RuntimeStepEvent:
    return replace(
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            step_id,
            step_index,
            "e",
            "running",
            "succeeded",
            provider,  # type: ignore[arg-type]
            None,
            f"response-{step_id}",
            f"request-{step_id}",
            f"output-{step_id}",
            None,
        ),
        **changes,
    )


def terminal_event(
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    **changes: object,
) -> RuntimeStepEvent:
    invocation = result.invocation_result
    if type(result) is StepRuntimeExecutionSuccess:
        event = RuntimeStepEvent(
            "step_succeeded",
            result.workflow_id,
            result.step_id,
            result.step_index,
            result.employee_id,
            "running",
            "succeeded",
            invocation.provider,
            None,
            invocation.response_id,
            invocation.request_id,
            invocation.text,
            None,
        )
    else:
        event = RuntimeStepEvent(
            "step_failed",
            result.workflow_id,
            result.step_id,
            result.step_index,
            result.employee_id,
            "running",
            "failed",
            invocation.provider,
            invocation.category,
            None,
            invocation.request_id,
            None,
            invocation.message,
        )
    return replace(event, **changes)


def runtime_success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "four",
        4,
        "e",
        ModelInvocationSuccess(
            "openai", "response-four", "request-four", "completed", ("output",), "output"
        ),
    )


def runtime_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w",
        "four",
        4,
        "e",
        ModelInvocationFailure(
            "openai", "api_error", "safe failure", "request-four", 500, None, None
        ),
    )


def setup(tmp_path: Path) -> dict[str, object]:
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state = WorkflowExecutionState(
        "w", "running", "four", 4, "e", ("one", "two", "three"), None
    )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(
                predecessor_event(step_id, index, provider)
            )
            for step_id, index, provider in (
                ("one", 1, "other"),
                ("two", 2, "other"),
                ("three", 3, "openai"),
            )
        ),
        encoding="utf-8",
    )
    return {
        "result": runtime_success(),
        "workflow": workflow(),
        "state_path": state_path,
        "events_path": events_path,
    }


def setup_index(tmp_path: Path, index: int) -> dict[str, object]:
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    selected = workflow().steps[index - 1]
    state = WorkflowExecutionState(
        "w",
        "running",
        selected.id,
        index,
        "e",
        tuple(step.id for step in workflow().steps[: index - 1]),
        None,
    )
    events_path.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(predecessor_event(step.id, position))
            for position, step in enumerate(workflow().steps[: index - 1], 1)
        ),
        encoding="utf-8",
    )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    result = StepRuntimeExecutionSuccess(
        "w",
        selected.id,
        index,
        "e",
        ModelInvocationSuccess("openai", "response", "request", "completed", ("out",), "out"),
    )
    return {
        "result": result,
        "workflow": workflow(),
        "state_path": state_path,
        "events_path": events_path,
    }


def persist_fake(
    result: object,
    _workflow: object,
    state_path: Path,
    events_path: Path,
) -> WorkflowExecutionPersistenceResult:
    assert type(result) in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)
    current = load_workflow_execution_state(state_path)
    invocation = result.invocation_result  # type: ignore[union-attr]
    successful = type(result) is StepRuntimeExecutionSuccess
    state = WorkflowExecutionState(
        result.workflow_id,  # type: ignore[union-attr]
        "succeeded" if successful else "failed",
        result.step_id,  # type: ignore[union-attr]
        result.step_index,  # type: ignore[union-attr]
        result.employee_id,  # type: ignore[union-attr]
        current.completed_step_ids + ((result.step_id,) if successful else ()),  # type: ignore[union-attr]
        None if successful else invocation.category,
    )
    event = terminal_event(result)
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = serialize_runtime_step_event_jsonl(event).encode("utf-8")
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(events_path.read_bytes() + event_bytes)
    return WorkflowExecutionPersistenceResult(
        state_path, events_path, len(state_bytes), len(event_bytes)
    )


def call(values: dict[str, object], dependency: object) -> object:
    supplied = dict(values)
    supplied["phase127_function"] = dependency
    return route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary(
        **supplied  # type: ignore[arg-type]
    )


def reject(
    values: dict[str, object], classification: str, **changes: object
) -> BaseException:
    supplied = dict(values)
    supplied.update(changes)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    supplied["phase127_function"] = dependency
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary(
            **supplied  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == classification
    assert calls == 0
    return caught.value


def reject_after_call(
    values: dict[str, object], result: object, classification: str
) -> None:
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return result

    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    supplied = dict(values)
    supplied["phase127_function"] = dependency
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary(
            **supplied  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == classification
    assert calls == 1
    assert (
        values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    ) == before


def reject_with_dependency(
    values: dict[str, object], dependency: object, classification: str
) -> None:
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    calls = 0

    def counted(*args: object) -> object:
        nonlocal calls
        calls += 1
        return dependency(*args)  # type: ignore[operator]

    supplied = dict(values)
    supplied["phase127_function"] = counted
    with pytest.raises(
        RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary(
            **supplied  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == classification
    assert calls == 1
    assert (
        values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    ) == before


def success_targets(tmp_path: Path, provider: object = "other") -> tuple[Path, Path]:
    state_path, events_path = tmp_path / "succeeded-state", tmp_path / "succeeded-events"
    state = WorkflowExecutionState(
        "w", "succeeded", "four", 4, "e", ("one", "two", "three", "four"), None
    )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(
                predecessor_event(step, index, step_provider)
            )
            for step, index, step_provider in (
                ("one", 1, "other"), ("two", 2, "other"), ("three", 3, "openai")
            )
        )
        + serialize_runtime_step_event_jsonl(
            terminal_event(runtime_success(), provider=provider)
        ),
        encoding="utf-8",
    )
    return state_path, events_path


def failure_targets(tmp_path: Path, provider: object = "other") -> tuple[Path, Path]:
    state_path, events_path = tmp_path / "failed-state", tmp_path / "failed-events"
    state = WorkflowExecutionState(
        "w", "failed", "four", 4, "e", ("one", "two", "three"), "api_error"
    )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(
                predecessor_event(step, index, step_provider)
            )
            for step, index, step_provider in (
                ("one", 1, "other"), ("two", 2, "other"), ("three", 3, "openai")
            )
        )
        + serialize_runtime_step_event_jsonl(
            terminal_event(runtime_failure(), provider=provider)
        ),
        encoding="utf-8",
    )
    return state_path, events_path


def stop_values(tmp_path: Path, kind: str) -> tuple[dict[str, object], object]:
    values = setup(tmp_path)
    if kind == "complete":
        state, events = success_targets(tmp_path)
        result: object = WorkflowProgressionDecision(
            "workflow_complete", "w", "four", 4, "e", None, None, None, "last_step_succeeded"
        )
    else:
        state, events = failure_targets(tmp_path)
        result = PersistedExecutionOutcome(
            "persisted_failure", "w", "four", 4, "e", "api_error"
        )
    values.update(
        result=result,
        state_path=state,
        events_path=events,
    )
    return values, result


def test_public_signature_and_source_audit() -> None:
    function = route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary
    params = list(inspect.signature(function).parameters.values())
    assert [param.name for param in params[:4]] == [
        "result", "workflow", "state_path", "events_path"
    ]
    assert all(param.annotation is object for param in params[:4])
    assert all(
        param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for param in params[:4]
    )
    assert params[4].kind is inspect.Parameter.KEYWORD_ONLY
    from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary import (
        route_runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary,
    )
    assert params[4].default is route_runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary
    source = Path(
        "src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert "route_runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary" in source
    assert "phase120" not in source.lower()
    assert "route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


@pytest.mark.parametrize("result", [runtime_success(), runtime_failure()])
def test_valid_routes_delegate_canonical_identity_once_and_return_exact_object(
    tmp_path: Path, result: object
) -> None:
    values = setup(tmp_path)
    values["result"] = result
    seen: list[tuple[object, ...]] = []
    expected: object = object()

    def dependency(*args: object) -> object:
        seen.append(args)
        assert len(args) == 4
        assert all(
            actual is wanted
            for actual, wanted in zip(
                args,
                tuple(values[key] for key in ("result", "workflow", "state_path", "events_path")),
                strict=True,
            )
        )
        nonlocal expected
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(values, dependency) is expected
    assert len(seen) == 1


@pytest.mark.parametrize("index", [1, 2, 3])
def test_runtime_indices_one_two_three_are_rejected_before_phase127(
    tmp_path: Path, index: int
) -> None:
    values = setup_index(tmp_path, index)
    reject(values, "runtime_contract")


@pytest.mark.parametrize(
    "value",
    [
        SuccessChild("w", "four", 4, "e", runtime_success().invocation_result),
        FailureChild("w", "four", 4, "e", runtime_failure().invocation_result),
        SimpleNamespace(workflow_id="w", step_id="four", step_index=4, employee_id="e"),
    ],
)
def test_result_subclasses_and_substitutes_are_zero_call_rejected(
    tmp_path: Path, value: object
) -> None:
    reject(setup(tmp_path), "result_type", result=value)


def test_workflow_subclass_and_attribute_compatible_substitute_are_zero_call_rejected(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    child = WorkflowChild.model_validate(workflow().model_dump())
    substitute = SimpleNamespace(
        id="w", name="W", description="D", steps=workflow().steps
    )
    for value in (child, substitute):
        reject(values, "workflow_definition", workflow=value)


def test_workflow_step_subclass_and_attribute_compatible_substitute_are_rejected(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    original = values["workflow"]
    for replacement in (
        StepChild(id="four", name="Four", employee="e", instructions="four"),
        SimpleNamespace(id="four", name="Four", employee="e", instructions="four"),
    ):
        steps = list(original.steps)  # type: ignore[union-attr]
        steps[-1] = replacement
        candidate = WorkflowDefinition.model_construct(
            id="w", name="W", description="D", steps=steps
        )
        reject(values, "workflow_definition", workflow=candidate)


@pytest.mark.parametrize("nested", ["subclass", "substitute"])
def test_nested_invocation_result_subclasses_and_substitutes_are_zero_call_rejected(
    tmp_path: Path, nested: str
) -> None:
    values = setup(tmp_path)
    invocation = runtime_success().invocation_result
    replacement: object = (
        SuccessInvocationChild(*invocation.__dict__.values())
        if nested == "subclass"
        else SimpleNamespace(**invocation.__dict__)
    )
    reject(
        values,
        "runtime_contract",
        result=replace(runtime_success(), invocation_result=replacement),
    )


@pytest.mark.parametrize("field,value", [("provider", "other"), ("provider", 4), ("request_id", 4), ("text", "wrong"), ("text_parts", ["output"])])
def test_runtime_success_nested_contract_is_strict(
    tmp_path: Path, field: str, value: object
) -> None:
    result = runtime_success()
    bad = replace(result, invocation_result=replace(result.invocation_result, **{field: value}))
    reject(setup(tmp_path), "runtime_contract", result=bad)


@pytest.mark.parametrize("field,value", [("provider", "other"), ("category", "unknown"), ("message", None), ("status_code", True), ("request_id", 4)])
def test_runtime_failure_nested_contract_is_strict(
    tmp_path: Path, field: str, value: object
) -> None:
    result = runtime_failure()
    bad = replace(result, invocation_result=replace(result.invocation_result, **{field: value}))
    reject(setup(tmp_path), "runtime_contract", result=bad)


@pytest.mark.parametrize("field,value", [("workflow_id", "other"), ("step_id", "other"), ("step_index", True), ("employee_id", "other")])
def test_runtime_linkage_is_rejected_before_phase127(
    tmp_path: Path, field: str, value: object
) -> None:
    reject(setup(tmp_path), "runtime_contract", result=replace(runtime_success(), **{field: value}))


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"])
def test_predecessor_history_matrix_is_rejected_before_phase127(
    tmp_path: Path, mutation: str
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    unrelated = serialize_runtime_step_event_jsonl(
        predecessor_event("unrelated", 99)
    )
    if mutation == "duplicate":
        content = "".join(lines + [lines[-1]])
    elif mutation == "missing":
        content = "".join(lines[:-1])
    elif mutation == "reordered":
        content = lines[1] + lines[0] + lines[2]
    elif mutation == "unrelated":
        content = unrelated + "".join(lines[1:])
    elif mutation == "malformed":
        content = "{malformed}\n"
    else:
        content = "".join(lines) + unrelated
    events.write_text(content, encoding="utf-8")  # type: ignore[union-attr]
    reject(values, "runtime_contract")


@pytest.mark.parametrize(
    "step_id,position,provider,request_id",
    [
        ("one", 1, "other", None),
        ("three", 3, "openai", ""),
    ],
)
def test_predecessor_request_id_provenance_is_required(
    tmp_path: Path,
    step_id: str,
    position: int,
    provider: object,
    request_id: object,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event(step_id, position, provider, request_id=request_id)
    )
    events.write_text(
        "".join(lines[: position - 1]) + replacement + "".join(lines[position:]),
        encoding="utf-8",
    )  # type: ignore[union-attr]
    reject(values, "runtime_contract")


def test_immediate_predecessor_none_request_id_nonempty_output_delegates_once(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("three", 3, "openai", request_id=None)
    )
    events.write_text("".join(lines[:2]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    seen: list[tuple[object, ...]] = []
    expected: object = None

    def dependency(*args: object) -> object:
        seen.append(args)
        nonlocal expected
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(values, dependency) is expected
    assert len(seen) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            seen[0],
            tuple(
                values[key]
                for key in ("result", "workflow", "state_path", "events_path")
            ),
            strict=True,
        )
    )


def test_immediate_predecessor_none_request_id_empty_output_delegates_once(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("three", 3, "openai", request_id=None, output_text="")
    )
    events.write_text("".join(lines[:2]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(values, dependency) is expected


def test_earlier_predecessor_none_request_id_is_rejected_before_phase127(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("one", 1, "other", request_id=None)
    )
    events.write_text(replacement + "".join(lines[1:]), encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "runtime_contract")


@pytest.mark.parametrize("request_id", ["", 123, True])
def test_immediate_predecessor_invalid_request_ids_are_rejected_before_phase127(
    tmp_path: Path, request_id: object
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("three", 3, "openai", request_id=request_id)
    )
    events.write_text("".join(lines[:2]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "runtime_contract")


@pytest.mark.parametrize("provider", ["other", 4])
def test_immediate_predecessor_provider_must_be_openai(
    tmp_path: Path, provider: object
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("three", 3, provider)
    )
    events.write_text("".join(lines[:2]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    reject(values, "runtime_contract")


def test_earlier_predecessor_non_openai_provider_remains_allowed(tmp_path: Path) -> None:
    values = setup(tmp_path)
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(values, dependency) is expected


@pytest.mark.parametrize("result", [runtime_success(), runtime_failure()])
def test_persistence_return_must_be_exact_and_target_identical(
    tmp_path: Path, result: object
) -> None:
    values = setup(tmp_path)
    values["result"] = result
    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        return replace(persisted, state_path=Path("not-the-supplied-target"))

    reject_with_dependency(values, dependency, "persistence_contract")


@pytest.mark.parametrize("return_value", [object(), SimpleNamespace(state_path=Path("s"), events_path=Path("e"), state_bytes_written=1, event_bytes_appended=1)])
def test_malformed_persistence_returns_are_rejected_and_compensated(
    tmp_path: Path, return_value: object
) -> None:
    values = setup(tmp_path)
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]

    def dependency(*args: object) -> object:
        persist_fake(*args)  # type: ignore[arg-type]
        values["state_path"].write_bytes(b"invalid")  # type: ignore[union-attr]
        values["events_path"].write_bytes(b"invalid")  # type: ignore[union-attr]
        return return_value

    calls = 0

    def counted(*args: object) -> object:
        nonlocal calls
        calls += 1
        return dependency(*args)

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(values, counted)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_persistence_result_subclass_is_rejected_and_restored(tmp_path: Path) -> None:
    values = setup(tmp_path)

    def dependency(*args: object) -> object:
        value = persist_fake(*args)  # type: ignore[arg-type]
        return PersistenceChild(*value.__dict__.values())

    reject_with_dependency(values, dependency, "persistence_contract")


def test_fully_compatible_persistence_result_substitute_is_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path)

    def dependency(*args: object) -> object:
        value = persist_fake(*args)  # type: ignore[arg-type]
        return SimpleNamespace(
            state_path=value.state_path,
            events_path=value.events_path,
            state_bytes_written=value.state_bytes_written,
            event_bytes_appended=value.event_bytes_appended,
        )

    reject_with_dependency(values, dependency, "persistence_contract")


@pytest.mark.parametrize("field,value", [("state_bytes_written", 0), ("state_bytes_written", -1), ("state_bytes_written", True), ("state_bytes_written", IntChild(1)), ("event_bytes_appended", 0), ("event_bytes_appended", -1), ("event_bytes_appended", True), ("event_bytes_appended", IntChild(1))])
def test_persistence_byte_counts_require_positive_exact_int(
    tmp_path: Path, field: str, value: object
) -> None:
    values = setup(tmp_path)

    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        return replace(persisted, **{field: value})

    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    calls = 0

    def counted(*args: object) -> object:
        nonlocal calls
        calls += 1
        return dependency(*args)

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(values, counted)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_persistence_count_wrong_positive_is_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path)

    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        return replace(persisted, state_bytes_written=persisted.state_bytes_written + 1)

    reject_with_dependency(values, dependency, "persistence_contract")


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_persistence_target_mutation_is_compensated_without_retry(
    tmp_path: Path, mutation: str
) -> None:
    values = setup(tmp_path)
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        if mutation in ("state", "both"):
            values["state_path"].write_bytes(b"mutated-state")  # type: ignore[union-attr]
        if mutation in ("events", "both"):
            values["events_path"].write_bytes(b"mutated-events")  # type: ignore[union-attr]
        return persisted

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(values, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_invalid_persisted_terminal_state_is_rejected_and_restored(tmp_path: Path) -> None:
    values = setup(tmp_path)

    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        invalid = WorkflowExecutionState("w", "running", "four", 4, "e", ("one", "two", "three"), None)
        state_bytes = serialize_workflow_execution_state_json(invalid).encode("utf-8")
        values["state_path"].write_bytes(state_bytes)  # type: ignore[union-attr]
        return replace(persisted, state_bytes_written=len(state_bytes))

    reject_with_dependency(values, dependency, "persistence_contract")


@pytest.mark.parametrize("field,value", [("workflow_id", "other"), ("step_id", "other"), ("step_index", 3), ("employee_id", "other"), ("provider", "other"), ("provider", 4), ("request_id", "other-request")])
def test_invalid_persisted_terminal_event_linkage_is_rejected_and_restored(
    tmp_path: Path, field: str, value: object
) -> None:
    values = setup(tmp_path)
    before = values["events_path"].read_bytes()  # type: ignore[union-attr]

    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        result = values["result"]
        event = terminal_event(result, **{field: value})  # type: ignore[arg-type]
        values["events_path"].write_bytes(  # type: ignore[union-attr]
            before + serialize_runtime_step_event_jsonl(event).encode("utf-8")
        )
        event_bytes = serialize_runtime_step_event_jsonl(event).encode("utf-8")
        return replace(persisted, event_bytes_appended=len(event_bytes))

    reject_with_dependency(values, dependency, "persistence_contract")


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_type", "step_failed"),
        ("next_status", "failed"),
        ("failure_category", "api_error"),
        ("response_id", None),
        ("output_text", None),
        ("message", "wrong success message"),
    ],
)
def test_invalid_persisted_terminal_event_semantics_are_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    values = setup(tmp_path)
    before = values["events_path"].read_bytes()  # type: ignore[union-attr]

    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        event = terminal_event(values["result"], **{field: value})  # type: ignore[arg-type]
        appended = serialize_runtime_step_event_jsonl(event).encode("utf-8")
        values["events_path"].write_bytes(before + appended)  # type: ignore[union-attr]
        return replace(persisted, event_bytes_appended=len(appended))

    reject_with_dependency(values, dependency, "persistence_contract")


def test_invalid_persisted_terminal_event_kind_and_linkage_is_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path)
    before = values["events_path"].read_bytes()  # type: ignore[union-attr]

    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        event = terminal_event(
            values["result"],  # type: ignore[arg-type]
            event_type="step_failed",
            next_status="failed",
            failure_category="api_error",
            response_id=None,
            output_text=None,
            message="wrong linkage",
        )
        appended = serialize_runtime_step_event_jsonl(event).encode("utf-8")
        values["events_path"].write_bytes(before + appended)  # type: ignore[union-attr]
        return replace(persisted, event_bytes_appended=len(appended))

    reject_with_dependency(values, dependency, "persistence_contract")


def test_invalid_persisted_predecessor_history_is_rejected_and_restored(tmp_path: Path) -> None:
    values = setup(tmp_path)
    before = values["events_path"].read_bytes()  # type: ignore[union-attr]

    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        appended = values["events_path"].read_bytes()[len(before) :]  # type: ignore[union-attr]
        rewritten = (
            serialize_runtime_step_event_jsonl(predecessor_event("two", 1)).encode("utf-8")
            + serialize_runtime_step_event_jsonl(predecessor_event("one", 2)).encode("utf-8")
            + serialize_runtime_step_event_jsonl(predecessor_event("three", 3)).encode("utf-8")
            + appended
        )
        values["events_path"].write_bytes(rewritten)  # type: ignore[union-attr]
        return replace(persisted, event_bytes_appended=len(appended))

    reject_with_dependency(values, dependency, "persistence_contract")


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_safe_phase127_error_identity_is_preserved_after_compensation(
    tmp_path: Path, mutation: str | None
) -> None:
    values = setup(tmp_path)
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    supplied_error = RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationError("safe detail")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"mutated-state")  # type: ignore[union-attr]
        if mutation in ("events", "both"):
            events.write_bytes(b"mutated-events")  # type: ignore[union-attr]
        raise supplied_error

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationError) as caught:
        call(values, dependency)
    assert caught.value is supplied_error and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_unexpected_error_is_sanitized_and_compensated(
    tmp_path: Path, mutation: str | None
) -> None:
    values = setup(tmp_path)
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"mutated-state")  # type: ignore[union-attr]
        if mutation in ("events", "both"):
            events.write_bytes(b"mutated-events")  # type: ignore[union-attr]
        raise RuntimeError("secret detail")

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(values, dependency)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value) and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_malformed_return_is_compensated_without_retry(
    tmp_path: Path, mutation: str | None
) -> None:
    values = setup(tmp_path)
    state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"mutated-state")  # type: ignore[union-attr]
        if mutation in ("events", "both"):
            events.write_bytes(b"mutated-events")  # type: ignore[union-attr]
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(values, dependency)
    assert caught.value.detail.classification == "persistence_contract" and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path, failed_target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = setup(tmp_path)
    state, events = values["state_path"], values["events_path"]
    original_write = Path.write_bytes
    restore_calls = {"state": 0, "events": 0}
    dependency_calls = 0

    def restore(path: Path, data: bytes) -> int:
        key = "state" if path == state else "events"
        restore_calls[key] += 1
        if failed_target in (key, "both"):
            raise OSError("rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", restore)

    def dependency(*_: object) -> object:
        nonlocal dependency_calls
        dependency_calls += 1
        original_write(state, b"mutated-state")
        original_write(events, b"mutated-events")
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(values, dependency)
    assert caught.value.detail.classification == "dependency_rollback"
    assert restore_calls == {"state": 1, "events": 1}
    assert dependency_calls == 1


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_allow_non_openai_terminal_provider_and_are_zero_call(
    tmp_path: Path, kind: str
) -> None:
    values, result = stop_values(tmp_path, kind)
    calls = 0
    values["state_path"], values["events_path"] = (
        success_targets(tmp_path, "other") if kind == "complete" else failure_targets(tmp_path, "other")
    )
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert call(values, dependency) is result
    assert calls == 0
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_subclasses_and_compatible_substitutes_are_zero_call_rejected(
    tmp_path: Path, kind: str
) -> None:
    values, result = stop_values(tmp_path, kind)
    child = (
        DecisionChild(*result.__dict__.values())
        if kind == "complete"
        else OutcomeChild(*result.__dict__.values())
    )
    substitute = SimpleNamespace(**result.__dict__)
    for replacement in (child, substitute):
        reject(values, "result_type", result=replacement)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_current_step_index_bool_and_int_subclass_are_zero_call_rejected(
    tmp_path: Path, kind: str
) -> None:
    values, result = stop_values(tmp_path, kind)
    classification = "completion_contract" if kind == "complete" else "failure_contract"
    for replacement in (True, IntChild(4)):
        reject(values, classification, result=replace(result, current_step_index=replacement))


def test_stop_malformed_values_and_unsupported_results_are_zero_call_rejected(
    tmp_path: Path,
) -> None:
    values, complete = stop_values(tmp_path, "complete")
    reject(values, "completion_contract", result=replace(complete, reason="wrong"))
    values, failure = stop_values(tmp_path, "failure")
    reject(values, "failure_contract", result=replace(failure, failure_category="unknown"))
    unsupported = WorkflowProgressionDecision(
        "prepare_next_step", "w", "four", 4, "e", None, None, None, "unsupported"
    )
    reject(setup(tmp_path), "completion_contract", result=unsupported)


@pytest.mark.parametrize(
    "value",
    [
        WorkflowExecutionPersistenceResult(Path("s"), Path("e"), 1, 1),
        SimpleNamespace(request="request", running_state="state"),
    ],
)
def test_direct_non_phase133_results_are_zero_call_rejected(
    tmp_path: Path, value: object
) -> None:
    reject(setup(tmp_path), "result_type", result=value)


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_missing_and_directory_targets_are_rejected_before_phase127(
    tmp_path: Path, target: str
) -> None:
    values = setup(tmp_path)
    path = values[target]
    path.unlink()  # type: ignore[union-attr]
    reject(values, "state_target" if target == "state_path" else "event_target")
    path.mkdir()
    reject(values, "state_target" if target == "state_path" else "event_target")


def test_target_conflict_and_non_callable_dependency_are_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path)
    reject(values, "target_conflict", events_path=values["state_path"])
    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary(
            **values, phase127_function=object()  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_target_oserror_is_classified_by_target(
    tmp_path: Path,
    operation: str,
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    selected = values[target]
    original = getattr(Path, operation)

    def raising(path: Path, *args: object, **kwargs: object) -> object:
        if path == selected:
            raise OSError("synthetic")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, raising)
    reject(values, "state_target" if target == "state_path" else "event_target")


def reject_unchanged(
    values: dict[str, object], classification: str, **changes: object
) -> BaseException:
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    caught = reject(values, classification, **changes)
    assert (
        values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    ) == before
    return caught


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(
            workflow_id="w",
            step_id="four",
            step_index=4,
            employee_id="e",
            invocation_result=runtime_success().invocation_result,
        ),
        SimpleNamespace(
            workflow_id="w",
            step_id="four",
            step_index=4,
            employee_id="e",
            invocation_result=runtime_failure().invocation_result,
        ),
    ],
)
def test_runtime_result_fully_compatible_substitutes_are_zero_call_rejected(
    tmp_path: Path, result: object
) -> None:
    reject_unchanged(setup(tmp_path), "result_type", result=result)


@pytest.mark.parametrize("result_factory", [runtime_success, runtime_failure])
@pytest.mark.parametrize(
    "field",
    [
        "current_step_index_bool",
        "current_step_index_int_subclass",
        "status",
        "current_step_id",
        "current_employee_id",
        "completed_step_ids_tuple",
        "completed_step_ids_list",
        "last_failure_category",
        "workflow_id",
    ],
)
def test_persisted_running_state_contract_is_revalidated_before_phase127(
    tmp_path: Path,
    result_factory: object,
    field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    values["result"] = result_factory()  # type: ignore[operator]
    state_path = values["state_path"]
    state = load_workflow_execution_state(state_path)  # type: ignore[arg-type]
    result = values["result"]
    if field == "current_step_index_bool":
        change = {"current_step_index": True}
    elif field == "current_step_index_int_subclass":
        change = {"current_step_index": IntChild(4)}
    elif field == "status":
        change = {"status": "succeeded"}
    elif field == "current_step_id":
        change = {"current_step_id": "other-step"}
    elif field == "current_employee_id":
        change = {"current_employee_id": "other-employee"}
    elif field == "completed_step_ids_tuple":
        change = {"completed_step_ids": ("one", "two", "wrong")}
    elif field == "completed_step_ids_list":
        change = {"completed_step_ids": ["one", "two", "three"]}
    elif field == "last_failure_category":
        change = {
            "last_failure_category": (
                "api_error"
                if type(result) is StepRuntimeExecutionSuccess
                else "transport_error"
            )
        }
    else:
        change = {"workflow_id": "other-workflow"}
    corrupted = replace(state, **change)
    state_bytes = serialize_workflow_execution_state_json(corrupted).encode("utf-8")
    state_path.write_bytes(state_bytes)  # type: ignore[union-attr]
    loaded_events = tuple(
        predecessor_event(step_id, index, provider)
        for step_id, index, provider in (
            ("one", 1, "other"),
            ("two", 2, "other"),
            ("three", 3, "openai"),
        )
    )
    if field in {"current_step_index_int_subclass", "completed_step_ids_list"}:
        monkeypatch.setattr(
            "ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary.load_workflow_execution_history",
            lambda _targets: SimpleNamespace(state=corrupted, events=loaded_events),
        )
    reject_unchanged(values, "runtime_contract")


@pytest.mark.parametrize("provider", ["", 4])
def test_earlier_predecessor_provider_must_be_nonempty_builtin_string(
    tmp_path: Path, provider: object
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("one", 1, provider)
    )
    events.write_text(replacement + "".join(lines[1:]), encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "runtime_contract")


def test_predecessor_request_id_must_be_nonempty_builtin_string(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("three", 3, "openai", request_id=4)
    )
    events.write_text("".join(lines[:2]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "runtime_contract")


@pytest.mark.parametrize("result_factory", [runtime_success, runtime_failure])
@pytest.mark.parametrize(
    "field",
    [
        "malformed_bytes",
        "workflow_id",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "completed_step_ids",
        "last_failure_category",
    ],
)
def test_persisted_terminal_state_matrix_is_compensated_without_retry(
    tmp_path: Path, result_factory: object, field: str
) -> None:
    values = setup(tmp_path)
    result = result_factory()  # type: ignore[operator]
    values["result"] = result

    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        state_path = values["state_path"]
        if field == "malformed_bytes":
            state_bytes = b"{malformed persisted state}\n"
        else:
            current = load_workflow_execution_state(state_path)  # type: ignore[arg-type]
            if field == "workflow_id":
                change = {"workflow_id": "other-workflow"}
            elif field == "current_step_id":
                change = {"current_step_id": "other-step"}
            elif field == "current_step_index":
                change = {"current_step_index": 3}
            elif field == "current_employee_id":
                change = {"current_employee_id": "other-employee"}
            elif field == "completed_step_ids":
                change = {"completed_step_ids": ("one", "two", "wrong", "four")}
                if type(result) is StepRuntimeExecutionFailure:
                    change = {"completed_step_ids": ("one", "two", "wrong")}
            else:
                change = {
                    "last_failure_category": (
                        "api_error"
                        if type(result) is StepRuntimeExecutionSuccess
                        else "transport_error"
                    )
                }
            state_bytes = serialize_workflow_execution_state_json(
                replace(current, **change)
            ).encode("utf-8")
        state_path.write_bytes(state_bytes)  # type: ignore[union-attr]
        return replace(persisted, state_bytes_written=len(state_bytes))

    reject_with_dependency(values, dependency, "persistence_contract")


@pytest.mark.parametrize("result_factory", [runtime_success, runtime_failure])
@pytest.mark.parametrize(
    "mutation",
    [
        "prefix_removal",
        "prefix_duplication",
        "prefix_reorder",
        "prefix_rewrite",
        "terminal_missing",
        "terminal_twice",
        "terminal_malformed",
        "unrelated_extra",
    ],
)
def test_persisted_event_prefix_and_append_invariants_are_compensated(
    tmp_path: Path, result_factory: object, mutation: str
) -> None:
    values = setup(tmp_path)
    result = result_factory()  # type: ignore[operator]
    values["result"] = result
    original_events = values["events_path"].read_bytes()  # type: ignore[union-attr]
    lines = original_events.splitlines(keepends=True)

    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        valid_append = values["events_path"].read_bytes()[len(original_events) :]  # type: ignore[union-attr]
        unrelated = serialize_runtime_step_event_jsonl(
            predecessor_event("unrelated", 99)
        ).encode("utf-8")
        if mutation == "prefix_removal":
            content = b"".join(lines[:-1]) + valid_append
        elif mutation == "prefix_duplication":
            content = original_events + lines[-1] + valid_append
        elif mutation == "prefix_reorder":
            content = lines[1] + lines[0] + lines[2] + valid_append
        elif mutation == "prefix_rewrite":
            rewritten = serialize_runtime_step_event_jsonl(
                predecessor_event("rewritten", 1)
            ).encode("utf-8")
            content = rewritten + b"".join(lines[1:]) + valid_append
        elif mutation == "terminal_missing":
            content = original_events
        elif mutation == "terminal_twice":
            content = original_events + valid_append + valid_append
        elif mutation == "terminal_malformed":
            valid_append = b"{malformed terminal event}\n"
            content = original_events + valid_append
        else:
            content = original_events + valid_append + unrelated
        values["events_path"].write_bytes(content)  # type: ignore[union-attr]
        return replace(persisted, event_bytes_appended=len(content) - len(original_events))

    reject_with_dependency(values, dependency, "persistence_contract")


def test_event_bytes_appended_wrong_positive_is_rejected_and_compensated(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path)
    original_events = values["events_path"].read_bytes()  # type: ignore[union-attr]

    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        actual = len(values["events_path"].read_bytes()) - len(original_events)  # type: ignore[union-attr]
        return replace(persisted, event_bytes_appended=actual + 1)

    reject_with_dependency(values, dependency, "persistence_contract")


@pytest.mark.parametrize(
    "field,value",
    [
        ("failure_category", "transport_error"),
        ("message", "wrong failure message"),
        ("request_id", "wrong-request"),
        ("response_id", "unexpected-response"),
        ("output_text", "unexpected-output"),
        ("provider", "other"),
        ("provider", 4),
    ],
)
def test_failed_terminal_event_semantics_are_revalidated_and_compensated(
    tmp_path: Path, field: str, value: object
) -> None:
    values = setup(tmp_path)
    values["result"] = runtime_failure()
    original_events = values["events_path"].read_bytes()  # type: ignore[union-attr]

    def dependency(*args: object) -> object:
        persisted = persist_fake(*args)  # type: ignore[arg-type]
        event = terminal_event(values["result"], **{field: value})  # type: ignore[arg-type]
        appended = serialize_runtime_step_event_jsonl(event).encode("utf-8")
        values["events_path"].write_bytes(original_events + appended)  # type: ignore[union-attr]
        return replace(persisted, event_bytes_appended=len(appended))

    reject_with_dependency(values, dependency, "persistence_contract")


@pytest.mark.parametrize(
    "value",
    [
        WorkflowExecutionPersistenceResult(Path("s"), Path("e"), 1, 1),
        RunningStatePersistenceResult(1),
        PreparedStepExecutionStart(
            ModelInvocationRequest("model", "system", "task", ("tool",)),
            WorkflowExecutionState(
                "w", "running", "four", 4, "e", ("one", "two", "three"), None
            ),
        ),
    ],
)
def test_direct_unsupported_exact_models_are_zero_call_rejected_and_unchanged(
    tmp_path: Path, value: object
) -> None:
    reject_unchanged(setup(tmp_path), "result_type", result=value)


@pytest.mark.parametrize("result_factory", [runtime_success, runtime_failure])
def test_immediate_predecessor_empty_output_text_delegates_once_canonical_order(
    tmp_path: Path, result_factory: object
) -> None:
    values = setup(tmp_path)
    values["result"] = result_factory()  # type: ignore[operator]
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("three", 3, "openai", output_text="")
    )
    events.write_text("".join(lines[:2]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    seen: list[tuple[object, ...]] = []
    expected: object = None

    def dependency(*args: object) -> object:
        seen.append(args)
        nonlocal expected
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(values, dependency) is expected
    assert len(seen) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            seen[0],
            tuple(values[key] for key in ("result", "workflow", "state_path", "events_path")),
            strict=True,
        )
    )


@pytest.mark.parametrize("result_factory", [runtime_success, runtime_failure])
def test_earlier_empty_output_text_survives_later_succeeded_predecessor(
    tmp_path: Path, result_factory: object
) -> None:
    values = setup(tmp_path)
    values["result"] = result_factory()  # type: ignore[operator]
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    earlier = serialize_runtime_step_event_jsonl(
        predecessor_event("one", 1, "other", output_text="")
    )
    events.write_text(earlier + "".join(lines[1:]), encoding="utf-8")  # type: ignore[union-attr]
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(values, dependency) is expected


@pytest.mark.parametrize("result_factory", [runtime_success, runtime_failure])
def test_predecessor_nonempty_output_text_remains_accepted(
    tmp_path: Path, result_factory: object
) -> None:
    values = setup(tmp_path)
    values["result"] = result_factory()  # type: ignore[operator]
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(values, dependency) is expected


@pytest.mark.parametrize("output_text", [4, None, ["output"]])
def test_predecessor_output_text_non_string_is_rejected(
    tmp_path: Path, output_text: object
) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("three", 3, "openai", output_text=output_text)
    )
    events.write_text("".join(lines[:2]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "runtime_contract")


@pytest.mark.parametrize("result_factory", [runtime_success, runtime_failure])
def test_immediate_predecessor_empty_output_text_still_requires_openai_provider(
    tmp_path: Path, result_factory: object
) -> None:
    values = setup(tmp_path)
    values["result"] = result_factory()  # type: ignore[operator]
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("three", 3, "other", output_text="")
    )
    events.write_text("".join(lines[:2]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "runtime_contract")


@pytest.mark.parametrize("result_factory", [runtime_success, runtime_failure])
def test_predecessor_empty_output_text_still_requires_response_id(
    tmp_path: Path, result_factory: object
) -> None:
    values = setup(tmp_path)
    values["result"] = result_factory()  # type: ignore[operator]
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("three", 3, "openai", output_text="", response_id="")
    )
    events.write_text("".join(lines[:2]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "runtime_contract")


def test_stop_route_empty_predecessor_output_text_remains_rejected(
    tmp_path: Path,
) -> None:
    values, _result = stop_values(tmp_path, "complete")
    state_path, events_path = values["state_path"], values["events_path"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("three", 3, "openai", output_text="")
    )
    events_path.write_text("".join(lines[:2]) + replacement + "".join(lines[3:]), encoding="utf-8")  # type: ignore[union-attr]
    before = state_path.read_bytes(), events_path.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(RuntimeResultTransitionPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary(
            **values, phase127_function=dependency  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (state_path.read_bytes(), events_path.read_bytes()) == before  # type: ignore[union-attr]


def accumulated_workflow() -> WorkflowDefinition:
    """Eight-step workflow exposing positions 5 and 6 as accumulated history."""
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{index}",
                    "name": f"Step {index}",
                    "employee": "e",
                    "instructions": f"step-{index}",
                }
                for index in range(1, 9)
            ],
        }
    )


def setup_accumulated(
    tmp_path: Path,
    *,
    five_provider: object = "openai",
    six_provider: object = "openai",
) -> dict[str, object]:
    """Running step-7 state with six predecessors.

    Positions 1-4 use the default non-openai predecessors; positions 5 and 6
    carry accumulated None request ids with the openai provider by default.
    """
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    wf = accumulated_workflow()
    state = WorkflowExecutionState(
        "w",
        "running",
        "step-7",
        7,
        "e",
        tuple(step.id for step in wf.steps[:6]),
        None,
    )
    lines = []
    for position, step in enumerate(wf.steps[:6], 1):
        if position >= 5:
            provider = five_provider if position == 5 else six_provider
            lines.append(
                serialize_runtime_step_event_jsonl(
                    predecessor_event(step.id, position, provider, request_id=None)
                )
            )
        else:
            lines.append(
                serialize_runtime_step_event_jsonl(
                    predecessor_event(step.id, position, "other")
                )
            )
    events_path.write_text("".join(lines), encoding="utf-8")
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    result = StepRuntimeExecutionSuccess(
        "w",
        "step-7",
        7,
        "e",
        ModelInvocationSuccess(
            "openai", "response", "request", "completed", ("out",), "out"
        ),
    )
    return {
        "result": result,
        "workflow": wf,
        "state_path": state_path,
        "events_path": events_path,
    }


def test_accumulated_none_request_id_positions_five_six_success_delegates_once(
    tmp_path: Path,
) -> None:
    values = setup_accumulated(tmp_path)
    seen: list[tuple[object, ...]] = []
    expected: object = None

    def dependency(*args: object) -> object:
        seen.append(args)
        nonlocal expected
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(values, dependency) is expected
    assert len(seen) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            seen[0],
            tuple(
                values[key]
                for key in ("result", "workflow", "state_path", "events_path")
            ),
            strict=True,
        )
    )


def test_accumulated_none_request_id_positions_five_six_failure_route_delegates_once(
    tmp_path: Path,
) -> None:
    values = setup_accumulated(tmp_path)
    values["result"] = StepRuntimeExecutionFailure(
        "w",
        "step-7",
        7,
        "e",
        ModelInvocationFailure(
            "openai", "api_error", "safe failure", "request", 500, None, None
        ),
    )
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(values, dependency) is expected


def test_accumulated_none_position_five_non_openai_provider_is_rejected_before_phase127(
    tmp_path: Path,
) -> None:
    values = setup_accumulated(tmp_path, five_provider="other")
    reject_unchanged(values, "runtime_contract")


def test_accumulated_none_position_four_remains_rejected_before_phase127(
    tmp_path: Path,
) -> None:
    values = setup_accumulated(tmp_path)
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("step-4", 4, "other", request_id=None)
    )
    events.write_text("".join(lines[:3]) + replacement + "".join(lines[4:]), encoding="utf-8")  # type: ignore[union-attr]
    reject_unchanged(values, "runtime_contract")
