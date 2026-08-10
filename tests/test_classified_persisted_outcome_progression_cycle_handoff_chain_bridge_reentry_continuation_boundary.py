"""Focused tests for the Phase 136 classified persisted-outcome outer bridge."""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeReentryContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary,
)
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeReentryContinuationError as Phase136Error,
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeReentryContinuationFailureDetail,
    route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary as public_phase136,
)
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeReentryContinuationCompatibilityError as PublicPhase136CompatibilityError,
)
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationCompatibilityError as Phase129CompatibilityError,
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationError as Phase129Error,
    route_classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary as public_phase129,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class OutcomeChild(PersistedExecutionOutcome):
    pass


class DecisionChild(WorkflowProgressionDecision):
    pass


class WorkflowChild(WorkflowDefinition):
    pass


class StepChild(WorkflowStepDefinition):
    pass


class PathChild(type(Path())):
    pass


class IntChild(int):
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
                {"id": "five", "name": "Five", "employee": "e", "instructions": "five"},
            ],
        }
    )


def predecessor_event(
    step: WorkflowStepDefinition,
    index: int,
    *,
    provider: object = "other",
    request_id: object = "request",
    response_id: object = "response",
    output_text: object = "output",
    **changes: object,
) -> RuntimeStepEvent:
    return replace(
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            step.id,
            index,
            step.employee,
            "running",
            "succeeded",
            provider,
            None,
            response_id,
            request_id,
            output_text,
            None,
        ),
        **changes,
    )


def terminal_event(
    step: WorkflowStepDefinition,
    index: int,
    status: str,
    *,
    provider: object = "openai",
    output_text: object = "output",
    request_id: object = "request",
    response_id: object = "response",
    **changes: object,
) -> RuntimeStepEvent:
    failed = status == "failed"
    return replace(
        RuntimeStepEvent(
            "step_failed" if failed else "step_succeeded",
            "w",
            step.id,
            index,
            step.employee,
            "running",
            status,
            provider,
            "api_error" if failed else None,
            None if failed else response_id,
            request_id,
            None if failed else output_text,
            "safe failure" if failed else None,
        ),
        **changes,
    )


def data(
    tmp_path: Path,
    *,
    index: int = 4,
    status: str = "succeeded",
    terminal_provider: object = "openai",
    terminal_output: object = "output",
    terminal_request_id: object = "request",
    terminal_response_id: object = "response",
    predecessor_providers: dict[int, object] | None = None,
) -> dict[str, object]:
    definition = workflow()
    step = definition.steps[index - 1]
    predecessor_providers = predecessor_providers or {}
    predecessors = tuple(
        predecessor_event(
            predecessor,
            position,
            provider=predecessor_providers.get(
                position, "openai" if position == index - 1 else "other"
            ),
        )
        for position, predecessor in enumerate(definition.steps[: index - 1], 1)
    )
    state = WorkflowExecutionState(
        "w",
        status,
        step.id,
        index,
        step.employee,
        tuple(item.id for item in definition.steps[:index])
        if status == "succeeded"
        else tuple(item.id for item in definition.steps[: index - 1]),
        None if status == "succeeded" else "api_error",
    )
    terminal = terminal_event(
        step,
        index,
        status,
        provider=terminal_provider,
        output_text=terminal_output,
        request_id=terminal_request_id,
        response_id=terminal_response_id,
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result: object = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        step.id,
        index,
        step.employee,
        None if status == "succeeded" else "api_error",
    )
    return {
        "result": result,
        "workflow": definition,
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_bytes,
        "before_events": event_bytes,
    }


def completion_data(
    tmp_path: Path, *, provider: object = "other", output: object = "output"
) -> dict[str, object]:
    current = data(tmp_path, index=5, terminal_provider=provider, terminal_output=output)
    result = current["result"]
    current["result"] = WorkflowProgressionDecision(
        "workflow_complete",
        result.workflow_id,
        result.current_step_id,
        result.current_step_index,
        result.current_employee_id,
        None,
        None,
        None,
        "last_step_succeeded",
    )
    return current


def before(data_set: dict[str, object]) -> tuple[bytes, bytes]:
    return (
        data_set["state_path"].read_bytes(),  # type: ignore[union-attr]
        data_set["events_path"].read_bytes(),  # type: ignore[union-attr]
    )


def set_before(data_set: dict[str, object]) -> None:
    data_set["before_state"], data_set["before_events"] = before(data_set)


def unchanged(data_set: dict[str, object]) -> None:
    assert before(data_set) == (data_set["before_state"], data_set["before_events"])


def invoke(data_set: dict[str, object], dependency: object) -> object:
    return public_phase136(
        data_set["result"],
        data_set["workflow"],
        data_set["state_path"],
        data_set["events_path"],
        phase129_function=dependency,
    )


def reject(
    data_set: dict[str, object],
    expected: str,
    *,
    result: object | None = None,
    workflow_value: object | None = None,
    state_path: object | None = None,
    events_path: object | None = None,
    preserve_before: bool = False,
) -> None:
    if not preserve_before:
        set_before(data_set)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(PublicPhase136CompatibilityError) as caught:
        public_phase136(
            data_set["result"] if result is None else result,
            data_set["workflow"] if workflow_value is None else workflow_value,
            data_set["state_path"] if state_path is None else state_path,
            data_set["events_path"] if events_path is None else events_path,
            phase129_function=dependency,
        )
    assert type(caught.value) is PublicPhase136CompatibilityError
    assert caught.value.detail.classification == expected
    assert calls == 0
    if not preserve_before:
        unchanged(data_set)


def reject_after_call(
    data_set: dict[str, object], dependency: object, expected: str
) -> None:
    set_before(data_set)
    calls = 0

    def counted(*args: object) -> object:
        nonlocal calls
        calls += 1
        return dependency(*args)  # type: ignore[operator]

    with pytest.raises(PublicPhase136CompatibilityError) as caught:
        invoke(data_set, counted)
    assert caught.value.detail.classification == expected
    assert calls == 1
    unchanged(data_set)


def expected_decision(data_set: dict[str, object]) -> WorkflowProgressionDecision:
    result = data_set["result"]
    definition = data_set["workflow"]
    final = result.current_step_index == len(definition.steps)
    next_step = None if final else definition.steps[result.current_step_index]
    return WorkflowProgressionDecision(
        "workflow_complete" if final else "prepare_next_step",
        result.workflow_id,
        result.current_step_id,
        result.current_step_index,
        result.current_employee_id,
        None if final else next_step.id,
        None if final else result.current_step_index + 1,
        None if final else next_step.employee,
        "last_step_succeeded" if final else "next_step_available",
    )


def replace_terminal(data_set: dict[str, object], event: RuntimeStepEvent) -> None:
    lines = data_set["events_path"].read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    data_set["events_path"].write_bytes(  # type: ignore[union-attr]
        b"".join(lines[:-1]) + serialize_runtime_step_event_jsonl(event).encode("utf-8")
    )


def replace_state(data_set: dict[str, object], state: WorkflowExecutionState) -> None:
    data_set["state_path"].write_bytes(  # type: ignore[union-attr]
        serialize_workflow_execution_state_json(state).encode("utf-8")
    )


def test_public_signature_default_identity_and_source_audit() -> None:
    signature = inspect.signature(public_phase136)
    parameters = list(signature.parameters.values())
    assert [item.name for item in parameters] == [
        "result",
        "workflow",
        "state_path",
        "events_path",
        "phase129_function",
    ]
    assert all(item.annotation is object for item in parameters[:4])
    assert [item.kind for item in parameters[:4]] == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD
    ] * 4
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[4].default is public_phase129
    source = Path(
        "src/ai_office/engine/"
        "classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert (
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary"
        in source
    )
    assert "phase122" not in source.lower()
    assert (
        "route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary"
        not in source
    )
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


@pytest.mark.parametrize("index", [4, 5], ids=["intermediate", "final"])
def test_valid_persisted_success_delegates_once_in_canonical_order_and_returns_identity(
    tmp_path: Path, index: int
) -> None:
    data_set = data(tmp_path, index=index)
    decision = expected_decision(data_set)
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        return decision

    assert invoke(data_set, dependency) is decision
    assert calls == [
        (
            data_set["result"],
            data_set["workflow"],
            data_set["state_path"],
            data_set["events_path"],
        )
    ]
    unchanged(data_set)


def test_empty_success_terminal_output_is_valid_on_phase136_route(tmp_path: Path) -> None:
    data_set = data(tmp_path, terminal_output="")
    decision = expected_decision(data_set)
    calls = 0

    def dependency(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        return decision

    assert invoke(data_set, dependency) is decision
    assert calls == 1
    unchanged(data_set)


@pytest.mark.parametrize("index", [1, 2, 3])
def test_indices_one_two_three_are_rejected_before_phase129(tmp_path: Path, index: int) -> None:
    reject(data(tmp_path, index=index), "success_contract")


@pytest.mark.parametrize(
    "mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"]
)
def test_history_matrix_is_rejected_before_phase129(tmp_path: Path, mode: str) -> None:
    data_set = data(tmp_path)
    lines = data_set["events_path"].read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    extra = serialize_runtime_step_event_jsonl(
        predecessor_event(workflow().steps[0], 99, provider="other")
    ).encode()
    if mode == "duplicate":
        content = lines[0] + lines[0] + b"".join(lines[1:])
    elif mode == "missing":
        content = b"".join([lines[0], lines[2], lines[3]])
    elif mode == "reordered":
        content = b"".join([lines[1], lines[0], lines[2], lines[3]])
    elif mode == "unrelated":
        unrelated = replace(
            predecessor_event(workflow().steps[0], 1), workflow_id="other"
        )
        content = serialize_runtime_step_event_jsonl(unrelated).encode() + b"".join(lines[1:])
    elif mode == "malformed":
        content = b"{malformed}\n"
    else:
        content = b"".join(lines) + extra
    data_set["events_path"].write_bytes(content)  # type: ignore[union-attr]
    reject(data_set, "terminal_contract")


@pytest.mark.parametrize("provider", ["other", "", 4])
def test_immediate_predecessor_provider_is_exact_openai(tmp_path: Path, provider: object) -> None:
    reject(data(tmp_path, predecessor_providers={3: provider}), "terminal_contract")


@pytest.mark.parametrize("provider", ["", 4])
def test_earlier_predecessor_provider_must_remain_nonempty_exact_string(
    tmp_path: Path, provider: object
) -> None:
    reject(data(tmp_path, predecessor_providers={1: provider}), "terminal_contract")


def test_earlier_nonopenai_predecessor_remains_accepted(tmp_path: Path) -> None:
    data_set = data(tmp_path, predecessor_providers={1: "other", 2: "vendor", 3: "openai"})
    decision = expected_decision(data_set)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return decision

    assert invoke(data_set, dependency) is decision
    assert calls == 1


@pytest.mark.parametrize("request_id", ["", 4])
def test_predecessor_request_id_requires_nonempty_builtin_string(
    tmp_path: Path, request_id: object
) -> None:
    data_set = data(tmp_path)
    lines = data_set["events_path"].read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event(workflow().steps[2], 3, request_id=request_id)
    ).encode()
    data_set["events_path"].write_bytes(  # type: ignore[union-attr]
        b"".join(lines[:2]) + replacement + lines[3]
    )
    reject(data_set, "terminal_contract")


@pytest.mark.parametrize(
    "field,value", [("response_id", ""), ("response_id", 4), ("output_text", None), ("output_text", 4)]
)
def test_predecessor_response_and_output_contract_is_strict(
    tmp_path: Path, field: str, value: object
) -> None:
    data_set = data(tmp_path)
    lines = data_set["events_path"].read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event(workflow().steps[2], 3, **{field: value})
    ).encode()
    data_set["events_path"].write_bytes(  # type: ignore[union-attr]
        b"".join(lines[:2]) + replacement + lines[3]
    )
    reject(data_set, "terminal_contract")


@pytest.mark.parametrize("provider", ["other", "", 4])
def test_classification_terminal_provider_is_exact_openai(tmp_path: Path, provider: object) -> None:
    reject(data(tmp_path, terminal_provider=provider), "terminal_contract")


@pytest.mark.parametrize(
    ("state_field", "event_field"),
    [("current_step_id", "step_id"), ("current_employee_id", "employee_id")],
)
def test_state_and_terminal_event_same_wrong_workflow_linkage_is_rejected(
    tmp_path: Path, state_field: str, event_field: str
) -> None:
    data_set = data(tmp_path)
    exact_state = WorkflowExecutionState(
        "w", "succeeded", "four", 4, "d", ("one", "two", "three", "four"), None
    )
    wrong_value = "wrong"
    replace_state(data_set, replace(exact_state, **{state_field: wrong_value}))
    replace_terminal(
        data_set,
        replace(
            terminal_event(data_set["workflow"].steps[3], 4, "succeeded"),
            **{event_field: wrong_value},
        ),
    )
    reject(data_set, "terminal_contract")


@pytest.mark.parametrize("response_id", ["", 4, None])
def test_classification_terminal_response_id_is_nonempty_exact_string(
    tmp_path: Path, response_id: object
) -> None:
    reject(data(tmp_path, terminal_response_id=response_id), "terminal_contract")


@pytest.mark.parametrize("request_id", ["", 4])
def test_classification_terminal_optional_request_id_is_nonempty_exact_string(
    tmp_path: Path, request_id: object
) -> None:
    reject(data(tmp_path, terminal_request_id=request_id), "terminal_contract")


@pytest.mark.parametrize(
    "field,value", [("failure_category", "invalid_request"), ("message", ""), ("response_id", "response"), ("output_text", "output")]
)
def test_terminal_failure_semantics_are_strict(
    tmp_path: Path, field: str, value: object
) -> None:
    data_set = data(tmp_path, status="failed")
    lines = data_set["events_path"].read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    event = replace(
        terminal_event(workflow().steps[3], 4, "failed"), **{field: value}
    )
    data_set["events_path"].write_bytes(  # type: ignore[union-attr]
        b"".join(lines[:-1]) + serialize_runtime_step_event_jsonl(event).encode()
    )
    reject(data_set, "terminal_contract")


@pytest.mark.parametrize("kind", ["failure", "completion"])
def test_stop_routes_are_identity_preserving_zero_call_stops(tmp_path: Path, kind: str) -> None:
    data_set = data(tmp_path, status="failed") if kind == "failure" else completion_data(tmp_path)
    supplied = data_set["result"]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert invoke(data_set, dependency) is supplied
    assert calls == 0
    unchanged(data_set)


def test_stop_routes_allow_nonopenai_provider_without_stricter_phase135_rule(tmp_path: Path) -> None:
    cases = (
        data(tmp_path / "failure", status="failed", terminal_provider="other"),
        completion_data(tmp_path / "complete", provider="other"),
    )
    for data_set in cases:
        supplied = data_set["result"]
        calls = 0

        def dependency(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert invoke(data_set, dependency) is supplied
        assert calls == 0
        unchanged(data_set)


def test_persisted_failure_stop_allows_phase135_index_one_contract(tmp_path: Path) -> None:
    data_set = data(tmp_path, index=1, status="failed", terminal_provider="other")
    supplied = data_set["result"]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert invoke(data_set, dependency) is supplied
    assert calls == 0
    unchanged(data_set)


def test_workflow_complete_stop_rejects_empty_success_output(tmp_path: Path) -> None:
    reject(completion_data(tmp_path, output=""), "terminal_contract")


@pytest.mark.parametrize("kind", ["completion", "failure"])
def test_stop_subclasses_and_attribute_compatible_substitutes_are_result_type(
    tmp_path: Path, kind: str
) -> None:
    data_set = completion_data(tmp_path) if kind == "completion" else data(tmp_path, status="failed")
    result = data_set["result"]
    child = (
        DecisionChild(
            result.decision,
            result.workflow_id,
            result.current_step_id,
            result.current_step_index,
            result.current_employee_id,
            result.next_step_id,
            result.next_step_index,
            result.next_employee_id,
            result.reason,
        )
        if kind == "completion"
        else OutcomeChild(
            result.outcome,
            result.workflow_id,
            result.current_step_id,
            result.current_step_index,
            result.current_employee_id,
            result.failure_category,
        )
    )
    substitute = SimpleNamespace(**result.__dict__)
    reject(data_set, "result_type", result=child)
    reject(data_set, "result_type", result=substitute)


@pytest.mark.parametrize("kind", ["completion", "failure"])
@pytest.mark.parametrize("index", [True, IntChild(4)])
def test_stop_bool_and_int_subclass_indices_are_zero_call(
    tmp_path: Path, kind: str, index: object
) -> None:
    data_set = completion_data(tmp_path) if kind == "completion" else data(tmp_path, status="failed")
    classification = "completion_contract" if kind == "completion" else "failure_contract"
    reject(
        data_set,
        classification,
        result=replace(data_set["result"], current_step_index=index),
    )


@pytest.mark.parametrize(
    "bad,expected",
    [
        (
            WorkflowProgressionDecision(
            "prepare_next_step", "w", "four", 4, "d", "five", 5, "e", "next_step_available"
            ),
            "completion_contract",
        ),
        (WorkflowExecutionPersistenceResult(Path("state"), Path("events"), 1, 1), "result_type"),
        (RunningStatePersistenceResult(1), "result_type"),
        (
            StepRuntimeExecutionSuccess(
                "w", "four", 4, "d",
                ModelInvocationSuccess("openai", "response", "request", "completed", (), "output"),
            ),
            "result_type",
        ),
        (
            StepRuntimeExecutionFailure(
                "w", "four", 4, "d",
                ModelInvocationFailure("openai", "api_error", "safe", "request", 500, None, None),
            ),
            "result_type",
        ),
        (PreparedStepExecutionStart(SimpleNamespace(), SimpleNamespace()), "result_type"),
    ],
)
def test_direct_unsupported_exact_inputs_are_result_type_zero_call(
    tmp_path: Path, bad: object, expected: str
) -> None:
    reject(data(tmp_path), expected, result=bad)


def test_exact_workflow_subclass_and_compatible_substitute_are_rejected(tmp_path: Path) -> None:
    data_set = data(tmp_path)
    exact = data_set["workflow"]
    child = WorkflowChild.model_validate(exact.model_dump())
    substitute = SimpleNamespace(
        id=exact.id, name=exact.name, description=exact.description, steps=exact.steps
    )
    reject(data_set, "workflow_definition", workflow_value=child)
    reject(data_set, "workflow_definition", workflow_value=substitute)


def test_workflow_step_subclass_and_compatible_substitute_are_rejected(tmp_path: Path) -> None:
    data_set = data(tmp_path)
    exact = data_set["workflow"]
    child = StepChild(
        id=exact.steps[0].id,
        name=exact.steps[0].name,
        employee=exact.steps[0].employee,
        instructions=exact.steps[0].instructions,
    )
    substitute = SimpleNamespace(
        id=exact.steps[0].id,
        name=exact.steps[0].name,
        employee=exact.steps[0].employee,
        instructions=exact.steps[0].instructions,
    )
    for step in (child, substitute):
        candidate = WorkflowDefinition.model_construct(
            id=exact.id,
            name=exact.name,
            description=exact.description,
            steps=[step, *exact.steps[1:]],
        )
        reject(data_set, "workflow_definition", workflow_value=candidate)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(4)),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("failure_category", "api_error"),
    ],
)
def test_success_input_contract_is_exact(tmp_path: Path, field: str, value: object) -> None:
    data_set = data(tmp_path)
    reject(data_set, "success_contract", result=replace(data_set["result"], **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(4)),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("failure_category", None),
    ],
)
def test_failure_input_contract_is_exact(tmp_path: Path, field: str, value: object) -> None:
    data_set = data(tmp_path, status="failed")
    reject(data_set, "failure_contract", result=replace(data_set["result"], **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "prepare_next_step"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(5)),
        ("current_employee_id", "other"),
        ("next_step_id", "wrong"),
        ("next_step_index", 4),
        ("next_employee_id", "wrong"),
        ("reason", "next_step_available"),
    ],
)
def test_completion_input_contract_is_exact(tmp_path: Path, field: str, value: object) -> None:
    data_set = completion_data(tmp_path)
    reject(data_set, "completion_contract", result=replace(data_set["result"], **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "workflow_complete"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(4)),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("next_step_id", "wrong"),
        ("next_step_index", 4),
        ("next_employee_id", "wrong"),
        ("reason", "last_step_succeeded"),
    ],
)
def test_dependency_progression_contract_is_exact(tmp_path: Path, field: str, value: object) -> None:
    data_set = data(tmp_path)
    bad = replace(expected_decision(data_set), **{field: value})
    reject_after_call(data_set, lambda *_: bad, "progression_contract")


@pytest.mark.parametrize(
    "bad",
    [
        OutcomeChild("persisted_success", "w", "four", 4, "d", None),
        SimpleNamespace(
            outcome="persisted_success",
            workflow_id="w",
            current_step_id="four",
            current_step_index=4,
            current_employee_id="d",
            failure_category=None,
        ),
        object(),
    ],
)
def test_dependency_return_must_be_exact_workflow_progression_decision(
    tmp_path: Path, bad: object
) -> None:
    reject_after_call(data(tmp_path), lambda *_: bad, "progression_contract")


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_return_target_mutation_is_compensated_without_retry(
    tmp_path: Path, mutation: str
) -> None:
    data_set = data(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            data_set["state_path"].write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            data_set["events_path"].write_bytes(b"changed-events")  # type: ignore[union-attr]
        return expected_decision(data_set)

    reject_after_call(data_set, dependency, "progression_contract")
    assert calls == 1


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_phase129_error_identity_is_preserved_after_compensation(
    tmp_path: Path, mutation: str
) -> None:
    data_set = data(tmp_path)
    supplied_error = Phase129CompatibilityError("progression_contract")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            data_set["state_path"].write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            data_set["events_path"].write_bytes(b"changed-events")  # type: ignore[union-attr]
        raise supplied_error

    set_before(data_set)
    with pytest.raises(Phase129Error) as caught:
        invoke(data_set, dependency)
    assert caught.value is supplied_error
    assert calls == 1
    unchanged(data_set)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_phase129_error_is_sanitized_and_compensated(
    tmp_path: Path, mutation: str
) -> None:
    data_set = data(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            data_set["state_path"].write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            data_set["events_path"].write_bytes(b"changed-events")  # type: ignore[union-attr]
        raise RuntimeError("secret detail")

    set_before(data_set)
    with pytest.raises(PublicPhase136CompatibilityError) as caught:
        invoke(data_set, dependency)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    unchanged(data_set)


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_malformed_dependency_return_with_mutation_is_compensated_without_retry(
    tmp_path: Path, mutation: str
) -> None:
    data_set = data(tmp_path)

    def dependency(*_: object) -> object:
        if mutation in {"state", "both"}:
            data_set["state_path"].write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            data_set["events_path"].write_bytes(b"changed-events")  # type: ignore[union-attr]
        return object()

    reject_after_call(data_set, dependency, "progression_contract")


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path, failed_target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_set = data(tmp_path)
    state, events = data_set["state_path"], data_set["events_path"]
    original_write = Path.write_bytes
    restore_calls = {"state": 0, "events": 0}
    dependency_calls = 0

    def restore(path: Path, content: bytes) -> int:
        key = "state" if path == state else "events"
        restore_calls[key] += 1
        if failed_target in {key, "both"}:
            raise OSError("restore failure")
        return original_write(path, content)

    monkeypatch.setattr(Path, "write_bytes", restore)

    def dependency(*_: object) -> object:
        nonlocal dependency_calls
        dependency_calls += 1
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return object()

    set_before(data_set)
    with pytest.raises(PublicPhase136CompatibilityError) as caught:
        invoke(data_set, dependency)
    assert caught.value.detail.classification == "dependency_rollback"
    assert restore_calls == {"state": 1, "events": 1}
    assert dependency_calls == 1


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_missing_and_directory_targets_are_zero_call(tmp_path: Path, target: str) -> None:
    data_set = data(tmp_path)
    path = data_set[target]
    path.unlink()  # type: ignore[union-attr]
    reject(
        data_set,
        "state_target" if target == "state_path" else "event_target",
        preserve_before=True,
    )
    path.mkdir()  # type: ignore[union-attr]
    reject(
        data_set,
        "state_target" if target == "state_path" else "event_target",
        preserve_before=True,
    )


@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
@pytest.mark.parametrize("target", ["state", "events"])
def test_target_oserrors_are_safely_classified(
    tmp_path: Path,
    operation: str,
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_set = data(tmp_path)
    selected = data_set["state_path"] if target == "state" else data_set["events_path"]
    original = getattr(Path, operation)

    def raising(path: Path, *args: object, **kwargs: object) -> object:
        if path == selected:
            raise OSError("secret target detail")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, raising)
    reject(
        data_set,
        "state_target" if target == "state" else "event_target",
        preserve_before=True,
    )


def test_target_conflict_path_subclass_and_noncallable_dependency_are_zero_call(
    tmp_path: Path,
) -> None:
    data_set = data(tmp_path)
    reject(data_set, "target_conflict", events_path=data_set["state_path"])
    reject(data_set, "state_target", state_path="state")
    reject(data_set, "event_target", events_path=PathChild(data_set["events_path"]))
    set_before(data_set)
    with pytest.raises(PublicPhase136CompatibilityError) as caught:
        public_phase136(
            data_set["result"],
            data_set["workflow"],
            data_set["state_path"],
            data_set["events_path"],
            phase129_function=object(),
        )
    assert caught.value.detail.classification == "dependency_error"
    unchanged(data_set)


def test_public_error_detail_has_only_safe_classification() -> None:
    detail = ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeReentryContinuationFailureDetail(
        "result_type"
    )
    assert detail.classification == "result_type"
