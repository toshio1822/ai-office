"""Real Phase 143 classify -> real Phase 52 bridge (Phase-155 fallback) ->
real Phase 45 -> 38 -> 37 / 31 -> 25 persisted-outcome routing tail with
Phase-155 provenance unchanged (Issue #349, Phase 171)."""

# ruff: noqa: E501,E701,E702,F401,I001

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    classify_persisted_execution_outcome_reentry,
    decide_persisted_success_progression,
    decide_workflow_progression,
    route_classified_persisted_outcome_bridge_reentry,
    route_classified_persisted_outcome_reentry,
    route_persisted_execution_outcome_reentry,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_SENTINEL = object()


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": step_id,
                    "name": step_id.capitalize(),
                    "employee": step_id[0],
                    "instructions": step_id,
                }
                for step_id in _STEP_IDS
            ],
        }
    )


def predecessor_event(
    step_id: str,
    position: int,
    provider: object = "other",
    request_id: object = _SENTINEL,
    output_text: object = "output",
) -> RuntimeStepEvent:
    resolved_request_id = f"request-{step_id}" if request_id is _SENTINEL else request_id
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        position,
        step_id[0],
        "running",
        "succeeded",
        provider,  # type: ignore[arg-type]
        None,
        f"response-{step_id}",
        resolved_request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
        None,
    )


def terminal_event(status: str, message: str = "safe failure") -> RuntimeStepEvent:
    if status == "succeeded":
        return RuntimeStepEvent(
            "step_succeeded",
            "w",
            "six",
            6,
            "s",
            "running",
            "succeeded",
            "openai",
            None,
            "response-six",
            "request-six",
            "output-six",
            None,
        )
    return RuntimeStepEvent(
        "step_failed",
        "w",
        "six",
        6,
        "s",
        "running",
        "failed",
        "openai",
        "api_error",
        None,
        "request-six",
        None,
        message,
    )


def setup(
    tmp_path: Path,
    status: str,
    *,
    earlier_empty: tuple[int, ...] = (2,),
    message: str = "safe failure",
) -> dict[str, object]:
    supplied_workflow = workflow()
    state = WorkflowExecutionState(
        "w",
        status,
        "six",
        6,
        "s",
        tuple(_STEP_IDS) if status == "succeeded" else tuple(_STEP_IDS[:5]),
        None if status == "succeeded" else "api_error",
    )
    events = [
        predecessor_event(
            step_id,
            position,
            output_text="" if position in earlier_empty else "output",
        )
        for position, step_id in enumerate(_STEP_IDS[:5], 1)
    ]
    events[4] = predecessor_event(
        "five", 5, provider="openai", request_id=None, output_text=""
    )
    events.append(terminal_event(status, message))
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")
    terminal_bytes = serialize_runtime_step_event_jsonl(events[-1]).encode("utf-8")
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(
        state_path,
        events_path,
        len(state_bytes),
        len(terminal_bytes),
    )
    return {
        "result": result,
        "workflow": supplied_workflow,
        "state_path": state_path,
        "events_path": events_path,
    }


def reload_and_assert_provenance(
    values: dict[str, object],
    status: str,
    *,
    earlier_empty: tuple[int, ...] = (2,),
    message: str = "safe failure",
) -> None:
    """Reload persisted state/history via the public storage loader and assert
    the Issue #330 Phase-155 provenance facts before invocation."""
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
        )
    )
    state, events = loaded.state, loaded.events
    for position in earlier_empty:
        assert events[position - 1].step_id == _STEP_IDS[position - 1]
        assert events[position - 1].output_text == ""
    # Issue #341: earlier predecessor request IDs stay exact non-empty built-in
    # strings at Phase 144 / 136.
    for position in (1, 2, 3, 4):
        assert isinstance(events[position - 1].request_id, str)
        assert events[position - 1].request_id
    assert events[4].step_id == "five"
    assert events[4].output_text == ""
    assert events[4].request_id is None
    # Issue #341: the immediate predecessor provider is exactly "openai" where
    # the existing boundary requires it.
    assert events[4].provider == "openai"
    assert state.status == status
    assert state.current_step_id == "six"
    assert state.current_step_index == 6
    assert state.current_employee_id == "s"
    assert state.completed_step_ids == (
        tuple(_STEP_IDS) if status == "succeeded" else tuple(_STEP_IDS[:5])
    )
    assert state.last_failure_category == (
        None if status == "succeeded" else "api_error"
    )
    terminal = events[-1]
    assert terminal.step_id == "six"
    assert terminal.step_index == 6
    assert terminal.employee_id == "s"
    assert terminal.provider == "openai"
    if status == "succeeded":
        assert terminal.event_type == "step_succeeded"
        assert terminal.next_status == "succeeded"
        assert terminal.failure_category is None
        assert terminal.response_id == "response-six"
        assert terminal.request_id == "request-six"
        assert terminal.output_text == "output-six"
        assert terminal.message is None
    else:
        assert terminal.event_type == "step_failed"
        assert terminal.next_status == "failed"
        assert terminal.failure_category == "api_error"
        assert terminal.response_id is None
        assert terminal.request_id == "request-six"
        assert terminal.output_text is None
        assert terminal.message == message


def classify(values: dict[str, object], status: str) -> PersistedExecutionOutcome:
    """Run the real Phase 143 public classification boundary on the persisted
    result and require an exact PersistedExecutionOutcome with the exact Issue
    #341 Phase-155 fields."""
    outcome = route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        values["result"],  # type: ignore[arg-type]
        values["workflow"],  # type: ignore[arg-type]
        values["state_path"],  # type: ignore[arg-type]
        values["events_path"],  # type: ignore[arg-type]
    )
    assert type(outcome) is PersistedExecutionOutcome
    assert outcome.outcome == (
        "persisted_success" if status == "succeeded" else "persisted_failure"
    )
    assert outcome.workflow_id == "w"
    assert outcome.current_step_id == "six"
    assert outcome.current_step_index == 6
    assert outcome.current_employee_id == "s"
    assert outcome.failure_category == (
        None if status == "succeeded" else "api_error"
    )
    return outcome


def run_real_success_tail(
    values: dict[str, object], outcome: PersistedExecutionOutcome
) -> tuple[object, dict[str, int], list[tuple[str, tuple[object, ...]]], list[object]]:
    """Real Phase 52 bridge (Phase-155 fallback) -> real Phase 45 -> 38 ->
    37 / 31 -> 25 with counting wrappers only at the public boundary
    parameters, each immediately delegating to the real public next
    function.  Captures every layer's exact returned object."""
    calls = {
        "phase52": 0,
        "phase45": 0,
        "phase38": 0,
        "phase37": 0,
        "phase31": 0,
        "phase25": 0,
    }
    handoffs: list[tuple[str, tuple[object, ...]]] = []
    decisions: list[object] = []

    def phase25(definition: object, history: object) -> WorkflowProgressionDecision:
        calls["phase25"] += 1
        handoffs.append(("phase25", (definition, history)))
        decision = decide_workflow_progression(  # type: ignore[arg-type]
            definition, history
        )
        decisions.append(decision)
        return decision

    def phase31(
        definition: object, state_path: object, events_path: object
    ) -> WorkflowProgressionDecision:
        calls["phase31"] += 1
        handoffs.append(("phase31", (definition, state_path, events_path)))
        return decide_persisted_success_progression(  # type: ignore[arg-type]
            definition,
            state_path,
            events_path,
            decision_function=phase25,
        )

    def phase37(
        definition: object, state_path: object, events_path: object
    ) -> PersistedExecutionOutcome:
        calls["phase37"] += 1
        handoffs.append(("phase37", (definition, state_path, events_path)))
        return classify_persisted_execution_outcome_reentry(  # type: ignore[arg-type]
            definition, state_path, events_path
        )

    def phase38(
        current: object, definition: object, state_path: object, events_path: object
    ) -> object:
        calls["phase38"] += 1
        handoffs.append(("phase38", (current, definition, state_path, events_path)))
        return route_persisted_execution_outcome_reentry(  # type: ignore[arg-type]
            current,
            definition,
            state_path,
            events_path,
            classification_function=phase37,
            progression_function=phase31,
        )

    def phase45(
        current: object, definition: object, state_path: object, events_path: object
    ) -> object:
        calls["phase45"] += 1
        handoffs.append(("phase45", (current, definition, state_path, events_path)))
        return route_classified_persisted_outcome_reentry(  # type: ignore[arg-type]
            current,
            definition,
            state_path,
            events_path,
            routing_function=phase38,
        )

    calls["phase52"] += 1
    out = route_classified_persisted_outcome_bridge_reentry(
        outcome,
        values["workflow"],  # type: ignore[arg-type]
        values["state_path"],  # type: ignore[arg-type]
        values["events_path"],  # type: ignore[arg-type]
        routing_function=phase45,  # type: ignore[arg-type]
    )
    return out, calls, handoffs, decisions


def run_real_failure_tail(
    values: dict[str, object], outcome: PersistedExecutionOutcome
) -> tuple[object, dict[str, int], list[tuple[str, tuple[object, ...]]]]:
    """Real Phase 52 bridge (Phase-155 fallback) -> real Phase 45 -> 38 with
    counting wrappers only at the public boundary parameters; the failure
    route must stop before any progression call."""
    calls = {
        "phase52": 0,
        "phase45": 0,
        "phase38": 0,
        "phase37": 0,
        "phase31": 0,
        "phase25": 0,
    }
    handoffs: list[tuple[str, tuple[object, ...]]] = []

    def forbidden(*_: object) -> object:
        pytest.fail("progression dependency must not be called")

    def phase37(
        definition: object, state_path: object, events_path: object
    ) -> PersistedExecutionOutcome:
        calls["phase37"] += 1
        handoffs.append(("phase37", (definition, state_path, events_path)))
        return classify_persisted_execution_outcome_reentry(  # type: ignore[arg-type]
            definition, state_path, events_path
        )

    def phase38(
        current: object, definition: object, state_path: object, events_path: object
    ) -> object:
        calls["phase38"] += 1
        handoffs.append(("phase38", (current, definition, state_path, events_path)))
        return route_persisted_execution_outcome_reentry(  # type: ignore[arg-type]
            current,
            definition,
            state_path,
            events_path,
            classification_function=phase37,
            progression_function=forbidden,
        )

    def phase45(
        current: object, definition: object, state_path: object, events_path: object
    ) -> object:
        calls["phase45"] += 1
        handoffs.append(("phase45", (current, definition, state_path, events_path)))
        return route_classified_persisted_outcome_reentry(  # type: ignore[arg-type]
            current,
            definition,
            state_path,
            events_path,
            routing_function=phase38,
        )

    calls["phase52"] += 1
    out = route_classified_persisted_outcome_bridge_reentry(
        outcome,
        values["workflow"],  # type: ignore[arg-type]
        values["state_path"],  # type: ignore[arg-type]
        values["events_path"],  # type: ignore[arg-type]
        routing_function=phase45,  # type: ignore[arg-type]
    )
    return out, calls, handoffs


def assert_success_chain_ok(
    values: dict[str, object],
    outcome: PersistedExecutionOutcome,
    out: object,
    calls: dict[str, int],
    handoffs: list[tuple[str, tuple[object, ...]]],
    decisions: list[object],
) -> None:
    assert calls == {
        "phase52": 1,
        "phase45": 1,
        "phase38": 1,
        "phase37": 1,
        "phase31": 1,
        "phase25": 1,
    }
    # The exact decision object produced by real Phase 25 survives every
    # layer's _validate_return unchanged (object identity preserved).
    assert len(decisions) == 1
    assert out is decisions[0]
    assert type(out) is WorkflowProgressionDecision
    expected = tuple(
        [outcome, values["workflow"], values["state_path"], values["events_path"]]
    )
    assert [name for name, _ in handoffs] == [
        "phase45",
        "phase38",
        "phase37",
        "phase31",
        "phase25",
    ]
    for name, args in handoffs:
        if name in {"phase37", "phase31"}:
            assert all(
                actual is wanted
                for actual, wanted in zip(
                    args,
                    (values["workflow"], values["state_path"], values["events_path"]),
                    strict=True,
                )
            )
        elif name == "phase25":
            assert args[0] is values["workflow"]
        else:
            assert all(
                actual is wanted for actual, wanted in zip(args, expected, strict=True)
            )


def assert_failure_chain_ok(
    values: dict[str, object],
    outcome: PersistedExecutionOutcome,
    out: object,
    calls: dict[str, int],
    handoffs: list[tuple[str, tuple[object, ...]]],
) -> None:
    assert calls == {
        "phase52": 1,
        "phase45": 1,
        "phase38": 1,
        "phase37": 1,
        "phase31": 0,
        "phase25": 0,
    }
    assert out is outcome
    expected = tuple(
        [outcome, values["workflow"], values["state_path"], values["events_path"]]
    )
    assert [name for name, _ in handoffs] == ["phase45", "phase38", "phase37"]
    for name, args in handoffs:
        if name == "phase37":
            assert all(
                actual is wanted
                for actual, wanted in zip(
                    args,
                    (values["workflow"], values["state_path"], values["events_path"]),
                    strict=True,
                )
            )
        else:
            assert all(
                actual is wanted for actual, wanted in zip(args, expected, strict=True)
            )


def test_real_tail_success_propagates_same_decision_unchanged(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "succeeded")
    reload_and_assert_provenance(values, "succeeded")
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    outcome = classify(values, "succeeded")
    out, calls, handoffs, decisions = run_real_success_tail(values, outcome)
    assert_success_chain_ok(values, outcome, out, calls, handoffs, decisions)
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_real_tail_failure_stops_before_progression_with_same_outcome(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "failed")
    reload_and_assert_provenance(values, "failed")
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    outcome = classify(values, "failed")
    out, calls, handoffs = run_real_failure_tail(values, outcome)
    assert_failure_chain_ok(values, outcome, out, calls, handoffs)
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_real_tail_success_multiple_earlier_empty_unchanged(tmp_path: Path) -> None:
    values = setup(tmp_path, "succeeded", earlier_empty=(2, 3))
    reload_and_assert_provenance(values, "succeeded", earlier_empty=(2, 3))
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    outcome = classify(values, "succeeded")
    out, calls, handoffs, decisions = run_real_success_tail(values, outcome)
    assert_success_chain_ok(values, outcome, out, calls, handoffs, decisions)
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_real_tail_failure_multiple_earlier_empty_stops_before_progression(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "failed", earlier_empty=(2, 3))
    reload_and_assert_provenance(values, "failed", earlier_empty=(2, 3))
    before = values["state_path"].read_bytes(), values["events_path"].read_bytes()  # type: ignore[union-attr]
    outcome = classify(values, "failed")
    out, calls, handoffs = run_real_failure_tail(values, outcome)
    assert_failure_chain_ok(values, outcome, out, calls, handoffs)
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_real_tail_step2_output_none_rejected_at_phase52_before_phase45(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "succeeded")
    outcome = classify(values, "succeeded")
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("two", 2, "other", output_text=None)
    )
    events.write_text(lines[0] + replacement + "".join(lines[2:]), encoding="utf-8")  # type: ignore[union-attr]
    before = values["state_path"].read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = {"phase45": 0}

    def fail(*_: object) -> object:
        calls["phase45"] += 1
        pytest.fail("Phase 45 must not be called")

    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            outcome,
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            events,
            routing_function=fail,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls["phase45"] == 0
    assert (values["state_path"].read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


def test_real_tail_step5_output_non_string_rejected_at_phase52_before_phase45(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "succeeded")
    outcome = classify(values, "succeeded")
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("five", 5, provider="openai", request_id=None, output_text=1)
    )
    events.write_text("".join(lines[:4]) + replacement + "".join(lines[5:]), encoding="utf-8")  # type: ignore[union-attr]
    before = values["state_path"].read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = {"phase45": 0}

    def fail(*_: object) -> object:
        calls["phase45"] += 1
        pytest.fail("Phase 45 must not be called")

    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            outcome,
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            events,
            routing_function=fail,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls["phase45"] == 0
    assert (values["state_path"].read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]
