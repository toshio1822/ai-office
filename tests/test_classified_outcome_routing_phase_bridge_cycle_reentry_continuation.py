"""Focused Phase 87 strict-boundary tests."""

# ruff: noqa: E501

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedOutcomeRoutingPhaseBridgeContinuationError,
    ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation,
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


def workflow(single: bool = False) -> WorkflowDefinition:
    steps = [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}]
    if not single:
        steps.append({"id": "two", "name": "Two", "employee": "b", "instructions": "y"})
    return WorkflowDefinition.model_validate({"id": "w", "name": "W", "description": "D", "steps": steps})


def setup(tmp_path: Path, *, single: bool = False, failed: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = workflow(single)
    index = 1 if not single else len(definition.steps)
    step = definition.steps[index - 1]
    status = "failed" if failed else "succeeded"
    state_model = WorkflowExecutionState(
        "w", status, step.id, index, step.employee,
        () if failed else tuple(item.id for item in definition.steps[:index]),
        "api_error" if failed else None,
    )
    event = RuntimeStepEvent(
        "step_failed" if failed else "step_succeeded", "w", step.id, index, step.employee,
        "running", status, "openai", "api_error" if failed else None,
        None if failed else "response", "request", None if failed else "output",
        "failure" if failed else None,
    )
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(serialize_workflow_execution_state_json(state_model).encode())
    events.write_bytes(serialize_runtime_step_event_jsonl(event).encode())
    result = PersistedExecutionOutcome(
        "persisted_failure" if failed else "persisted_success", "w", step.id, index, step.employee,
        "api_error" if failed else None,
    )
    return result, definition, state, events


def prepare() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available")


def complete(single: bool = False) -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete", "w", "one" if single else "two", 1 if single else 2,
        "a" if single else "b", None, None, None, "last_step_succeeded"
    )


def test_public_signature_and_success_routes_once_with_identity(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    expected = prepare()
    calls: list[tuple[object, ...]] = []

    def phase80(*args: object) -> object:
        calls.append(args)
        return expected

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
        result, definition, state, events, phase80_function=phase80
    )
    assert returned is expected and len(calls) == 1
    assert calls[0] == (result, definition, state, events)
    assert all(calls[0][i] is value for i, value in enumerate((result, definition, state, events)))
    assert (state.read_bytes(), events.read_bytes()) == before


def test_final_success_and_completion_mapping(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path, single=True)
    expected = WorkflowProgressionDecision("workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded")
    assert route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
        result, definition, state, events, phase80_function=lambda *_: expected
    ) is expected


def test_failure_and_completion_are_identity_preserving_zero_call_stops(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path, failed=True)
    calls = 0

    def phase80(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=phase80) is result
    result, definition, state, events = setup(tmp_path / "complete", single=True)
    value = complete(single=True)
    assert route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(value, definition, state, events, phase80_function=phase80) is value
    assert calls == 0


@pytest.mark.parametrize("field,value", [
    ("outcome", "persisted_failure"), ("workflow_id", "other"), ("current_step_id", "other"),
    ("current_step_index", True), ("current_employee_id", "other"), ("failure_category", "api_error"),
])
def test_success_fields_and_substitute_models_rejected_before_dependency(tmp_path: Path, field: str, value: object) -> None:
    result, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
            replace(result, **{field: value}), definition, state, events, phase80_function=lambda *_: pytest.fail("called")
        )
    subclass = OutcomeSubclass("persisted_success", "w", "one", 1, "a", None)
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(subclass, definition, state, events)


@pytest.mark.parametrize("value", [object(), DecisionSubclass("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available")])
def test_malformed_or_subclass_dependency_return_rejected_once_and_compensated(tmp_path: Path, value: object) -> None:
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def phase80(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"mutated")
        return value

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=phase80)
    assert calls == 1 and caught.value.detail.classification == "outcome_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_safe_error_identity_after_compensation_and_unexpected_sanitization(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    safe = ClassifiedOutcomeRoutingPhaseBridgeContinuationError("safe")
    before = (state.read_bytes(), events.read_bytes())

    def safe_phase(*_: object) -> object:
        state.write_bytes(b"changed")
        events.write_bytes(b"changed")
        raise safe

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeContinuationError) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=safe_phase)
    assert caught.value is safe and (state.read_bytes(), events.read_bytes()) == before
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret")

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=unexpected)
    assert calls == 1 and error.value.detail.classification == "dependency_error"


@pytest.mark.parametrize("which", ["state", "events", "both"])
def test_dependency_mutation_restores_both_targets(tmp_path: Path, which: str) -> None:
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())

    def phase80(*_: object) -> object:
        if which in ("state", "both"):
            state.write_bytes(b"changed-state")
        if which in ("events", "both"):
            events.write_bytes(b"changed-events")
        return prepare()

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=phase80)
    assert error.value.detail.classification == "outcome_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_invalid_targets_are_rejected_before_dependency(tmp_path: Path, kind: str) -> None:
    result, definition, state, events = setup(tmp_path)
    target = state if kind == "missing" else events
    target.unlink()
    if kind == "directory":
        target.mkdir()
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("failure_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_and_classifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_target: str) -> None:
    result, definition, state, events = setup(tmp_path)
    original_write = Path.write_bytes
    attempts: list[Path] = []

    def write(path: Path, data: bytes) -> int:
        attempts.append(path)
        if failure_target in ("state", "both") and path == state:
            raise OSError
        if failure_target == "events" and path == events:
            raise OSError
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)

    def phase80(*_: object) -> object:
        original_write(state, b"changed")
        original_write(events, b"changed")
        return prepare()

    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase80_function=phase80)
    assert error.value.detail.classification == "dependency_rollback"
    assert state in attempts and events in attempts


def test_no_second_restore_after_rollback_failure_and_target_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, definition, state, events = setup(tmp_path)
    original_read = Path.read_bytes

    def fail(path: Path) -> bytes:
        if path == state:
            raise OSError
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", fail)
    with pytest.raises(ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as error:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(result, definition, state, events)
    assert error.value.detail.classification == "state_target"


_PHASE155_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_PHASE155_SENTINEL = object()


def _phase155_workflow() -> WorkflowDefinition:
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
                for step_id in _PHASE155_STEP_IDS
            ],
        }
    )


def _phase155_predecessor(
    step_id: str,
    position: int,
    *,
    provider: object = "other",
    request_id: object = _PHASE155_SENTINEL,
    output_text: object = "output",
) -> RuntimeStepEvent:
    resolved_request_id = (
        f"request-{step_id}" if request_id is _PHASE155_SENTINEL else request_id
    )
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


def phase155_setup(
    tmp_path: Path,
    status: str,
    *,
    earlier_empty: tuple[int, ...] = (2,),
    message: str = "safe failure",
):
    """Six-step Phase-155 provenance fixture shared by the Phase 169 tests.

    Terminal step 6, predecessors 1-5 succeeded, step 2 (and any extra
    ``earlier_empty`` positions) with empty ``output_text``, immediate step 5
    with empty ``output_text``, provider ``"openai"`` and ``request_id=None``,
    earlier request IDs exact non-empty built-in strings, and valid terminal
    succeeded/failed variants.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = _phase155_workflow()
    state = WorkflowExecutionState(
        "w",
        status,
        "six",
        6,
        "s",
        tuple(_PHASE155_STEP_IDS)
        if status == "succeeded"
        else tuple(_PHASE155_STEP_IDS[:5]),
        None if status == "succeeded" else "api_error",
    )
    events = [
        _phase155_predecessor(
            step_id,
            position,
            output_text="" if position in earlier_empty else "output",
        )
        for position, step_id in enumerate(_PHASE155_STEP_IDS[:5], 1)
    ]
    events[4] = _phase155_predecessor(
        "five", 5, provider="openai", request_id=None, output_text=""
    )
    if status == "succeeded":
        terminal = RuntimeStepEvent(
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
        result = PersistedExecutionOutcome(
            "persisted_success", "w", "six", 6, "s", None
        )
    else:
        terminal = RuntimeStepEvent(
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
        result = PersistedExecutionOutcome(
            "persisted_failure", "w", "six", 6, "s", "api_error"
        )
    events.append(terminal)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(
        serialize_workflow_execution_state_json(state).encode("utf-8")
    )
    events_path.write_bytes(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events).encode("utf-8")
    )
    return result, definition, state_path, events_path


def _phase155_expected_decision() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        "six",
        6,
        "s",
        None,
        None,
        None,
        "last_step_succeeded",
    )


def _phase155_replace_event(
    events_path: Path, position: int, event: RuntimeStepEvent
) -> None:
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)
    replacement = serialize_runtime_step_event_jsonl(event)
    lines[position - 1] = replacement
    events_path.write_text("".join(lines), encoding="utf-8")


def test_phase155_success_delegates_to_phase80_exactly_once(tmp_path: Path) -> None:
    """Phase-155 persisted success delegates to Phase 80 exactly once with
    canonical four-argument object identity/order, exact returned-object
    identity, and unchanged targets."""
    result, definition, state, events = phase155_setup(tmp_path, "succeeded")
    before = state.read_bytes(), events.read_bytes()
    decision = _phase155_expected_decision()
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        return decision

    returned = route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
        result, definition, state, events, phase80_function=dependency
    )
    assert returned is decision
    assert len(calls) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            calls[0], (result, definition, state, events), strict=True
        )
    )
    assert (state.read_bytes(), events.read_bytes()) == before

    # The fallback must not invent predecessor provider/request-ID policy: an
    # otherwise identical history whose immediate predecessor keeps
    # provider="other" and a non-empty request ID still delegates.
    alt = phase155_setup(tmp_path / "alt", "succeeded")
    _phase155_replace_event(
        alt[3],
        5,
        _phase155_predecessor(
            "five", 5, provider="other", request_id="request-five", output_text=""
        ),
    )
    alt_before = alt[2].read_bytes(), alt[3].read_bytes()
    alt_calls: list[tuple[object, ...]] = []

    def alt_dependency(*args: object) -> WorkflowProgressionDecision:
        alt_calls.append(args)
        return decision

    alt_returned = route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
        alt[0],
        alt[1],
        alt[2],
        alt[3],
        phase80_function=alt_dependency,
    )
    assert alt_returned is decision
    assert len(alt_calls) == 1
    assert (alt[2].read_bytes(), alt[3].read_bytes()) == alt_before

    # Terminal succeeded response_id="" stays rejected.
    pin = phase155_setup(tmp_path / "pin-response", "succeeded")
    empty_response = RuntimeStepEvent(
        "step_succeeded",
        "w",
        "six",
        6,
        "s",
        "running",
        "succeeded",
        "openai",
        None,
        "",
        "request-six",
        "output-six",
        None,
    )
    _phase155_replace_event(pin[3], 6, empty_response)
    pin_before = pin[2].read_bytes(), pin[3].read_bytes()
    pin_calls = {"phase80": 0}

    def pin_dependency(*_: object) -> object:
        pin_calls["phase80"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
            pin[0], pin[1], pin[2], pin[3], phase80_function=pin_dependency
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert pin_calls["phase80"] == 0
    assert (pin[2].read_bytes(), pin[3].read_bytes()) == pin_before

    # Final succeeded terminal output_text="" stays rejected.
    pin2 = phase155_setup(tmp_path / "pin-output", "succeeded")
    empty_output = RuntimeStepEvent(
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
        "",
        None,
    )
    _phase155_replace_event(pin2[3], 6, empty_output)
    pin2_before = pin2[2].read_bytes(), pin2[3].read_bytes()
    pin2_calls = {"phase80": 0}

    def pin2_dependency(*_: object) -> object:
        pin2_calls["phase80"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
            pin2[0], pin2[1], pin2[2], pin2[3], phase80_function=pin2_dependency
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert pin2_calls["phase80"] == 0
    assert (pin2[2].read_bytes(), pin2[3].read_bytes()) == pin2_before

    # workflow_complete stays strict: the completion route must not gain
    # predecessor-empty compatibility through the fallback.
    pin3 = phase155_setup(tmp_path / "pin-complete", "succeeded")
    pin3_before = pin3[2].read_bytes(), pin3[3].read_bytes()
    completion = WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        "six",
        6,
        "s",
        None,
        None,
        None,
        "last_step_succeeded",
    )
    pin3_calls = {"phase80": 0}

    def pin3_dependency(*_: object) -> object:
        pin3_calls["phase80"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
            completion,
            pin3[1],
            pin3[2],
            pin3[3],
            phase80_function=pin3_dependency,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert pin3_calls["phase80"] == 0
    assert (pin3[2].read_bytes(), pin3[3].read_bytes()) == pin3_before


def test_phase155_failure_empty_message_returns_unchanged_zero_calls(
    tmp_path: Path,
) -> None:
    """Phase-155 persisted failure with valid message="" returns the exact
    supplied object with Phase 80 call count zero and unchanged targets."""
    result, definition, state, events = phase155_setup(
        tmp_path, "failed", message=""
    )
    before = state.read_bytes(), events.read_bytes()
    calls = {"phase80": 0}

    def forbidden(*_: object) -> object:
        calls["phase80"] += 1
        raise AssertionError("must not be called")

    returned = route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
        result, definition, state, events, phase80_function=forbidden
    )
    assert returned is result
    assert calls == {"phase80": 0}
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_success_multiple_earlier_empty_delegates_once(tmp_path: Path) -> None:
    """Phase-155 persisted success with multiple earlier empty outputs (steps
    2 and 3) delegates to Phase 80 exactly once with unchanged targets."""
    result, definition, state, events = phase155_setup(
        tmp_path, "succeeded", earlier_empty=(2, 3)
    )
    before = state.read_bytes(), events.read_bytes()
    decision = _phase155_expected_decision()
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        return decision

    returned = route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
        result, definition, state, events, phase80_function=dependency
    )
    assert returned is decision
    assert len(calls) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            calls[0], (result, definition, state, events), strict=True
        )
    )
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_failure_multiple_earlier_empty_returns_unchanged_zero_calls(
    tmp_path: Path,
) -> None:
    """Phase-155 persisted failure with multiple earlier empty outputs returns
    the exact supplied object with zero calls and unchanged targets."""
    result, definition, state, events = phase155_setup(
        tmp_path, "failed", earlier_empty=(2, 3), message=""
    )
    before = state.read_bytes(), events.read_bytes()
    calls = {"phase80": 0}

    def forbidden(*_: object) -> object:
        calls["phase80"] += 1
        raise AssertionError("must not be called")

    returned = route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
        result, definition, state, events, phase80_function=forbidden
    )
    assert returned is result
    assert calls == {"phase80": 0}
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step2_output_none_rejected_before_phase80(tmp_path: Path) -> None:
    """Earlier predecessor step 2 output_text=None rejects with exact
    terminal_contract before Phase 80 with zero calls."""
    result, definition, state, events = phase155_setup(tmp_path, "succeeded")
    _phase155_replace_event(
        events,
        2,
        _phase155_predecessor("two", 2, output_text=None),
    )
    before = state.read_bytes(), events.read_bytes()
    calls = {"phase80": 0}

    def forbidden(*_: object) -> object:
        calls["phase80"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
            result, definition, state, events, phase80_function=forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase80": 0}
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step5_output_non_string_rejected_before_phase80(
    tmp_path: Path,
) -> None:
    """Immediate predecessor step 5 output_text=1 (non-string) rejects with
    exact terminal_contract before Phase 80 with zero calls."""
    result, definition, state, events = phase155_setup(tmp_path, "succeeded")
    _phase155_replace_event(
        events,
        5,
        _phase155_predecessor(
            "five", 5, provider="openai", request_id=None, output_text=1
        ),
    )
    before = state.read_bytes(), events.read_bytes()
    calls = {"phase80": 0}

    def forbidden(*_: object) -> object:
        calls["phase80"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
            result, definition, state, events, phase80_function=forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase80": 0}
    assert (state.read_bytes(), events.read_bytes()) == before
