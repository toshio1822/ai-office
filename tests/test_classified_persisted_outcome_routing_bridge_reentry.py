"""Focused Phase 52 bridge tests using injected Phase 45 fakes only."""

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_persisted_outcome_bridge_reentry,
)
from ai_office.engine.classified_persisted_outcome_routing_reentry import (
    ClassifiedPersistedOutcomeRoutingCompatibilityError,
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


class WorkflowSubclass(WorkflowDefinition):
    pass


def workflow(two: bool = False) -> WorkflowDefinition:
    steps = [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}]
    if two:
        steps.append({"id": "two", "name": "Two", "employee": "b", "instructions": "y"})
    return WorkflowDefinition.model_validate(
        {"id": "w", "name": "W", "description": "D", "steps": steps}
    )


def setup(
    tmp_path: Path, *, two: bool = False, status: str = "succeeded", index: int = 1
) -> tuple[Path, Path, PersistedExecutionOutcome, WorkflowDefinition]:
    definition = workflow(two)
    step = definition.steps[index - 1]
    completed = (
        tuple(item.id for item in definition.steps[:index])
        if status == "succeeded"
        else tuple(item.id for item in definition.steps[: index - 1])
    )
    state = WorkflowExecutionState(
        "w",
        status,
        step.id,
        index,
        step.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "w",
        step.id,
        index,
        step.employee,
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "out" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_bytes(serialize_runtime_step_event_jsonl(event).encode())
    outcome = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        step.id,
        index,
        step.employee,
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    return state_path, events_path, outcome, definition


@pytest.mark.parametrize(
    "two,index,decision",
    [(False, 1, "workflow_complete"), (True, 1, "prepare_next_step")],
)
def test_success_delegates_exact_arguments_and_returns_same_decision(
    tmp_path: Path, two: bool, index: int, decision: str
) -> None:
    state, events, outcome, definition = setup(tmp_path, two=two, index=index)
    expected = WorkflowProgressionDecision(
        decision,
        "w",
        outcome.current_step_id,
        index,
        outcome.current_employee_id,
        None if decision == "workflow_complete" else "two",
        None if decision == "workflow_complete" else 2,
        None if decision == "workflow_complete" else "b",
        "last_step_succeeded"
        if decision == "workflow_complete"
        else "next_step_available",
    )  # type: ignore[arg-type]
    calls = 0

    def phase45(*args: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        assert (
            len(args) == 4
            and args[0] is outcome
            and args[1] is definition
            and args[2] is state
            and args[3] is events
        )
        return expected

    assert (
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=phase45
        )
        is expected
    )
    assert calls == 1


def test_failure_delegates_once_and_returns_same_supplied_object(
    tmp_path: Path,
) -> None:
    state, events, outcome, definition = setup(tmp_path, status="failed")
    calls = 0

    def phase45(*args: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        assert (
            args[0] is outcome
            and args[1] is definition
            and args[2] is state
            and args[3] is events
        )
        return outcome

    assert (
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=phase45
        )
        is outcome
    )
    assert calls == 1


def test_completion_stops_unchanged(tmp_path: Path) -> None:
    state, events, outcome, definition = setup(tmp_path)
    decision = WorkflowProgressionDecision(
        "workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded"
    )
    before = state.read_bytes(), events.read_bytes()
    assert (
        route_classified_persisted_outcome_bridge_reentry(
            decision,
            definition,
            state,
            events,
            routing_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
        is decision
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "result",
    [
        object(),
        PersistedExecutionOutcome("persisted_success", "bad", "one", 1, "a", None),
    ],
)
def test_prevalidation_rejects_without_dependency(
    tmp_path: Path, result: object
) -> None:
    state, events, _, definition = setup(tmp_path)
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            result,
            definition,
            state,
            events,
            routing_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )


@pytest.mark.parametrize("operation", ["replace", "delete", "append"])
def test_mutating_or_malformed_dependency_is_compensated(
    tmp_path: Path, operation: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def phase45(*_: object) -> PersistedExecutionOutcome:
        if operation == "delete":
            events.unlink()
        elif operation == "append":
            events.write_bytes(events.read_bytes() + b"x")
        else:
            events.write_bytes(b"changed")
        return outcome

    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=phase45
        )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "result",
    [
        OutcomeSubclass("persisted_success", "w", "one", 1, "a", None),
        DecisionSubclass(
            "workflow_complete",
            "w",
            "one",
            1,
            "a",
            None,
            None,
            None,
            "last_step_succeeded",
        ),
        object(),
    ],
)
def test_exact_types_and_substitutes_reject_without_calls(
    tmp_path: Path, result: object
) -> None:
    state, events, _, definition = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            result, definition, state, events, routing_function=dependency
        )
    assert calls == 0


@pytest.mark.parametrize("bad", ["workflow", "state", "events", "same", "function"])
def test_workflow_target_and_dependency_prevalidation(tmp_path: Path, bad: str) -> None:
    state, events, outcome, definition = setup(tmp_path)
    args: list[object] = [outcome, definition, state, events]
    if bad == "workflow":
        args[1] = WorkflowSubclass(**definition.__dict__)
    if bad == "state":
        args[2] = "bad"
    if bad == "events":
        args[3] = "bad"
    if bad == "same":
        args[3] = state
    function: object = object() if bad == "function" else lambda *_: outcome
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            *args, routing_function=function
        )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "outcome",
    [
        PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", "api_error"),
        PersistedExecutionOutcome("persisted_failure", "w", "one", 1, "a", None),
        PersistedExecutionOutcome("persisted_failure", "w", "bad", 1, "a", "api_error"),
    ],
)
def test_malformed_success_and_failure_reject_before_dependency(
    tmp_path: Path, outcome: PersistedExecutionOutcome
) -> None:
    state, events, _, definition = setup(tmp_path)
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome,
            definition,
            state,
            events,
            routing_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )


@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("kind", ["normal", "safe", "unexpected"])
def test_dependency_mutation_error_matrix(
    tmp_path: Path, operation: str, target: str, kind: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0
    safe = ClassifiedPersistedOutcomeRoutingCompatibilityError("result_type")

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"x")
        else:
            path.write_bytes(b"changed")

    def dependency(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            mutate(state)
        if target in {"events", "both"}:
            mutate(events)
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("provider response output")
        return outcome

    expected = (
        ClassifiedPersistedOutcomeRoutingCompatibilityError
        if kind == "safe"
        else ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=dependency
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification in {
            "routing_contract",
            "dependency_error",
        }
        assert "provider" not in str(caught.value) and "output" not in str(caught.value)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["normal", "safe", "unexpected"])
@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failure_overrides_dependency_and_attempts_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, failed: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    originals, original_write, attempted, calls = (
        {state: state.read_bytes(), events: events.read_bytes()},
        Path.write_bytes,
        [],
        0,
    )
    safe = ClassifiedPersistedOutcomeRoutingCompatibilityError("result_type")

    def write(path: Path, contents: bytes) -> int:
        if contents == originals.get(path):
            attempted.append(path)
            if (
                failed == "both"
                or (failed == "state" and path == state)
                or (failed == "events" and path == events)
            ):
                raise OSError("provider output")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", write)

    def dependency(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        original_write(state, b"x")
        original_write(events, b"x")
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("provider output")
        return outcome

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=dependency
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1 and state in attempted and events in attempted
    assert "provider" not in str(caught.value) and "output" not in str(caught.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"decision": "workflow_complete"},
        {"workflow_id": "bad"},
        {"current_step_id": "bad"},
        {"current_step_index": 2},
        {"current_employee_id": "bad"},
        {"next_step_id": "bad"},
        {"next_step_index": 3},
        {"next_employee_id": "bad"},
        {"reason": "bad"},
    ],
)
def test_prepare_next_step_return_rejects_every_wrong_field(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events, outcome, definition = setup(tmp_path, two=True)
    values = dict(
        decision="prepare_next_step",
        workflow_id="w",
        current_step_id="one",
        current_step_index=1,
        current_employee_id="a",
        next_step_id="two",
        next_step_index=2,
        next_employee_id="b",
        reason="next_step_available",
    )
    values.update(changes)
    returned = WorkflowProgressionDecision(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=lambda *_: returned
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"decision": "prepare_next_step"},
        {"workflow_id": "bad"},
        {"current_step_id": "bad"},
        {"current_step_index": 2},
        {"current_employee_id": "bad"},
        {"next_step_id": "two"},
        {"next_step_index": 2},
        {"next_employee_id": "b"},
        {"reason": "bad"},
    ],
)
def test_workflow_complete_return_rejects_every_wrong_field(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    values = dict(
        decision="workflow_complete",
        workflow_id="w",
        current_step_id="one",
        current_step_index=1,
        current_employee_id="a",
        next_step_id=None,
        next_step_index=None,
        next_employee_id=None,
        reason="last_step_succeeded",
    )
    values.update(changes)
    returned = WorkflowProgressionDecision(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=lambda *_: returned
        )


def test_failure_rejects_equivalent_but_distinct_return_object(tmp_path: Path) -> None:
    state, events, outcome, definition = setup(tmp_path, status="failed")
    equivalent = PersistedExecutionOutcome(*outcome.__dict__.values())
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=lambda *_: equivalent
        )


@pytest.mark.parametrize("missing", ["state", "events"])
def test_missing_target_has_zero_calls_writes_and_unchanged_other(
    tmp_path: Path, missing: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    other = events if missing == "state" else state
    before, calls = other.read_bytes(), 0
    (state if missing == "state" else events).unlink()

    def dependency(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=dependency
        )
    assert calls == 0 and other.read_bytes() == before


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
def test_unchanged_dependency_errors_preserve_identity_or_sanitize_without_writes(
    tmp_path: Path, kind: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0
    safe = ClassifiedPersistedOutcomeRoutingCompatibilityError("result_type")

    def dependency(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        if kind == "safe":
            raise safe
        raise RuntimeError("provider request response output")

    expected = (
        ClassifiedPersistedOutcomeRoutingCompatibilityError
        if kind == "safe"
        else ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=dependency
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert (
            caught.value.detail.classification == "dependency_error"
            and "provider" not in str(caught.value)
        )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("current_step_index", 0),
        ("current_step_index", True),
        ("current_step_id", "bad"),
        ("current_employee_id", "bad"),
    ],
)
def test_invalid_outcome_identity_rejects_before_dependency(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    values = dict(outcome.__dict__)
    values[field] = value
    bad = PersistedExecutionOutcome(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            bad,
            definition,
            state,
            events,
            routing_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )


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
    """Six-step Phase-155 provenance fixture shared by the Phase 171 tests.

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
        "".join(
            serialize_runtime_step_event_jsonl(event) for event in events
        ).encode("utf-8")
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


def test_phase155_success_delegates_to_phase45_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase-155 persisted success delegates to Phase 45 exactly once with
    canonical four-argument object identity/order, exact returned-object
    identity, and unchanged targets."""
    result, definition, state, events = phase155_setup(tmp_path, "succeeded")
    before = state.read_bytes(), events.read_bytes()
    decision = _phase155_expected_decision()
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        return decision

    returned = route_classified_persisted_outcome_bridge_reentry(
        result, definition, state, events, routing_function=dependency
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

    alt_returned = route_classified_persisted_outcome_bridge_reentry(
        alt[0],
        alt[1],
        alt[2],
        alt[3],
        routing_function=alt_dependency,
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
    pin_calls = {"phase45": 0}

    def pin_dependency(*_: object) -> object:
        pin_calls["phase45"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            pin[0], pin[1], pin[2], pin[3], routing_function=pin_dependency
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert pin_calls["phase45"] == 0
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
    pin2_calls = {"phase45": 0}

    def pin2_dependency(*_: object) -> object:
        pin2_calls["phase45"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            pin2[0], pin2[1], pin2[2], pin2[3], routing_function=pin2_dependency
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert pin2_calls["phase45"] == 0
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
    pin3_calls = {"phase45": 0}

    def pin3_dependency(*_: object) -> object:
        pin3_calls["phase45"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            completion,
            pin3[1],
            pin3[2],
            pin3[3],
            routing_function=pin3_dependency,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert pin3_calls["phase45"] == 0
    assert (pin3[2].read_bytes(), pin3[3].read_bytes()) == pin3_before

    # A transient OSError on the strict-path state read stays terminal_contract:
    # storage wraps it as WorkflowExecutionLoadError and
    # load_strict_terminal_history re-wraps it as TerminalHistoryContractError
    # with a cause, so the Phase-155 fallback must not be entered even though a
    # retry read would succeed.  The dependency stays uncalled and both targets
    # stay byte-for-byte unchanged.
    io = phase155_setup(tmp_path / "io", "succeeded")
    io_before = io[2].read_bytes(), io[3].read_bytes()
    real_read_bytes = Path.read_bytes
    state_reads = {"count": 0}

    def flaky_read_bytes(self: Path, *args: object, **kwargs: object) -> bytes:
        if self == io[2]:
            state_reads["count"] += 1
            if state_reads["count"] == 2:  # the strict-path storage state read
                raise OSError("simulated transient strict-path read failure")
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", flaky_read_bytes)
    io_calls = {"phase45": 0}

    def io_forbidden(*_: object) -> object:
        io_calls["phase45"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            io[0], io[1], io[2], io[3], routing_function=io_forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert io_calls["phase45"] == 0
    assert state_reads["count"] == 2  # no fallback retry read occurred
    # The failure was transient: a fresh read succeeds with identical bytes.
    assert (io[2].read_bytes(), io[3].read_bytes()) == io_before


def test_phase155_failure_empty_message_delegates_to_phase45_exactly_once(
    tmp_path: Path,
) -> None:
    """Phase-155 persisted failure with valid message="" delegates to Phase 45
    exactly once and returns the exact supplied object unchanged."""
    result, definition, state, events = phase155_setup(
        tmp_path, "failed", message=""
    )
    before = state.read_bytes(), events.read_bytes()
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> PersistedExecutionOutcome:
        calls.append(args)
        return result

    returned = route_classified_persisted_outcome_bridge_reentry(
        result, definition, state, events, routing_function=dependency
    )
    assert returned is result
    assert len(calls) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            calls[0], (result, definition, state, events), strict=True
        )
    )
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_success_multiple_earlier_empty_delegates_once(
    tmp_path: Path,
) -> None:
    """Phase-155 persisted success with multiple earlier empty outputs (steps
    2 and 3) delegates to Phase 45 exactly once with unchanged targets."""
    result, definition, state, events = phase155_setup(
        tmp_path, "succeeded", earlier_empty=(2, 3)
    )
    before = state.read_bytes(), events.read_bytes()
    decision = _phase155_expected_decision()
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        return decision

    returned = route_classified_persisted_outcome_bridge_reentry(
        result, definition, state, events, routing_function=dependency
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


def test_phase155_failure_multiple_earlier_empty_delegates_once(tmp_path: Path) -> None:
    """Phase-155 persisted failure with multiple earlier empty outputs (steps
    2 and 3) and message="" delegates to Phase 45 exactly once with unchanged
    targets."""
    result, definition, state, events = phase155_setup(
        tmp_path, "failed", earlier_empty=(2, 3), message=""
    )
    before = state.read_bytes(), events.read_bytes()
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> PersistedExecutionOutcome:
        calls.append(args)
        return result

    returned = route_classified_persisted_outcome_bridge_reentry(
        result, definition, state, events, routing_function=dependency
    )
    assert returned is result
    assert len(calls) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            calls[0], (result, definition, state, events), strict=True
        )
    )
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step2_output_none_rejected_before_phase45(tmp_path: Path) -> None:
    """Earlier predecessor step 2 output_text=None rejects with exact
    terminal_contract before Phase 45 with zero calls."""
    result, definition, state, events = phase155_setup(tmp_path, "succeeded")
    _phase155_replace_event(
        events,
        2,
        _phase155_predecessor("two", 2, output_text=None),
    )
    before = state.read_bytes(), events.read_bytes()
    calls = {"phase45": 0}

    def forbidden(*_: object) -> object:
        calls["phase45"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            result, definition, state, events, routing_function=forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase45": 0}
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step5_output_non_string_rejected_before_phase45(
    tmp_path: Path,
) -> None:
    """Immediate predecessor step 5 output_text=1 (non-string) rejects with
    exact terminal_contract before Phase 45 with zero calls."""
    result, definition, state, events = phase155_setup(tmp_path, "succeeded")
    _phase155_replace_event(
        events,
        5,
        _phase155_predecessor(
            "five", 5, provider="openai", request_id=None, output_text=1
        ),
    )
    before = state.read_bytes(), events.read_bytes()
    calls = {"phase45": 0}

    def forbidden(*_: object) -> object:
        calls["phase45"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            result, definition, state, events, routing_function=forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase45": 0}
    assert (state.read_bytes(), events.read_bytes()) == before
