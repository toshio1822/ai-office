"""Focused Phase 80 strict-boundary tests."""

# ruff: noqa: E501

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedOutcomeRoutingPhaseBridgeContinuationError,
    ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_outcome_routing_phase_bridge_cycle_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class IntSubclass(int):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({
        "id": "w", "name": "W", "description": "D",
        "steps": [
            {"id": "one", "name": "One", "employee": "a", "instructions": "x"},
            {"id": "two", "name": "Two", "employee": "b", "instructions": "y"},
        ],
    })


def setup(tmp_path: Path, index: int = 1, status: str = "succeeded", single: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = workflow()
    if single:
        definition = WorkflowDefinition.model_validate({
            "id": "w", "name": "W", "description": "D",
            "steps": [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}],
        })
        index = 1
    step = definition.steps[index - 1]
    state_model = WorkflowExecutionState(
        "w", status, step.id, index, step.employee,
        tuple(item.id for item in definition.steps[:index]) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error",
    )
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed", "w", step.id, index, step.employee,
        "running", status, "openai", None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None, "request", "output" if status == "succeeded" else None,
        None if status == "succeeded" else "failure",
    )
    state = tmp_path / "state.json"
    events = tmp_path / "events.jsonl"
    state.write_bytes(serialize_workflow_execution_state_json(state_model).encode())
    events.write_bytes(serialize_runtime_step_event_jsonl(event).encode())
    result = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure", "w", step.id, index,
        step.employee, None if status == "succeeded" else "api_error",
    )
    return result, definition, state, events


def completion(index: int = 2, step_id: str = "two", employee: str = "b") -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "w", step_id, index, employee, None, None, None, "last_step_succeeded")


def prepare() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available")


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "w", "one", 1, "a", "api_error")


def rewrite_json(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n")


def safe_error() -> ClassifiedOutcomeRoutingPhaseBridgeContinuationError:
    return ClassifiedOutcomeRoutingPhaseBridgeContinuationError("safe")


_SIX = ("one", "two", "three", "four", "five", "six")
_SENTINEL = object()


def six_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({
        "id": "w", "name": "W", "description": "D",
        "steps": [
            {"id": step_id, "name": step_id.capitalize(), "employee": step_id[0], "instructions": step_id}
            for step_id in _SIX
        ],
    })


def six_predecessor(step_id: str, position: int, provider: object = "other", request_id: object = _SENTINEL, output_text: object = "output") -> RuntimeStepEvent:
    resolved_request_id = f"request-{step_id}" if request_id is _SENTINEL else request_id
    return RuntimeStepEvent(
        "step_succeeded", "w", step_id, position, step_id[0],
        "running", "succeeded", provider, None, f"response-{step_id}",
        resolved_request_id, output_text, None,
    )


def six_terminal(status: str) -> RuntimeStepEvent:
    if status == "succeeded":
        return RuntimeStepEvent(
            "step_succeeded", "w", "six", 6, "s", "running", "succeeded", "openai", None,
            "response-six", "request-six", "output-six", None,
        )
    return RuntimeStepEvent(
        "step_failed", "w", "six", 6, "s", "running", "failed", "openai", "api_error",
        None, "request-six", None, "safe failure",
    )


def setup_six(tmp_path: Path, status: str, *, earlier_empty: tuple[int, ...] = (2,)) -> tuple[PersistedExecutionOutcome, WorkflowDefinition, Path, Path]:
    definition = six_workflow()
    state_model = WorkflowExecutionState(
        "w", status, "six", 6, "s",
        tuple(_SIX) if status == "succeeded" else tuple(_SIX[:5]),
        None if status == "succeeded" else "api_error",
    )
    events = [
        six_predecessor(step_id, position, output_text="" if position in earlier_empty else "output")
        for position, step_id in enumerate(_SIX[:5], 1)
    ]
    events[4] = six_predecessor("five", 5, provider="openai", request_id=None, output_text="")
    events.append(six_terminal(status))
    state = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    state.write_bytes(serialize_workflow_execution_state_json(state_model).encode())
    events_path.write_bytes("".join(serialize_runtime_step_event_jsonl(event) for event in events).encode())
    result = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure", "w", "six", 6, "s",
        None if status == "succeeded" else "api_error",
    )
    return result, definition, state, events_path


@pytest.mark.parametrize("final", [False, True])
def test_valid_success_routes_delegate_once_with_exact_identity_and_return_object(tmp_path: Path, final: bool) -> None:
    result, definition, state, events = setup(tmp_path, 1, single=final)
    expected = completion(index=1, step_id="one", employee="a") if final else prepare()
    received: list[object] = []

    def phase73(result_arg: object, workflow_arg: object, state_arg: object, events_arg: object) -> object:
        received.extend((result_arg, workflow_arg, state_arg, events_arg))
        return expected

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73)
    assert returned is expected
    assert received[0] is result and received[1] is definition and received[2] is state and received[3] is events
    assert (state.read_bytes(), events.read_bytes()) == before


def test_failure_and_completion_are_identity_preserving_zero_call_stops(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path, status="failed")
    calls = 0

    def phase73(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73) is result
    complete_result, complete_definition, complete_state, complete_events = setup(tmp_path / "complete", single=True)
    complete_value = completion(index=1, step_id="one", employee="a")
    assert route_classified_outcome_routing_phase_bridge_cycle_continuation(complete_value, complete_definition, complete_state, complete_events, phase73_function=phase73) is complete_value
    assert calls == 0


@pytest.mark.parametrize("bad", [object(), DecisionSubclass("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available"), SimpleNamespace(decision="prepare_next_step")])
def test_malformed_dependency_return_subclass_and_substitute_are_rejected_once(tmp_path: Path, bad: object) -> None:
    result, definition, state, events = setup(tmp_path)
    calls = 0

    def phase73(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73)
    assert calls == 1 and caught.value.detail.classification == "progression_contract"


@pytest.mark.parametrize("field", ["outcome", "workflow_id", "current_step_id", "current_step_index", "current_employee_id", "failure_category"])
def test_success_outcome_each_field_and_exact_types_are_rejected(tmp_path: Path, field: str) -> None:
    result, definition, state, events = setup(tmp_path)
    values = {"outcome": "persisted_failure", "workflow_id": "other", "current_step_id": "other", "current_step_index": True, "current_employee_id": "other", "failure_category": "api_error"}
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(replace(result, **{field: values[field]}), definition, state, events, phase73_function=lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification in {"success_contract", "failure_contract", "outcome_contract"}


def test_outcome_subclass_and_invalid_direct_progression_are_rejected(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    subclass = OutcomeSubclass("persisted_success", "w", "one", 1, "a", None)
    for value in (subclass, prepare()):
        with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError):
            route_classified_outcome_routing_phase_bridge_cycle_continuation(value, definition, state, events)


@pytest.mark.parametrize("decision", ["unsupported"])
def test_dependency_decision_values_and_every_prepare_field_are_checked(tmp_path: Path, decision: str) -> None:
    result, definition, state, events = setup(tmp_path)
    valid = prepare()
    bad = replace(valid, decision=decision)
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=lambda *_: bad)
    assert caught.value.detail.classification == "progression_contract"
    for field, value in {"workflow_id": "other", "current_step_id": "other", "current_step_index": True, "current_employee_id": "other", "next_step_id": "other", "next_step_index": IntSubclass(2), "next_employee_id": "other", "reason": "other"}.items():
        with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
            route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=lambda *_: replace(valid, **{field: value}))
        assert caught.value.detail.classification == "progression_contract"


def test_final_completion_return_fields_are_checked(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path, single=True)
    valid = completion(index=1, step_id="one", employee="a")
    for field, value in {"decision": "prepare_next_step", "workflow_id": "other", "current_step_id": "other", "current_step_index": True, "current_employee_id": "other", "next_step_id": "two", "next_step_index": 2, "next_employee_id": "b", "reason": "other"}.items():
        with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
            route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=lambda *_: replace(valid, **{field: value}))
        assert caught.value.detail.classification == "progression_contract"


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_terminal_state_history_event_and_failure_category_mismatches_are_rejected(tmp_path: Path, status: str) -> None:
    result, definition, state, events = setup(tmp_path, status=status)
    state_fields = {"workflow_id": "other", "status": "failed" if status == "succeeded" else "succeeded", "current_step_id": "other", "current_step_index": 2, "current_employee_id": "other", "completed_step_ids": [] if status == "succeeded" else ["one"], "last_failure_category": "timeout" if status == "succeeded" else None}
    for field, value in state_fields.items():
        rewrite_json(state, field, value)
        with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError):
            route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=lambda *_: object())
        result, definition, state, events = setup(tmp_path / field, status=status)
    event_fields = {"event_type": "step_failed" if status == "succeeded" else "step_succeeded", "workflow_id": "other", "step_id": "other", "step_index": 2, "employee_id": "other", "previous_status": "ready", "next_status": "failed" if status == "succeeded" else "succeeded", "failure_category": "timeout" if status == "succeeded" else None}
    for field, value in event_fields.items():
        rewrite_json(events, field, value)
        with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError):
            route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=lambda *_: object())
        result, definition, state, events = setup(tmp_path / (field + "event"), status=status)


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_non_regular_targets_are_rejected(tmp_path: Path, kind: str) -> None:
    result, definition, state, events = setup(tmp_path)
    target = state if kind == "missing" else events
    if kind == "missing":
        target.unlink()
    else:
        target.unlink()
        target.mkdir()
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events)
    assert caught.value.detail.classification == ("state_target" if target == state else "event_target")


@pytest.mark.parametrize("which", ["state", "events"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_target_oserrors_are_classified_separately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, operation: str) -> None:
    result, definition, state, events = setup(tmp_path)
    target = state if which == "state" else events
    original = getattr(Path, operation)

    def fail(path: Path):
        if path == target:
            raise OSError("unreadable")
        return original(path)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events)
    assert caught.value.detail.classification == ("state_target" if which == "state" else "event_target")


@pytest.mark.parametrize("mutation", ["none", "state", "events", "both"])
def test_safe_error_identity_and_compensation_for_each_mutation(tmp_path: Path, mutation: str) -> None:
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    safe = safe_error()

    def phase73(*_: object) -> object:
        if mutation in ("state", "both"):
            state.write_bytes(b"changed-state")
        if mutation in ("events", "both"):
            events.write_bytes(b"changed-events")
        raise safe

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeContinuationError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73)
    assert caught.value is safe and (state.read_bytes(), events.read_bytes()) == before


def test_unexpected_error_is_sanitized_and_not_retried(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    calls = 0

    def phase73(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret")

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73)
    assert calls == 1 and caught.value.detail.classification == "dependency_error"


@pytest.mark.parametrize("failure_target", ["state", "events", "both"])
def test_rollback_failures_attempt_both_targets_and_do_not_restore_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_target: str) -> None:
    result, definition, state, events = setup(tmp_path)
    original_write = Path.write_bytes
    attempts: list[Path] = []

    def write(path: Path, data: bytes) -> int:
        attempts.append(path)
        if failure_target in ("state", "both") and path == state:
            raise OSError("state rollback")
        if failure_target in ("events", "both") and path == events:
            raise OSError("event rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)

    def phase73(*_: object) -> object:
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return object()

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73)
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempts == [state, events]


@pytest.mark.parametrize("bad", [object(), OutcomeSubclass("persisted_success", "w", "one", 1, "a", None), SimpleNamespace(outcome="persisted_success")])
def test_malformed_return_call_count_one_no_retry_and_compensation(tmp_path: Path, bad: object) -> None:
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def phase73(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed")
        return bad

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError):
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_phase155_success_delegates_once_with_identity_and_exact_return(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, definition, state, events = setup_six(tmp_path, "succeeded")
    decision = completion(index=6, step_id="six", employee="s")
    received: list[object] = []

    def phase73(result_arg: object, workflow_arg: object, state_arg: object, events_arg: object) -> object:
        received.extend((result_arg, workflow_arg, state_arg, events_arg))
        return decision

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73)
    assert returned is decision
    assert received[0] is result and received[1] is definition and received[2] is state and received[3] is events
    assert (state.read_bytes(), events.read_bytes()) == before

    # --- inline pins below (no extra collected tests) ---
    # 1. The fallback does not invent a provider/request-ID gate: an
    #    immediate predecessor with provider="other", request_id=
    #    "request-five", and output_text="" still delegates exactly once.
    lines = events.read_text().splitlines(keepends=True)
    predecessor_line = json.loads(lines[4])
    predecessor_line.update(
        provider="other", request_id="request-five", output_text=""
    )
    events.write_text(
        "".join(lines[:4])
        + json.dumps(predecessor_line, separators=(",", ":"))
        + "\n"
        + "".join(lines[5:])
    )
    alt_before = (state.read_bytes(), events.read_bytes())
    alt_calls = {"count": 0}

    def alt_phase73(*args: object) -> object:
        alt_calls["count"] += 1
        assert len(args) == 4
        assert args[0] is result and args[1] is definition
        assert args[2] is state and args[3] is events
        return decision

    alt_returned = route_classified_outcome_routing_phase_bridge_cycle_continuation(
        result, definition, state, events, phase73_function=alt_phase73
    )
    assert alt_returned is decision
    assert alt_calls["count"] == 1
    assert (state.read_bytes(), events.read_bytes()) == alt_before
    events.write_bytes(before[1])

    # 2. WorkflowProgressionDecision(workflow_complete) never enters the
    #    Phase-155 fallback: the same empty-predecessor history stays strict.
    complete_value = completion(index=6, step_id="six", employee="s")
    dep_calls = {"count": 0}

    def forbidden(*_: object) -> object:
        dep_calls["count"] += 1
        raise AssertionError("progression dependency must not be called")

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(complete_value, definition, state, events, phase73_function=forbidden)
    assert caught.value.detail.classification == "terminal_contract"
    assert dep_calls["count"] == 0
    assert (state.read_bytes(), events.read_bytes()) == before

    # 3. Terminal succeeded response_id="" stays rejected: the fallback does
    #    not weaken the terminal-success contract.
    lines = events.read_text().splitlines(keepends=True)
    terminal_line = json.loads(lines[-1])
    terminal_line["response_id"] = ""
    events.write_text("".join(lines[:-1]) + json.dumps(terminal_line, separators=(",", ":")) + "\n")
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=forbidden)
    assert caught.value.detail.classification == "terminal_contract"
    assert dep_calls["count"] == 0
    events.write_bytes(before[1])

    # 4. Final terminal succeeded output_text="" stays rejected.
    lines = events.read_text().splitlines(keepends=True)
    terminal_line = json.loads(lines[-1])
    terminal_line["output_text"] = ""
    events.write_text("".join(lines[:-1]) + json.dumps(terminal_line, separators=(",", ":")) + "\n")
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=forbidden)
    assert caught.value.detail.classification == "terminal_contract"
    assert dep_calls["count"] == 0
    events.write_bytes(before[1])

    # 5. A transient OSError from the strict-storage state read surfaces as
    #    exact terminal_contract: the initial capture read succeeds, the
    #    failing strict read is not retried through the fallback, dependency
    #    is not called, and both targets remain unchanged.
    original_read_bytes = Path.read_bytes
    read_calls: list[Path] = []

    def failing_strict_read(path: Path) -> bytes:
        read_calls.append(path)
        if path == state and read_calls.count(state) == 2:
            raise OSError("transient strict-path read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", failing_strict_read)
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=forbidden)
    assert caught.value.detail.classification == "terminal_contract"
    assert read_calls.count(state) == 2  # capture + one failing strict read
    assert read_calls.count(events) == 1  # no fallback retry read
    assert dep_calls["count"] == 0
    assert (state.read_bytes(), events.read_bytes()) == before  # fresh read succeeds


def test_phase155_failure_empty_message_is_unchanged_zero_call_stop(tmp_path: Path) -> None:
    result, definition, state, events = setup_six(tmp_path, "failed")
    events.write_text(events.read_text().replace('"safe failure"', '""'))
    calls = 0

    def phase73(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    before = (state.read_bytes(), events.read_bytes())
    assert route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73) is result
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before


def test_phase155_multiple_earlier_empty_success_delegates_once(tmp_path: Path) -> None:
    result, definition, state, events = setup_six(tmp_path, "succeeded", earlier_empty=(2, 3))
    decision = completion(index=6, step_id="six", employee="s")
    received: list[object] = []

    def phase73(result_arg: object, workflow_arg: object, state_arg: object, events_arg: object) -> object:
        received.extend((result_arg, workflow_arg, state_arg, events_arg))
        return decision

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73)
    assert returned is decision
    assert received[0] is result and received[1] is definition and received[2] is state and received[3] is events
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_multiple_earlier_empty_failure_is_unchanged_zero_call_stop(tmp_path: Path) -> None:
    result, definition, state, events = setup_six(tmp_path, "failed", earlier_empty=(2, 3))
    events.write_text(events.read_text().replace('"safe failure"', '""'))
    calls = 0

    def phase73(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    before = (state.read_bytes(), events.read_bytes())
    assert route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73) is result
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step2_output_none_is_rejected_before_dependency(tmp_path: Path) -> None:
    result, definition, state, events = setup_six(tmp_path, "succeeded")
    lines = events.read_text().splitlines(keepends=True)
    replacement = serialize_runtime_step_event_jsonl(six_predecessor("two", 2, output_text=None))
    events.write_text(lines[0] + replacement + "".join(lines[2:]))
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def phase73(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73)
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step5_output_non_string_is_rejected_before_dependency(tmp_path: Path) -> None:
    result, definition, state, events = setup_six(tmp_path, "succeeded")
    lines = events.read_text().splitlines(keepends=True)
    replacement = serialize_runtime_step_event_jsonl(six_predecessor("five", 5, provider="openai", request_id=None, output_text=1))
    events.write_text("".join(lines[:4]) + replacement + "".join(lines[5:]))
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def phase73(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_continuation(result, definition, state, events, phase73_function=phase73)
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before
