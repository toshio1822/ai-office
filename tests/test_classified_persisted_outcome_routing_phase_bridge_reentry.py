"""Focused Phase 59 boundary tests using injected Phase 52 fakes only."""

import json
from pathlib import Path

import pytest

import ai_office.engine.terminal_history_contract as _contract_module
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError,
    ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_persisted_outcome_routing_phase_bridge_reentry,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.storage.workflow_execution_history import WorkflowExecutionLoadError


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
    "two,index",
    [(True, 1), (False, 1)],
)
def test_persisted_success_delegates_once_with_identity_and_returns_decision(
    tmp_path: Path, two: bool, index: int
) -> None:
    state, events, outcome, definition = setup(tmp_path, two=two, index=index)
    decision = WorkflowProgressionDecision(
        "prepare_next_step" if two else "workflow_complete",
        "w",
        outcome.current_step_id,
        index,
        outcome.current_employee_id,
        "two" if two else None,
        2 if two else None,
        "b" if two else None,
        "next_step_available" if two else "last_step_succeeded",
    )  # type: ignore[arg-type]
    calls = 0

    def phase52(*args: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        assert args == (outcome, definition, state, events)
        assert args[0] is outcome and args[1] is definition
        assert args[2] is state and args[3] is events
        return decision

    assert (
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=phase52
        )
        is decision
    )
    assert calls == 1


def test_persisted_failure_delegates_once_and_returns_same_object(
    tmp_path: Path,
) -> None:
    state, events, outcome, definition = setup(tmp_path, status="failed")
    calls = 0

    def phase52(*args: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        assert args[0] is outcome and args[1] is definition
        assert args[2] is state and args[3] is events
        return outcome

    assert (
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=phase52
        )
        is outcome
    )
    assert calls == 1


def test_workflow_complete_is_an_unchanged_zero_call_stop(tmp_path: Path) -> None:
    state, events, _, definition = setup(tmp_path)
    decision = WorkflowProgressionDecision(
        "workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded"
    )
    before = state.read_bytes(), events.read_bytes()
    assert (
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            decision,
            definition,
            state,
            events,
            phase52_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
        is decision
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "result",
    [
        object(),
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
    ],
)
def test_exact_result_types_are_required_without_dependency_call(
    tmp_path: Path, result: object
) -> None:
    state, events, _, definition = setup(tmp_path)
    calls = 0

    def phase52(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            result, definition, state, events, phase52_function=phase52
        )
    assert calls == 0


@pytest.mark.parametrize("field", ["outcome", "failure_category", "current_step_index"])
def test_persisted_outcome_discriminator_and_fields_are_strict(
    tmp_path: Path, field: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    values = dict(outcome.__dict__)
    values[field] = {
        "outcome": "persisted_failure",
        "failure_category": None,
        "current_step_index": True,
    }[field]
    malformed = PersistedExecutionOutcome(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            malformed,
            definition,
            state,
            events,
            phase52_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )


@pytest.mark.parametrize(
    "field", ["decision", "workflow_id", "current_step_index", "reason"]
)
def test_completion_fields_are_strict(tmp_path: Path, field: str) -> None:
    state, events, _, definition = setup(tmp_path)
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
    values[field] = {"decision": "prepare_next_step", "workflow_id": "bad"}.get(
        field, True
    )
    malformed = WorkflowProgressionDecision(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            malformed,
            definition,
            state,
            events,
            phase52_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )


@pytest.mark.parametrize(
    "field",
    [
        "decision",
        "workflow_id",
        "next_step_id",
        "next_step_index",
        "next_employee_id",
        "reason",
    ],
)
def test_success_return_fields_are_strict(tmp_path: Path, field: str) -> None:
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
    values[field] = {"decision": "workflow_complete", "workflow_id": "bad"}.get(
        field, True
    )
    returned = WorkflowProgressionDecision(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=lambda *_: returned
        )


def test_failure_rejects_equivalent_but_distinct_return_without_retry(
    tmp_path: Path,
) -> None:
    state, events, outcome, definition = setup(tmp_path, status="failed")
    equivalent = PersistedExecutionOutcome(*outcome.__dict__.values())
    calls = 0

    def phase52(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        return equivalent

    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=phase52
        )
    assert calls == 1


@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("kind", ["normal", "safe", "unexpected", "malformed"])
def test_dependency_mutations_errors_and_malformed_returns_are_compensated(
    tmp_path: Path, operation: str, target: str, kind: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError("routing_contract")

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"x")
        else:
            path.write_bytes(b"changed")

    def phase52(*_: object) -> object:
        if target in {"state", "both"}:
            mutate(state)
        if target in {"events", "both"}:
            mutate(events)
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("sensitive provider output")
        if kind == "malformed":
            return object()
        return WorkflowProgressionDecision(
            "prepare_next_step",
            "w",
            "one",
            1,
            "a",
            "two",
            2,
            "b",
            "next_step_available",
        )

    expected = (
        ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
        if kind == "safe"
        else ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome,
            definition,
            state,
            events,
            phase52_function=phase52,
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert "sensitive" not in str(caught.value)
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed", ["state", "events", "both"])
@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
def test_rollback_failures_attempt_both_targets_and_sanitize_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed: str,
    kind: str,
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    originals = {state: state.read_bytes(), events: events.read_bytes()}
    original_write = Path.write_bytes
    attempts: list[Path] = []
    calls = 0
    safe = ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError("routing_contract")

    def fail_restore(path: Path, contents: bytes) -> int:
        if contents == originals[path]:
            attempts.append(path)
            if failed == "both" or (failed == "state" and path == state) or (
                failed == "events" and path == events
            ):
                raise OSError("sensitive dependency detail")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", fail_restore)

    def phase52(*_: object) -> object:
        nonlocal calls
        calls += 1
        original_write(state, b"changed")
        original_write(events, b"changed")
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("sensitive dependency detail")
        return object()

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=phase52
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1
    assert attempts[:2] == [state, events]
    assert "sensitive" not in str(caught.value)


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("condition", ["missing", "directory"])
def test_missing_and_non_regular_targets_are_rejected_before_dependency(
    tmp_path: Path, target: str, condition: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    selected = state if target == "state" else events
    if condition == "missing":
        selected.unlink()
    else:
        selected.unlink()
        selected.mkdir()
    calls = 0

    def phase52(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=phase52
        )
    assert caught.value.detail.classification == (
        "state_target" if target == "state" else "event_target"
    )
    assert calls == 0


@pytest.mark.parametrize("bad", ["workflow", "state", "events", "same", "function"])
def test_workflow_targets_and_dependency_are_prevalidated(
    tmp_path: Path, bad: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    args: list[object] = [outcome, definition, state, events]
    if bad == "workflow":
        args[1] = WorkflowSubclass(**definition.__dict__)
    elif bad == "state":
        args[2] = "bad"
    elif bad == "events":
        args[3] = "bad"
    elif bad == "same":
        args[3] = state
    function: object = object() if bad == "function" else lambda *_: outcome
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            *args, phase52_function=function  # type: ignore[arg-type]
        )


_SIX = ("one", "two", "three", "four", "five", "six")
_SENTINEL = object()


def six_workflow() -> WorkflowDefinition:
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
                for step_id in _SIX
            ],
        }
    )


def six_predecessor(
    step_id: str,
    position: int,
    provider: object = "other",
    request_id: object = _SENTINEL,
    output_text: object = "output",
) -> RuntimeStepEvent:
    resolved_request_id = (
        f"request-{step_id}" if request_id is _SENTINEL else request_id
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


def six_terminal(status: str) -> RuntimeStepEvent:
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
        "safe failure",
    )


def setup_six(
    tmp_path: Path,
    status: str,
    *,
    earlier_empty: tuple[int, ...] = (2,),
) -> tuple[Path, Path, PersistedExecutionOutcome, WorkflowDefinition]:
    definition = six_workflow()
    state = WorkflowExecutionState(
        "w",
        status,
        "six",
        6,
        "s",
        tuple(_SIX) if status == "succeeded" else tuple(_SIX[:5]),
        None if status == "succeeded" else "api_error",
    )
    events = [
        six_predecessor(
            step_id,
            position,
            output_text="" if position in earlier_empty else "output",
        )
        for position, step_id in enumerate(_SIX[:5], 1)
    ]
    events[4] = six_predecessor(
        "five", 5, provider="openai", request_id=None, output_text=""
    )
    events.append(six_terminal(status))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_bytes(
        "".join(
            serialize_runtime_step_event_jsonl(event) for event in events
        ).encode()
    )
    outcome = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        "six",
        6,
        "s",
        None if status == "succeeded" else "api_error",
    )
    return state_path, events_path, outcome, definition


def test_phase155_success_delegates_once_with_identity_and_exact_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, events, outcome, definition = setup_six(tmp_path, "succeeded")
    decision = WorkflowProgressionDecision(
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
    received: list[object] = []

    def phase52(
        result_arg: object,
        workflow_arg: object,
        state_arg: object,
        events_arg: object,
    ) -> object:
        received.extend((result_arg, workflow_arg, state_arg, events_arg))
        return decision

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_persisted_outcome_routing_phase_bridge_reentry(
        outcome, definition, state, events, phase52_function=phase52
    )
    assert returned is decision
    assert (
        received[0] is outcome
        and received[1] is definition
        and received[2] is state
        and received[3] is events
    )
    assert (state.read_bytes(), events.read_bytes()) == before

    # --- inline pins below (no extra collected tests) ---
    # 1. WorkflowProgressionDecision(workflow_complete) never enters the
    #    Phase-155 fallback: the same empty-predecessor history stays strict.
    dep_calls = {"count": 0}

    def forbidden(*_: object) -> object:
        dep_calls["count"] += 1
        raise AssertionError("progression dependency must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            decision, definition, state, events, phase52_function=forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert dep_calls["count"] == 0
    assert (state.read_bytes(), events.read_bytes()) == before

    # 2. Terminal succeeded response_id="" stays rejected: the fallback does
    #    not weaken the terminal-success contract.
    lines = events.read_text().splitlines(keepends=True)
    terminal_line = json.loads(lines[-1])
    terminal_line["response_id"] = ""
    events.write_text(
        "".join(lines[:-1])
        + json.dumps(terminal_line, separators=(",", ":"))
        + "\n"
    )
    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert dep_calls["count"] == 0
    events.write_bytes(before[1])

    # 3. Final terminal succeeded output_text="" stays rejected.
    lines = events.read_text().splitlines(keepends=True)
    terminal_line = json.loads(lines[-1])
    terminal_line["output_text"] = ""
    events.write_text(
        "".join(lines[:-1])
        + json.dumps(terminal_line, separators=(",", ":"))
        + "\n"
    )
    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert dep_calls["count"] == 0
    events.write_bytes(before[1])

    # 4. Transient strict-path storage load failure surfaces as exact
    #    terminal_contract with NO fallback retry read (load-call count
    #    proof), zero dependency calls and unchanged targets.
    original_load = _contract_module.load_workflow_execution_history
    load_calls = {"count": 0}

    def failing_load(*_args: object, **_kwargs: object) -> object:
        load_calls["count"] += 1
        raise WorkflowExecutionLoadError("transient strict-path read failure")

    monkeypatch.setattr(
        _contract_module, "load_workflow_execution_history", failing_load
    )
    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert load_calls["count"] == 1  # no fallback retry read
    assert dep_calls["count"] == 0
    assert (state.read_bytes(), events.read_bytes()) == before
    monkeypatch.setattr(
        _contract_module, "load_workflow_execution_history", original_load
    )


def test_phase155_failure_empty_message_delegates_once_and_returns_same_object(
    tmp_path: Path,
) -> None:
    state, events, outcome, definition = setup_six(tmp_path, "failed")
    events.write_text(events.read_text().replace('"safe failure"', '""'))
    calls = 0

    def phase52(*args: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        assert args[0] is outcome and args[1] is definition
        assert args[2] is state and args[3] is events
        return outcome

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_persisted_outcome_routing_phase_bridge_reentry(
        outcome, definition, state, events, phase52_function=phase52
    )
    assert returned is outcome
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_phase155_multiple_earlier_empty_success_delegates_once(
    tmp_path: Path,
) -> None:
    state, events, outcome, definition = setup_six(
        tmp_path, "succeeded", earlier_empty=(2, 3)
    )
    decision = WorkflowProgressionDecision(
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
    received: list[object] = []

    def phase52(
        result_arg: object,
        workflow_arg: object,
        state_arg: object,
        events_arg: object,
    ) -> object:
        received.extend((result_arg, workflow_arg, state_arg, events_arg))
        return decision

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_persisted_outcome_routing_phase_bridge_reentry(
        outcome, definition, state, events, phase52_function=phase52
    )
    assert returned is decision
    assert (
        received[0] is outcome
        and received[1] is definition
        and received[2] is state
        and received[3] is events
    )
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_multiple_earlier_empty_failure_delegates_once_and_returns_same_object(
    tmp_path: Path,
) -> None:
    state, events, outcome, definition = setup_six(
        tmp_path, "failed", earlier_empty=(2, 3)
    )
    events.write_text(events.read_text().replace('"safe failure"', '""'))
    calls = 0

    def phase52(*args: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        assert args[0] is outcome and args[1] is definition
        assert args[2] is state and args[3] is events
        return outcome

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_persisted_outcome_routing_phase_bridge_reentry(
        outcome, definition, state, events, phase52_function=phase52
    )
    assert returned is outcome
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step2_output_none_is_rejected_before_dependency(
    tmp_path: Path,
) -> None:
    state, events, outcome, definition = setup_six(tmp_path, "succeeded")
    lines = events.read_text().splitlines(keepends=True)
    replacement = serialize_runtime_step_event_jsonl(
        six_predecessor("two", 2, output_text=None)
    )
    events.write_text(lines[0] + replacement + "".join(lines[2:]))
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def phase52(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=phase52
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step5_output_non_string_is_rejected_before_dependency(
    tmp_path: Path,
) -> None:
    state, events, outcome, definition = setup_six(tmp_path, "succeeded")
    lines = events.read_text().splitlines(keepends=True)
    replacement = serialize_runtime_step_event_jsonl(
        six_predecessor(
            "five", 5, provider="openai", request_id=None, output_text=1
        )
    )
    events.write_text("".join(lines[:4]) + replacement + "".join(lines[5:]))
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def phase52(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=phase52
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before
