"""Tests for Phase 46 using only injected Phase 39 fakes."""

from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    ProgressionPreparationRoutingCompatibilityError,
    WorkflowProgressionDecision,
    route_progression_preparation_reentry,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_success_preparation_routing_reentry import (
    PersistedSuccessPreparationRoutingCompatibilityError,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "first", "name": "F", "employee": "one", "instructions": "a"},
                {"id": "second", "name": "S", "employee": "two", "instructions": "b"},
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "two",
            "name": "Two",
            "role": "R",
            "instructions": "employee",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def decision(final: bool = False) -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete" if final else "prepare_next_step",
        "workflow",
        "second" if final else "first",
        2 if final else 1,
        "two" if final else "one",
        None if final else "second",
        None if final else 2,
        None if final else "two",
        "last_step_succeeded" if final else "next_step_available",
    )


def approval() -> NextStepPreparationApproval:
    return NextStepPreparationApproval(True, "workflow", "first", 1, "second", 2, "two")


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        "workflow", "second", 2, "two", "employee", "b", "model", ("tool",)
    )


def targets(
    tmp_path: Path, status: str = "succeeded", final: bool = False
) -> tuple[Path, Path, PersistedExecutionOutcome]:
    index, step, person = (2, "second", "two") if final else (1, "first", "one")
    complete = (
        ("first", "second")
        if final and status == "succeeded"
        else (("first",) if status == "succeeded" else ())
    )
    state = WorkflowExecutionState(
        "workflow",
        status,
        step,
        index,
        person,
        complete,
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    terminal = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "workflow",
        step,
        index,
        person,
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "output" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    event_bytes = serialize_runtime_step_event_jsonl(terminal).encode()
    if final:
        prior = RuntimeStepEvent(
            "step_succeeded",
            "workflow",
            "first",
            1,
            "one",
            "running",
            "succeeded",
            "openai",
            None,
            "response",
            "request",
            "output",
            None,
        )
        event_bytes = serialize_runtime_step_event_jsonl(prior).encode() + event_bytes
    events_path.write_bytes(event_bytes)
    return (
        state_path,
        events_path,
        PersistedExecutionOutcome(
            "persisted_success" if status == "succeeded" else "persisted_failure",
            "workflow",
            step,
            index,
            person,
            None if status == "succeeded" else "api_error",
        ),
    )  # type: ignore[arg-type]


def test_prepare_delegates_exact_objects_once_and_returns_same_object(
    tmp_path: Path,
) -> None:
    state, events, _ = targets(tmp_path)
    supplied_workflow, supplied_decision, supplied_approval, supplied_employee = (
        workflow(),
        decision(),
        approval(),
        employee(),
    )
    before, calls, expected = (state.read_bytes(), events.read_bytes()), 0, prepared()

    def phase39(*args: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        (
            actual_decision,
            actual_workflow,
            actual_state,
            actual_events,
            actual_approval,
            actual_employee,
        ) = args
        assert (
            actual_decision is supplied_decision
            and actual_workflow is supplied_workflow
        )
        assert actual_state is state and actual_events is events
        assert (
            actual_approval is supplied_approval
            and actual_employee is supplied_employee
        )
        return expected

    assert (
        route_progression_preparation_reentry(
            supplied_decision,
            supplied_workflow,
            state,
            events,
            supplied_approval,
            supplied_employee,
            preparation_routing_function=phase39,
        )
        is expected
    )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_completion_is_read_only_and_keeps_same_object(tmp_path: Path) -> None:
    state, events, _ = targets(tmp_path, final=True)
    supplied, before, calls = (
        decision(True),
        (state.read_bytes(), events.read_bytes()),
        0,
    )

    def unexpected(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_progression_preparation_reentry(
            supplied,
            workflow(),
            state,
            events,
            approval(),
            employee(),
            preparation_routing_function=unexpected,
        )
        is supplied
    )
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "mode",
    [
        "invalid-state",
        "invalid-events",
        "running-state",
        "wrong-completed",
        "wrong-prior",
        "wrong-terminal",
        "wrong-success-response",
    ],
)
def test_completion_requires_strict_final_succeeded_history(
    tmp_path: Path, mode: str
) -> None:
    state, events, _ = targets(tmp_path, final=True)
    if mode == "invalid-state":
        state.write_bytes(b"not-json")
    elif mode == "invalid-events":
        events.write_bytes(b"not-json\n")
    elif mode == "running-state":
        running = WorkflowExecutionState(
            "workflow", "running", "second", 2, "two", ("first",), None
        )
        state.write_bytes(serialize_workflow_execution_state_json(running).encode())
    elif mode == "wrong-completed":
        bad = WorkflowExecutionState(
            "workflow", "succeeded", "second", 2, "two", ("first",), None
        )
        state.write_bytes(serialize_workflow_execution_state_json(bad).encode())
    elif mode == "wrong-prior":
        terminal = events.read_bytes().splitlines(keepends=True)[-1]
        prior = RuntimeStepEvent(
            "step_succeeded",
            "workflow",
            "second",
            2,
            "two",
            "ready",
            "succeeded",
            "openai",
            None,
            "response",
            "request",
            "output",
            None,
        )
        events.write_bytes(
            serialize_runtime_step_event_jsonl(prior).encode() + terminal
        )
    elif mode == "wrong-terminal":
        prior = events.read_bytes().splitlines(keepends=True)[0]
        terminal = RuntimeStepEvent(
            "step_failed",
            "workflow",
            "second",
            2,
            "two",
            "running",
            "failed",
            "openai",
            "api_error",
            None,
            "request",
            None,
            "safe",
        )
        events.write_bytes(
            prior + serialize_runtime_step_event_jsonl(terminal).encode()
        )
    else:
        prior = events.read_bytes().splitlines(keepends=True)[0]
        terminal = RuntimeStepEvent(
            "step_succeeded",
            "workflow",
            "second",
            2,
            "two",
            "running",
            "succeeded",
            "openai",
            None,
            None,
            "request",
            "output",
            None,
        )
        events.write_bytes(
            prior + serialize_runtime_step_event_jsonl(terminal).encode()
        )
    before, writes, calls = (state.read_bytes(), events.read_bytes()), [], 0
    original = Path.write_bytes

    def record(path: Path, data: bytes) -> int:
        writes.append(path)
        return original(path, data)

    def unexpected(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(ProgressionPreparationRoutingCompatibilityError) as caught:
            route_progression_preparation_reentry(
                decision(True),
                workflow(),
                state,
                events,
                approval(),
                employee(),
                preparation_routing_function=unexpected,
            )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0 and writes == []
    assert (state.read_bytes(), events.read_bytes()) == before


def test_failure_is_read_only_and_keeps_same_object(tmp_path: Path) -> None:
    state, events, outcome = targets(tmp_path, "failed")
    calls = 0

    def unexpected(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_progression_preparation_reentry(
            outcome,
            workflow(),
            state,
            events,
            approval(),
            employee(),
            preparation_routing_function=unexpected,
        )
        is outcome
    )
    assert calls == 0


@pytest.mark.parametrize(
    "result, classification",
    [
        (object(), "result_type"),
        (
            PersistedExecutionOutcome(
                "persisted_success", "workflow", "first", 1, "one", None
            ),
            "failure_contract",
        ),
    ],
)
def test_top_level_rejections_do_not_call_or_write(
    tmp_path: Path, result: object, classification: str
) -> None:
    state, events, _ = targets(tmp_path)
    before, calls, writes = (state.read_bytes(), events.read_bytes()), 0, []
    original = Path.write_bytes

    def record(path: Path, data: bytes) -> int:
        writes.append(path)
        return original(path, data)

    def unexpected(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(ProgressionPreparationRoutingCompatibilityError) as caught:
            route_progression_preparation_reentry(
                result,
                workflow(),
                state,
                events,
                approval(),
                employee(),
                preparation_routing_function=unexpected,
            )
    assert caught.value.detail.classification == classification
    assert (
        calls == 0
        and writes == []
        and (state.read_bytes(), events.read_bytes()) == before
    )


@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_dependency_mutations_are_compensated_once(
    tmp_path: Path, operation: str, target: str
) -> None:
    state, events, _ = targets(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"changed")
        else:
            path.write_bytes(b"changed")

    def phase39(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            mutate(state)
        if target in {"events", "both"}:
            mutate(events)
        return prepared()

    with pytest.raises(ProgressionPreparationRoutingCompatibilityError) as caught:
        route_progression_preparation_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            preparation_routing_function=phase39,
        )
    assert caught.value.detail.classification == "dependency_error"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_safe_error_identity_is_preserved_after_compensation(tmp_path: Path) -> None:
    state, events, _ = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    expected = PersistedSuccessPreparationRoutingCompatibilityError("decision_type")

    def phase39(*_: object) -> PreparedWorkflowStep:
        events.unlink()
        raise expected

    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError) as caught:
        route_progression_preparation_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            preparation_routing_function=phase39,
        )
    assert (
        caught.value is expected and (state.read_bytes(), events.read_bytes()) == before
    )


def test_unexpected_error_is_sanitized(tmp_path: Path) -> None:
    state, events, _ = targets(tmp_path)
    with pytest.raises(ProgressionPreparationRoutingCompatibilityError) as caught:
        route_progression_preparation_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            preparation_routing_function=lambda *_: (_ for _ in ()).throw(
                RuntimeError("/secret response output message")
            ),
        )
    assert caught.value.detail.classification == "dependency_error"
    assert "/secret" not in str(caught.value) and "response" not in str(caught.value)


@pytest.mark.parametrize("mode", ["invalid-state", "invalid-event", "wrong-prefix"])
def test_terminal_history_rejection_precedes_phase39(tmp_path: Path, mode: str) -> None:
    state, events, _ = targets(tmp_path)
    if mode == "invalid-state":
        state.write_bytes(b"not-json")
    elif mode == "invalid-event":
        events.write_bytes(b"not-json\n")
    else:
        bad = RuntimeStepEvent(
            "step_succeeded",
            "workflow",
            "second",
            2,
            "two",
            "running",
            "succeeded",
            "openai",
            None,
            "response",
            "request",
            "output",
            None,
        )
        events.write_bytes(serialize_runtime_step_event_jsonl(bad).encode())
    calls = 0

    def unexpected(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ProgressionPreparationRoutingCompatibilityError) as caught:
        route_progression_preparation_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            preparation_routing_function=unexpected,
        )
    assert caught.value.detail.classification == "terminal_contract" and calls == 0


class PreparedSubclass(PreparedWorkflowStep):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class ApprovalSubclass(NextStepPreparationApproval):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


@pytest.mark.parametrize(
    "returned",
    [object(), PreparedSubclass("workflow", "second", 2, "two", "e", "b", "m", ())],
)
def test_phase39_return_requires_exact_prepared_model(
    tmp_path: Path, returned: object
) -> None:
    state, events, _ = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    with pytest.raises(ProgressionPreparationRoutingCompatibilityError) as caught:
        route_progression_preparation_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            preparation_routing_function=lambda *_: returned,  # type: ignore[return-value]
        )
    assert caught.value.detail.classification == "preparation_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_completion_contract_precedes_missing_targets(tmp_path: Path) -> None:
    state, events, _ = targets(tmp_path, final=True)
    malformed = WorkflowProgressionDecision(
        "workflow_complete", "workflow", "second", 2, "two", "other", 3, "three", "bad"
    )
    state.unlink()
    with pytest.raises(ProgressionPreparationRoutingCompatibilityError) as caught:
        route_progression_preparation_reentry(
            malformed,
            workflow(),
            state,
            events,
            approval(),
            employee(),
            preparation_routing_function=lambda *_: prepared(),
        )
    assert caught.value.detail.classification == "completion_contract"


@pytest.mark.parametrize(
    (
        "result",
        "definition",
        "actual_approval",
        "actual_employee",
        "target",
        "dependency",
        "classification",
    ),
    [
        (
            DecisionSubclass(*decision().__dict__.values()),
            None,
            approval(),
            employee(),
            None,
            None,
            "result_type",
        ),
        (
            None,
            WorkflowSubclass(**workflow().__dict__),
            approval(),
            employee(),
            None,
            None,
            "workflow_definition",
        ),
        (
            None,
            None,
            ApprovalSubclass(*approval().__dict__.values()),
            employee(),
            None,
            None,
            "approval_contract",
        ),
        (
            None,
            None,
            approval(),
            EmployeeSubclass(**employee().__dict__),
            None,
            None,
            "employee_contract",
        ),
        (None, None, approval(), employee(), "state", None, "state_target"),
        (None, None, approval(), employee(), "events", None, "event_target"),
        (None, None, approval(), employee(), "same", None, "target_conflict"),
        (None, None, approval(), employee(), None, object(), "preparation_contract"),
    ],
)
def test_exact_input_contracts_reject_before_reads_or_phase39(
    tmp_path: Path,
    result: object | None,
    definition: object | None,
    actual_approval: object | None,
    actual_employee: object | None,
    target: str | None,
    dependency: object | None,
    classification: str,
) -> None:
    state, events, _ = targets(tmp_path)
    calls = 0

    def unexpected(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        raise AssertionError

    actual_state: object = state if target not in {"state", "same"} else "wrong"
    actual_events: object = events if target != "events" else "wrong"
    if target == "same":
        actual_state = actual_events = state
    with pytest.raises(ProgressionPreparationRoutingCompatibilityError) as caught:
        route_progression_preparation_reentry(
            decision() if result is None else result,
            workflow() if definition is None else definition,
            actual_state,
            actual_events,
            actual_approval,
            actual_employee,
            preparation_routing_function=unexpected
            if dependency is None
            else dependency,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == classification and calls == 0


def test_rollback_failure_still_attempts_event_restoration(tmp_path: Path) -> None:
    state, events, _ = targets(tmp_path)
    original = Path.write_bytes
    writes: list[Path] = []

    def phase39(*_: object) -> PreparedWorkflowStep:
        state.unlink()
        events.write_bytes(b"changed")
        return prepared()

    def fail_state_restore(path: Path, data: bytes) -> int:
        writes.append(path)
        if path == state:
            raise OSError("secret path")
        return original(path, data)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", fail_state_restore)
        with pytest.raises(ProgressionPreparationRoutingCompatibilityError) as caught:
            route_progression_preparation_reentry(
                decision(),
                workflow(),
                state,
                events,
                approval(),
                employee(),
                preparation_routing_function=phase39,
            )
    assert caught.value.detail.classification == "dependency_rollback"
    assert state in writes and events in writes
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("actual_approval", "actual_employee", "classification"),
    [
        (type("ApprovalLike", (), {})(), employee(), "approval_contract"),
        (approval(), type("EmployeeLike", (), {})(), "employee_contract"),
    ],
)
def test_attribute_compatible_approval_and_employee_are_rejected_without_writes(
    tmp_path: Path,
    actual_approval: object,
    actual_employee: object,
    classification: str,
) -> None:
    state, events, _ = targets(tmp_path, final=True)
    before, writes = (state.read_bytes(), events.read_bytes()), []
    original = Path.write_bytes

    def record(path: Path, data: bytes) -> int:
        writes.append(path)
        return original(path, data)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        with pytest.raises(ProgressionPreparationRoutingCompatibilityError) as caught:
            route_progression_preparation_reentry(
                decision(True),
                workflow(),
                state,
                events,
                actual_approval,
                actual_employee,
                preparation_routing_function=lambda *_: prepared(),
            )
    assert caught.value.detail.classification == classification
    assert writes == [] and (state.read_bytes(), events.read_bytes()) == before
