"""Focused behavioral tests for the Phase 144 classified persisted-outcome outer bridge."""

# ruff: noqa: E501,E701,E702,F401,I001

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as facade_module
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as PublicPhase144CompatibilityError,
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationError as OuterError,
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationFailureDetail,
    route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as public_phase144,
)
from ai_office.engine.persisted_success_progression import (
    PersistedSuccessProgressionCompatibilityError as OwnerCompatibilityError,
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


def workflow_six() -> WorkflowDefinition:
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
                {"id": "six", "name": "Six", "employee": "f", "instructions": "six"},
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


def write_terminal_targets(
    tmp_path: Path,
    definition: WorkflowDefinition,
    index: int,
    status: str,
    *,
    terminal_provider: object = "openai",
    terminal_output: object = "output",
    terminal_request_id: object = "request",
    terminal_response_id: object = "response",
    predecessor_providers: dict[int, object] | None = None,
) -> tuple[Path, Path, bytes, bytes]:
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
    return state_path, events_path, state_bytes, event_bytes


def values(
    tmp_path: Path,
    *,
    definition: WorkflowDefinition | None = None,
    index: int = 5,
    status: str = "succeeded",
    terminal_provider: object = "openai",
    terminal_output: object = "output",
    terminal_request_id: object = "request",
    terminal_response_id: object = "response",
    predecessor_providers: dict[int, object] | None = None,
) -> dict[str, object]:
    definition = definition or workflow()
    step = definition.steps[index - 1]
    state_path, events_path, state_bytes, event_bytes = write_terminal_targets(
        tmp_path,
        definition,
        index,
        status,
        terminal_provider=terminal_provider,
        terminal_output=terminal_output,
        terminal_request_id=terminal_request_id,
        terminal_response_id=terminal_response_id,
        predecessor_providers=predecessor_providers,
    )
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


def before(data_set: dict[str, object]) -> tuple[bytes, bytes]:
    return (
        data_set["state_path"].read_bytes(),  # type: ignore[union-attr]
        data_set["events_path"].read_bytes(),  # type: ignore[union-attr]
    )


def set_before(data_set: dict[str, object]) -> None:
    data_set["before_state"], data_set["before_events"] = before(data_set)


def unchanged(data_set: dict[str, object]) -> None:
    assert before(data_set) == (data_set["before_state"], data_set["before_events"])


def call(data_set: dict[str, object]) -> object:
    return public_phase144(
        data_set["result"],
        data_set["workflow"],
        data_set["state_path"],
        data_set["events_path"],
    )


def reject(
    data_set: dict[str, object],
    classification: str,
    *,
    result: object | None = None,
    workflow_value: object | None = None,
    state_path: object | None = None,
    events_path: object | None = None,
    preserve_before: bool = False,
) -> None:
    if not preserve_before:
        set_before(data_set)
    with pytest.raises(PublicPhase144CompatibilityError) as caught:
        public_phase144(
            data_set["result"] if result is None else result,
            data_set["workflow"] if workflow_value is None else workflow_value,
            data_set["state_path"] if state_path is None else state_path,
            data_set["events_path"] if events_path is None else events_path,
        )
    assert type(caught.value) is PublicPhase144CompatibilityError
    assert caught.value.detail.classification == classification
    if not preserve_before:
        unchanged(data_set)


def patch_owner(monkeypatch: pytest.MonkeyPatch, fake: object) -> None:
    monkeypatch.setattr(
        facade_module, "decide_persisted_success_progression", fake
    )


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


def assert_unchanged(data_set: dict[str, object]) -> None:
    unchanged(data_set)


def stop_values(
    tmp_path: Path, kind: str, provider: object = "other"
) -> tuple[dict[str, object], object]:
    status = "failed" if kind == "failure" else "succeeded"
    data_set = values(tmp_path, status=status, terminal_provider=provider)
    result = data_set["result"]
    if kind == "complete":
        data_set["result"] = WorkflowProgressionDecision(
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
    return data_set, data_set["result"]


def replace_predecessor(
    data_set: dict[str, object], position: int, event: RuntimeStepEvent
) -> None:
    lines = data_set["events_path"].read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    data_set["events_path"].write_bytes(  # type: ignore[union-attr]
        b"".join(lines[: position - 1])
        + serialize_runtime_step_event_jsonl(event).encode("utf-8")
        + b"".join(lines[position:])
    )
    set_before(data_set)


def replace_terminal(data_set: dict[str, object], event: RuntimeStepEvent) -> None:
    lines = data_set["events_path"].read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    data_set["events_path"].write_bytes(  # type: ignore[union-attr]
        b"".join(lines[:-1]) + serialize_runtime_step_event_jsonl(event).encode("utf-8")
    )
    set_before(data_set)


def replace_state(data_set: dict[str, object], state: WorkflowExecutionState) -> None:
    data_set["state_path"].write_bytes(  # type: ignore[union-attr]
        serialize_workflow_execution_state_json(state).encode("utf-8")
    )
    set_before(data_set)


def rewrite_state_json(data_set: dict[str, object], **changes: object) -> None:
    state_path = data_set["state_path"]
    payload = json.loads(state_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    payload.update(changes)
    state_path.write_text(  # type: ignore[union-attr]
        json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    set_before(data_set)


def test_valid_nonfinal_persisted_success_returns_exact_prepare_next_step(
    tmp_path: Path,
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    returned = call(data_set)
    assert type(returned) is WorkflowProgressionDecision
    assert returned == expected_decision(data_set)
    assert returned.decision == "prepare_next_step"
    assert returned.workflow_id == "w"
    assert returned.current_step_id == "five"
    assert returned.current_step_index == 5
    assert returned.current_employee_id == "e"
    assert returned.next_step_id == "six"
    assert returned.next_step_index == 6
    assert returned.next_employee_id == "f"
    assert returned.reason == "next_step_available"
    unchanged(data_set)


def test_valid_final_persisted_success_returns_exact_workflow_complete(
    tmp_path: Path,
) -> None:
    data_set = values(tmp_path, definition=workflow(), index=5)
    returned = call(data_set)
    assert type(returned) is WorkflowProgressionDecision
    assert returned == expected_decision(data_set)
    assert returned.decision == "workflow_complete"
    assert returned.current_step_id == "five"
    assert returned.current_step_index == 5
    assert returned.next_step_id is None
    assert returned.next_step_index is None
    assert returned.next_employee_id is None
    assert returned.reason == "last_step_succeeded"
    unchanged(data_set)


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_indices_one_two_three_four_progress_or_stop(
    tmp_path: Path, index: int
) -> None:
    data_set = values(tmp_path, index=index)
    returned = call(data_set)
    assert type(returned) is WorkflowProgressionDecision
    assert returned == expected_decision(data_set)
    unchanged(data_set)

    failed = values(tmp_path / "failure", index=index, status="failed")
    returned_failure = call(failed)
    assert returned_failure is failed["result"]
    assert returned_failure.failure_category == "api_error"  # type: ignore[union-attr]
    unchanged(failed)

    complete = values(tmp_path / "complete", index=index)
    complete_workflow = complete["workflow"]
    complete_workflow.steps = complete_workflow.steps[:index]  # type: ignore[union-attr]
    complete["state_path"].write_bytes(  # type: ignore[union-attr]
        serialize_workflow_execution_state_json(
            WorkflowExecutionState(
                "w",
                "succeeded",
                complete_workflow.steps[-1].id,  # type: ignore[union-attr]
                index,
                complete_workflow.steps[-1].employee,  # type: ignore[union-attr]
                tuple(step.id for step in complete_workflow.steps),  # type: ignore[union-attr]
                None,
            )
        ).encode("utf-8")
    )
    set_before(complete)
    result = complete["result"]
    complete_result = WorkflowProgressionDecision(
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
    returned_complete = call({**complete, "result": complete_result})
    assert returned_complete is complete_result
    unchanged(complete)

    if index == 1:
        one_step = workflow()
        one_step.steps = one_step.steps[:1]
        one_step_success = values(tmp_path / "one-step", definition=one_step, index=1)
        one_step_returned = call(one_step_success)
        assert type(one_step_returned) is WorkflowProgressionDecision
        assert one_step_returned == WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            "one",
            1,
            "a",
            None,
            None,
            None,
            "last_step_succeeded",
        )
        unchanged(one_step_success)


def test_immediate_predecessor_empty_output_text_progresses(tmp_path: Path) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    replace_predecessor(
        data_set,
        4,
        predecessor_event(
            data_set["workflow"].steps[3], 4, provider="openai", output_text=""
        ),
    )
    returned = call(data_set)
    assert type(returned) is WorkflowProgressionDecision
    assert returned == expected_decision(data_set)
    assert_unchanged(data_set)


def test_earlier_empty_output_text_survives_later_succeeded_predecessor(
    tmp_path: Path,
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    replace_predecessor(
        data_set,
        2,
        predecessor_event(
            data_set["workflow"].steps[1], 2, provider="other", output_text=""
        ),
    )
    returned = call(data_set)
    assert returned == expected_decision(data_set)
    assert_unchanged(data_set)


def test_predecessor_nonempty_output_text_remains_accepted(tmp_path: Path) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    returned = call(data_set)
    assert returned == expected_decision(data_set)
    assert_unchanged(data_set)


@pytest.mark.parametrize("output_text", [None, 4, ["output"]])
def test_predecessor_output_text_non_string_is_rejected(
    tmp_path: Path, output_text: object
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    replace_predecessor(
        data_set,
        4,
        predecessor_event(
            data_set["workflow"].steps[3], 4, provider="openai", output_text=output_text
        ),
    )
    reject(data_set, "terminal_contract")


@pytest.mark.parametrize("response_id", [None, ""])
def test_predecessor_empty_output_text_still_requires_response_id(
    tmp_path: Path, response_id: object
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    replace_predecessor(
        data_set,
        4,
        predecessor_event(
            data_set["workflow"].steps[3],
            4,
            provider="openai",
            output_text="",
            response_id=response_id,
        ),
    )
    reject(data_set, "terminal_contract")


@pytest.mark.parametrize("request_id", [None, ""])
def test_predecessor_empty_output_text_still_requires_request_id(
    tmp_path: Path, request_id: object
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    replace_predecessor(
        data_set,
        4,
        predecessor_event(
            data_set["workflow"].steps[3],
            4,
            provider="openai",
            output_text="",
            request_id=request_id,
        ),
    )
    reject(data_set, "terminal_contract")


def test_immediate_predecessor_empty_output_text_still_requires_openai_provider(
    tmp_path: Path,
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    replace_predecessor(
        data_set,
        4,
        predecessor_event(
            data_set["workflow"].steps[3], 4, provider="other", output_text=""
        ),
    )
    reject(data_set, "terminal_contract")


@pytest.mark.parametrize("provider", ["other", "", 4])
def test_immediate_predecessor_provider_contract_is_strict(
    tmp_path: Path, provider: object
) -> None:
    reject(
        values(tmp_path, definition=workflow_six(), index=5, predecessor_providers={4: provider}),
        "terminal_contract",
    )


def test_earlier_nonopenai_predecessor_remains_accepted(tmp_path: Path) -> None:
    data_set = values(
        tmp_path,
        definition=workflow_six(),
        index=5,
        predecessor_providers={1: "other", 2: "vendor", 3: "openai", 4: "openai"},
    )
    returned = call(data_set)
    assert returned == expected_decision(data_set)
    assert_unchanged(data_set)


def test_succeeded_terminal_exact_empty_output_remains_accepted(tmp_path: Path) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5, terminal_output="")
    returned = call(data_set)
    assert returned == expected_decision(data_set)
    assert_unchanged(data_set)


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_type", "step_failed"),
        ("next_status", "failed"),
        ("failure_category", "api_error"),
        ("response_id", None),
        ("output_text", None),
        ("output_text", 4),
        ("message", "wrong"),
    ],
)
def test_invalid_persisted_terminal_event_semantics_are_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    replace_terminal(
        data_set,
        replace(
            terminal_event(
                data_set["workflow"].steps[4], 5, "succeeded"
            ),
            **{field: value},
        ),
    )
    reject(data_set, "terminal_contract")


@pytest.mark.parametrize("provider", ["other", "", 4])
def test_terminal_provider_is_exact_openai_on_success_route(
    tmp_path: Path, provider: object
) -> None:
    reject(values(tmp_path, definition=workflow_six(), index=5, terminal_provider=provider), "terminal_contract")


@pytest.mark.parametrize("request_id", [None, "request", ""])
def test_terminal_request_id_is_optional_or_nonempty_exact_string(
    tmp_path: Path, request_id: object
) -> None:
    if request_id is None or request_id:
        data_set = values(
            tmp_path, definition=workflow_six(), index=5, terminal_request_id=request_id
        )
        returned = call(data_set)
        assert returned == expected_decision(data_set)
        assert_unchanged(data_set)
    else:
        reject(
            values(
                tmp_path, definition=workflow_six(), index=5, terminal_request_id=request_id
            ),
            "terminal_contract",
        )


@pytest.mark.parametrize("response_id", [None, ""])
def test_terminal_response_id_requires_nonempty_exact_string(
    tmp_path: Path, response_id: object
) -> None:
    reject(
        values(tmp_path, definition=workflow_six(), index=5, terminal_response_id=response_id),
        "terminal_contract",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", 3),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("completed_step_ids", ("one", "two", "three", "four", "six")),
        ("last_failure_category", "api_error"),
        ("status", "failed"),
    ],
)
def test_persisted_terminal_state_matrix_is_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    exact_state = WorkflowExecutionState(
        "w", "succeeded", "five", 5, "e", ("one", "two", "three", "four", "five"), None
    )
    replace_state(data_set, replace(exact_state, **{field: value}))
    reject(data_set, "terminal_contract")


@pytest.mark.parametrize(
    "mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"]
)
def test_history_matrix_is_rejected(tmp_path: Path, mode: str) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    lines = data_set["events_path"].read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    extra = serialize_runtime_step_event_jsonl(
        predecessor_event(data_set["workflow"].steps[0], 99, provider="other")
    ).encode()
    if mode == "duplicate":
        content = lines[0] + lines[0] + b"".join(lines[1:])
    elif mode == "missing":
        content = b"".join([lines[0], lines[2], lines[3], lines[4]])
    elif mode == "reordered":
        content = b"".join([lines[1], lines[0], lines[2], lines[3], lines[4]])
    elif mode == "unrelated":
        unrelated = replace(
            predecessor_event(data_set["workflow"].steps[0], 1), workflow_id="other"
        )
        content = serialize_runtime_step_event_jsonl(unrelated).encode() + b"".join(lines[1:])
    elif mode == "malformed":
        content = b"{malformed}\n"
    else:
        content = b"".join(lines) + extra
    data_set["events_path"].write_bytes(content)  # type: ignore[union-attr]
    reject(data_set, "terminal_contract")


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "workflow_complete"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(5)),
        ("current_step_index", True),
        ("current_step_index", 4),
        ("current_employee_id", "other"),
        ("next_step_id", "wrong"),
        ("next_step_index", 4),
        ("next_employee_id", "wrong"),
        ("reason", "last_step_succeeded"),
    ],
)
def test_malformed_owner_decision_is_rejected_as_progression_contract(
    tmp_path: Path, field: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    bad = replace(expected_decision(data_set), **{field: value})
    patch_owner(monkeypatch, lambda *_: bad)
    with pytest.raises(PublicPhase144CompatibilityError) as caught:
        call(data_set)
    assert caught.value.detail.classification == "progression_contract"
    unchanged(data_set)


@pytest.mark.parametrize(
    "bad",
    [
        OutcomeChild("persisted_success", "w", "five", 5, "e", None),
        SimpleNamespace(
            decision="prepare_next_step",
            workflow_id="w",
            current_step_id="five",
            current_step_index=5,
            current_employee_id="e",
            next_step_id="six",
            next_step_index=6,
            next_employee_id="f",
            reason="next_step_available",
        ),
        object(),
    ],
)
def test_owner_return_must_be_exact_workflow_progression_decision(
    tmp_path: Path, bad: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    patch_owner(monkeypatch, lambda *_: bad)
    with pytest.raises(PublicPhase144CompatibilityError) as caught:
        call(data_set)
    assert caught.value.detail.classification == "progression_contract"
    unchanged(data_set)


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_return_target_mutation_is_compensated_without_retry(
    tmp_path: Path, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    calls = 0
    decision = expected_decision(data_set)

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            data_set["state_path"].write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            data_set["events_path"].write_bytes(b"changed-events")  # type: ignore[union-attr]
        return decision

    patch_owner(monkeypatch, fake)
    with pytest.raises(PublicPhase144CompatibilityError) as caught:
        call(data_set)
    assert caught.value.detail.classification == "progression_contract"
    assert calls == 1
    unchanged(data_set)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_owner_error_is_sanitized_after_compensation(
    tmp_path: Path, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            data_set["state_path"].write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            data_set["events_path"].write_bytes(b"changed-events")  # type: ignore[union-attr]
        raise OwnerCompatibilityError("decision_contract")

    patch_owner(monkeypatch, fake)
    with pytest.raises(PublicPhase144CompatibilityError) as caught:
        call(data_set)
    assert caught.value.detail.classification == "dependency_error"
    assert calls == 1
    unchanged(data_set)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_owner_error_is_sanitized_and_compensated(
    tmp_path: Path, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            data_set["state_path"].write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            data_set["events_path"].write_bytes(b"changed-events")  # type: ignore[union-attr]
        raise RuntimeError("secret detail")

    patch_owner(monkeypatch, fake)
    with pytest.raises(PublicPhase144CompatibilityError) as caught:
        call(data_set)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    unchanged(data_set)


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_malformed_owner_return_with_mutation_is_compensated_without_retry(
    tmp_path: Path, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            data_set["state_path"].write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            data_set["events_path"].write_bytes(b"changed-events")  # type: ignore[union-attr]
        return object()

    patch_owner(monkeypatch, fake)
    with pytest.raises(PublicPhase144CompatibilityError) as caught:
        call(data_set)
    assert caught.value.detail.classification == "progression_contract"
    assert calls == 1
    unchanged(data_set)


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path, failed_target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    state, events = data_set["state_path"], data_set["events_path"]
    original_write = Path.write_bytes
    restore_calls = {"state": 0, "events": 0}
    owner_calls = 0

    def restore(path: Path, content: bytes) -> int:
        key = "state" if path == state else "events"
        restore_calls[key] += 1
        if failed_target in {key, "both"}:
            raise OSError("restore failure")
        return original_write(path, content)

    monkeypatch.setattr(Path, "write_bytes", restore)

    def fake(*_: object) -> object:
        nonlocal owner_calls
        owner_calls += 1
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return object()

    patch_owner(monkeypatch, fake)
    with pytest.raises(PublicPhase144CompatibilityError) as caught:
        call(data_set)
    assert caught.value.detail.classification == "dependency_rollback"
    assert restore_calls == {"state": 1, "events": 1}
    assert owner_calls == 1


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_are_identity_preserving_read_only(
    tmp_path: Path, kind: str
) -> None:
    data_set, supplied = stop_values(tmp_path, kind)
    returned = call(data_set)
    assert returned is supplied
    unchanged(data_set)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_allow_non_openai_terminal_provider_without_stricter_rule(
    tmp_path: Path, kind: str
) -> None:
    data_set, supplied = stop_values(tmp_path, kind, provider="other")
    returned = call(data_set)
    assert returned is supplied
    unchanged(data_set)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_allow_empty_predecessor_output_text_unchanged(
    tmp_path: Path, kind: str
) -> None:
    data_set, supplied = stop_values(tmp_path, kind)
    replace_predecessor(
        data_set,
        4,
        predecessor_event(
            data_set["workflow"].steps[3], 4, provider="openai", output_text=""
        ),
    )
    returned = call(data_set)
    assert returned is supplied
    unchanged(data_set)


def test_workflow_complete_stop_empty_terminal_output_is_rejected(tmp_path: Path) -> None:
    data_set, _ = stop_values(tmp_path, "complete")
    replace_terminal(
        data_set,
        replace(
            terminal_event(data_set["workflow"].steps[4], 5, "succeeded", provider="other"),
            output_text="",
        ),
    )
    reject(data_set, "terminal_contract")


def test_persisted_failure_stop_allows_index_one_contract(tmp_path: Path) -> None:
    data_set = values(tmp_path, index=1, status="failed", terminal_provider="other")
    supplied = data_set["result"]
    returned = call(data_set)
    assert returned is supplied
    unchanged(data_set)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_subclasses_and_attribute_compatible_substitutes_are_result_type(
    tmp_path: Path, kind: str
) -> None:
    data_set, result = stop_values(tmp_path, kind)
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
        if kind == "complete"
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


@pytest.mark.parametrize("kind", ["complete", "failure"])
@pytest.mark.parametrize("index", [True, IntChild(5)])
def test_stop_bool_and_int_subclass_indices_are_rejected(
    tmp_path: Path, kind: str, index: object
) -> None:
    data_set, result = stop_values(tmp_path, kind)
    classification = "completion_contract" if kind == "complete" else "failure_contract"
    reject(data_set, classification, result=replace(result, current_step_index=index))


@pytest.mark.parametrize(
    "bad,expected",
    [
        (
            WorkflowProgressionDecision(
                "prepare_next_step", "w", "five", 5, "e", "six", 6, "f", "next_step_available"
            ),
            "completion_contract",
        ),
        (
            WorkflowProgressionDecision(
                "workflow_complete", "w", "five", 5, "e", None, None, None, "next_step_available"
            ),
            "completion_contract",
        ),
        (WorkflowExecutionPersistenceResult(Path("state"), Path("events"), 1, 1), "result_type"),
        (RunningStatePersistenceResult(1), "result_type"),
        (
            StepRuntimeExecutionSuccess(
                "w", "five", 5, "e",
                ModelInvocationSuccess("openai", "response", "request", "completed", (), "output"),
            ),
            "result_type",
        ),
        (
            StepRuntimeExecutionFailure(
                "w", "five", 5, "e",
                ModelInvocationFailure("openai", "api_error", "safe", "request", 500, None, None),
            ),
            "result_type",
        ),
        (PreparedStepExecutionStart(SimpleNamespace(), SimpleNamespace()), "result_type"),
        (SimpleNamespace(outcome="persisted_success"), "result_type"),
    ],
)
def test_direct_unsupported_exact_inputs_are_result_type(
    tmp_path: Path, bad: object, expected: str
) -> None:
    reject(values(tmp_path, definition=workflow_six(), index=5), expected, result=bad)


def test_exact_workflow_subclass_and_compatible_substitute_are_rejected(
    tmp_path: Path,
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    exact = data_set["workflow"]
    child = WorkflowChild.model_validate(exact.model_dump())
    substitute = SimpleNamespace(
        id=exact.id, name=exact.name, description=exact.description, steps=exact.steps
    )
    reject(data_set, "workflow_definition", workflow_value=child)
    reject(data_set, "workflow_definition", workflow_value=substitute)


def test_workflow_step_subclass_and_compatible_substitute_are_rejected(
    tmp_path: Path,
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
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
        ("current_step_index", IntChild(5)),
        ("current_step_index", True),
        ("current_step_index", 4),
        ("current_employee_id", "other"),
        ("failure_category", "api_error"),
    ],
)
def test_success_input_contract_is_exact(tmp_path: Path, field: str, value: object) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    reject(data_set, "success_contract", result=replace(data_set["result"], **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(5)),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("failure_category", None),
    ],
)
def test_failure_input_contract_is_exact(tmp_path: Path, field: str, value: object) -> None:
    data_set = values(tmp_path, status="failed")
    reject(data_set, "failure_contract", result=replace(data_set["result"], **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "prepare_next_step"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(5)),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("next_step_id", "wrong"),
        ("next_step_index", 4),
        ("next_employee_id", "wrong"),
        ("reason", "next_step_available"),
    ],
)
def test_completion_input_contract_is_exact(tmp_path: Path, field: str, value: object) -> None:
    data_set, result = stop_values(tmp_path, "complete")
    reject(data_set, "completion_contract", result=replace(result, **{field: value}))


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_missing_and_directory_targets_are_rejected(
    tmp_path: Path, target: str
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
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
    data_set = values(tmp_path, definition=workflow_six(), index=5)
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


def test_target_conflict_path_subclass_are_rejected(
    tmp_path: Path,
) -> None:
    data_set = values(tmp_path, definition=workflow_six(), index=5)
    reject(data_set, "target_conflict", events_path=data_set["state_path"])
    reject(data_set, "state_target", state_path="state")
    reject(data_set, "event_target", events_path=PathChild(data_set["events_path"]))


def test_public_error_detail_has_only_safe_classification() -> None:
    detail = (
        ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationFailureDetail(
            "result_type"
        )
    )
    assert detail.classification == "result_type"
    assert OuterError is not None
    assert (
        PublicPhase144CompatibilityError
        is ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    )


def phase155_six_values(
    tmp_path: Path, *, status: str = "succeeded"
) -> dict[str, object]:
    """Six-step Phase-155 provenance fixture accepted by the Phase 144 gate."""
    data_set = values(tmp_path, definition=workflow_six(), index=6, status=status)
    steps = data_set["workflow"].steps
    replace_predecessor(
        data_set,
        2,
        predecessor_event(steps[1], 2, provider="other", output_text=""),
    )
    replace_predecessor(
        data_set,
        5,
        predecessor_event(
            steps[4], 5, provider="openai", request_id=None, output_text=""
        ),
    )
    return data_set


def test_phase155_six_step_success_accepts_empty_and_none_provenance(
    tmp_path: Path,
) -> None:
    data_set = phase155_six_values(tmp_path)
    returned = call(data_set)
    assert type(returned) is WorkflowProgressionDecision
    assert returned == expected_decision(data_set)
    assert returned.decision == "workflow_complete"
    assert_unchanged(data_set)
    steps = data_set["workflow"].steps
    # Inline pin: the immediate predecessor request_id="" stays rejected in
    # the exact >=6 Phase-155 domain (empty-string predecessor request ID
    # remains invalid at Phase 144).
    replace_predecessor(
        data_set,
        5,
        predecessor_event(
            steps[4], 5, provider="openai", request_id="", output_text=""
        ),
    )
    reject(data_set, "terminal_contract")
    # Restore the intact Phase-155 provenance (immediate predecessor step 5:
    # provider="openai", request_id=None, output_text="") so the earlier
    # predecessor request_id=None rejection below is proven independently,
    # not as a side effect of the still-invalid immediate predecessor "".
    replace_predecessor(
        data_set,
        5,
        predecessor_event(
            steps[4], 5, provider="openai", request_id=None, output_text=""
        ),
    )
    # Inline pin: an earlier (non-immediate) predecessor request_id=None stays
    # rejected in the exact >=6 Phase-155 domain; only the immediate
    # predecessor may carry request_id=None.
    replace_predecessor(
        data_set,
        2,
        predecessor_event(
            steps[1], 2, provider="other", request_id=None, output_text=""
        ),
    )
    reject(data_set, "terminal_contract")


def test_phase155_six_step_failure_accepts_empty_and_none_provenance_stops(
    tmp_path: Path,
) -> None:
    data_set = phase155_six_values(tmp_path, status="failed")
    supplied = data_set["result"]
    returned = call(data_set)
    assert returned is supplied
    unchanged(data_set)


def test_phase155_six_step_multiple_empty_outputs_success(tmp_path: Path) -> None:
    data_set = phase155_six_values(tmp_path)
    steps = data_set["workflow"].steps
    replace_predecessor(
        data_set,
        3,
        predecessor_event(steps[2], 3, provider="other", output_text=""),
    )
    returned = call(data_set)
    assert returned == expected_decision(data_set)
    assert_unchanged(data_set)
    # Inline pin: the persisted-success terminal empty output_text stays
    # accepted (narrow Phase-144 empty-terminal-output compatibility).
    replace_terminal(
        data_set, terminal_event(steps[5], 6, "succeeded", output_text="")
    )
    returned = call(data_set)
    assert returned == expected_decision(data_set)
    assert_unchanged(data_set)


def test_phase155_six_step_multiple_empty_outputs_failure_stops(
    tmp_path: Path,
) -> None:
    data_set = phase155_six_values(tmp_path, status="failed")
    steps = data_set["workflow"].steps
    replace_predecessor(
        data_set,
        3,
        predecessor_event(steps[2], 3, provider="other", output_text=""),
    )
    supplied = data_set["result"]
    returned = call(data_set)
    assert returned is supplied
    unchanged(data_set)


def test_phase155_six_step_earlier_output_none_is_rejected(
    tmp_path: Path,
) -> None:
    data_set = phase155_six_values(tmp_path)
    steps = data_set["workflow"].steps
    replace_predecessor(
        data_set,
        2,
        predecessor_event(steps[1], 2, provider="other", output_text=None),
    )
    reject(data_set, "terminal_contract")


def test_phase155_six_step_immediate_output_non_string_is_rejected(
    tmp_path: Path,
) -> None:
    data_set = phase155_six_values(tmp_path)
    steps = data_set["workflow"].steps
    replace_predecessor(
        data_set,
        5,
        predecessor_event(
            steps[4], 5, provider="openai", request_id=None, output_text=1
        ),
    )
    reject(data_set, "terminal_contract")


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


def accumulated_none_data_set(
    tmp_path: Path,
    status: str,
    *,
    five_provider: object = "openai",
    six_provider: object = "openai",
    current: int = 7,
    six_request_id: object = None,
) -> dict[str, object]:
    """Terminal step-``current`` targets with accumulated None request ids.

    Positions 1-4 use the default non-openai predecessors; position 5 and
    the immediate predecessor (``current-1``) carry accumulated None request
    ids with the openai provider by default.  For ``current=8`` the
    non-contiguous Issue #380 case 2 is built: step 5 None, step 6 a
    non-empty request id, immediate step 7 None.
    """
    data_set = values(
        tmp_path,
        definition=accumulated_workflow(),
        index=current,
        status=status,
    )
    replace_predecessor(
        data_set,
        5,
        predecessor_event(
            data_set["workflow"].steps[4],  # type: ignore[arg-type]
            5,
            provider=five_provider,
            request_id=None,
        ),
    )
    replace_predecessor(
        data_set,
        6,
        predecessor_event(
            data_set["workflow"].steps[5],  # type: ignore[arg-type]
            6,
            provider=six_provider,
            request_id=None if current == 7 else six_request_id,
        ),
    )
    if current == 8:
        replace_predecessor(
            data_set,
            7,
            predecessor_event(
                data_set["workflow"].steps[6],  # type: ignore[arg-type]
                7,
                provider=six_provider,
                request_id=None,
            ),
        )
    return data_set


def test_accumulated_none_request_id_positions_five_six(tmp_path: Path) -> None:
    data_set = accumulated_none_data_set(tmp_path, "succeeded")
    returned = call(data_set)
    assert returned == expected_decision(data_set)
    unchanged(data_set)


def test_accumulated_none_step8_noncontiguous_six_request_id(tmp_path: Path) -> None:
    """Issue #380 case 2: step8 with step5=None, step6 non-empty, step7=None.

    The non-contiguous accumulated None provenance (step 5 None, step 6 a
    non-empty request id, immediate step 7 None, all openai) progresses
    exactly once; the persisted-failure stop over the same provenance
    rejects the aged None as ``terminal_contract`` (inline subcase).
    """
    data_set = accumulated_none_data_set(
        tmp_path, "succeeded", current=8, six_request_id="req-6"
    )
    returned = call(data_set)
    assert returned == expected_decision(data_set)
    unchanged(data_set)

    failed = accumulated_none_data_set(
        tmp_path / "failed", "failed", current=8, six_request_id="req-6"
    )
    reject(failed, "terminal_contract")


def test_accumulated_none_position_five_non_openai_provider_is_rejected(
    tmp_path: Path,
) -> None:
    data_set = accumulated_none_data_set(tmp_path, "succeeded", five_provider="other")
    reject(data_set, "terminal_contract")


def test_accumulated_none_position_four_remains_rejected(
    tmp_path: Path,
) -> None:
    data_set = values(
        tmp_path,
        definition=accumulated_workflow(),
        index=7,
        status="succeeded",
    )
    replace_predecessor(
        data_set,
        4,
        predecessor_event(
            data_set["workflow"].steps[3],  # type: ignore[arg-type]
            4,
            provider="other",
            request_id=None,
        ),
    )
    reject(data_set, "terminal_contract")


def test_active_failure_opt_in_accepts_bounded_accumulated_failure_provenance(
    tmp_path: Path,
) -> None:
    """Issue #383: the private active-failure opt-in accepts the exact
    Issue #377 C mismatch: terminal failed step-7 with step5=None and
    step6=None (both ``openai``) returns the exact same persisted_failure
    object with bytes unchanged.
    """
    data_set = accumulated_none_data_set(tmp_path, "failed")
    result = data_set["result"]
    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    out = public_phase144(
        result,
        data_set["workflow"],
        data_set["state_path"],
        data_set["events_path"],
        _allow_accumulated_none_request_id_for_active_failure=True,
    )
    assert out is result  # exact same outcome object by identity
    unchanged(data_set)


def test_active_failure_opt_in_narrow_default_stays_strict(tmp_path: Path) -> None:
    """Issue #383: the opt-in is private and narrow; every other route keeps
    its existing strict semantics.
    """
    # 1. same aged persisted_failure with default False -> terminal_contract
    strict = accumulated_none_data_set(tmp_path / "strict", "failed")
    reject(strict, "terminal_contract")

    # 2. predecessor step-4 None remains rejected even with the opt-in
    data_p4 = values(
        tmp_path / "p4",
        definition=accumulated_workflow(),
        index=7,
        status="failed",
    )
    replace_predecessor(
        data_p4,
        4,
        predecessor_event(
            data_p4["workflow"].steps[3],  # type: ignore[arg-type]
            4,
            provider="openai",
            request_id=None,
        ),
    )
    with pytest.raises(PublicPhase144CompatibilityError) as caught_p4:
        public_phase144(
            data_p4["result"],
            data_p4["workflow"],
            data_p4["state_path"],
            data_p4["events_path"],
            _allow_accumulated_none_request_id_for_active_failure=True,
        )
    assert caught_p4.value.detail.classification == "terminal_contract"
    unchanged(data_p4)

    # 3. aged None with a non-OpenAI provider remains rejected even with opt-in
    data_other = accumulated_none_data_set(
        tmp_path / "other", "failed", five_provider="other"
    )
    with pytest.raises(PublicPhase144CompatibilityError) as caught_other:
        public_phase144(
            data_other["result"],
            data_other["workflow"],
            data_other["state_path"],
            data_other["events_path"],
            _allow_accumulated_none_request_id_for_active_failure=True,
        )
    assert caught_other.value.detail.classification == "terminal_contract"
    unchanged(data_other)

    # 4. empty / non-string request IDs remain invalid even with the opt-in
    for bad_request_id in ("", 123):
        data_bad = values(
            tmp_path / f"bad-{type(bad_request_id).__name__}",
            definition=accumulated_workflow(),
            index=7,
            status="failed",
        )
        replace_predecessor(
            data_bad,
            5,
            predecessor_event(
                data_bad["workflow"].steps[4],  # type: ignore[arg-type]
                5,
                provider="openai",
                request_id=bad_request_id,
            ),
        )
        with pytest.raises(PublicPhase144CompatibilityError) as caught_bad:
            public_phase144(
                data_bad["result"],
                data_bad["workflow"],
                data_bad["state_path"],
                data_bad["events_path"],
                _allow_accumulated_none_request_id_for_active_failure=True,
            )
        assert caught_bad.value.detail.classification == "terminal_contract"
        unchanged(data_bad)

    # 5. workflow_complete stop unchanged even with the opt-in supplied
    complete_set, complete = stop_values(tmp_path / "complete", "complete")
    out_complete = public_phase144(
        complete,
        complete_set["workflow"],
        complete_set["state_path"],
        complete_set["events_path"],
        _allow_accumulated_none_request_id_for_active_failure=True,
    )
    assert out_complete is complete
    unchanged(complete_set)

    # 6. persisted_success Issue-#380 accumulated behavior unchanged with opt-in
    success_set = accumulated_none_data_set(tmp_path / "success", "succeeded")
    out_success = public_phase144(
        success_set["result"],
        success_set["workflow"],
        success_set["state_path"],
        success_set["events_path"],
        _allow_accumulated_none_request_id_for_active_failure=True,
    )
    assert out_success == expected_decision(success_set)
    unchanged(success_set)
