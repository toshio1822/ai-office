"""Focused Phase 67 boundary tests using injected Phase 60 fakes only."""

# ruff: noqa: E501

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.approved_next_step_preparation_phase_bridge_continuation import (
    ApprovedNextStepPreparationPhaseBridgeContinuationError,
    route_approved_next_step_preparation_phase_bridge_continuation,
)
from ai_office.engine.approved_next_step_preparation_phase_bridge_reentry import (
    ApprovedNextStepPreparationPhaseBridgeError,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "test",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "one",
                    "instructions": "a",
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "two",
                    "instructions": "b",
                },
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "two",
            "name": "Two",
            "role": "role",
            "instructions": "employee",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


class StringSubclass(str):
    pass


class IntegerSubclass(int):
    pass


def decision() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "prepare_next_step",
        "workflow",
        "first",
        1,
        "one",
        "second",
        2,
        "two",
        "next_step_available",
    )


def approval() -> NextStepPreparationApproval:
    return NextStepPreparationApproval(True, "workflow", "first", 1, "second", 2, "two")


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        "workflow", "second", 2, "two", "employee", "b", "model", ("tool",)
    )


def event(status: str, index: int) -> RuntimeStepEvent:
    step, person = ("first", "one") if index == 1 else ("second", "two")
    return RuntimeStepEvent(
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
    )


def targets(
    tmp_path: Path, status: str = "succeeded", index: int = 1
) -> tuple[Path, Path]:
    definition = workflow()
    step = definition.steps[index - 1]
    completed = (
        tuple(item.id for item in definition.steps[:index])
        if status == "succeeded"
        else tuple(item.id for item in definition.steps[: index - 1])
    )
    state = WorkflowExecutionState(
        "workflow",
        status,
        step.id,
        index,
        step.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )
    events = [event("succeeded", 1)] if index == 2 else []
    events.append(event(status, index))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.json"
    state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events),
        encoding="utf-8",
    )
    return state_path, events_path


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete",
        "workflow",
        "second",
        2,
        "two",
        None,
        None,
        None,
        "last_step_succeeded",
    )


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_failure", "workflow", "first", 1, "one", "api_error"
    )


def test_prepare_calls_phase60_once_and_returns_exact_value(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    supplied, definition, person, approved = (
        decision(),
        workflow(),
        employee(),
        approval(),
    )
    result = prepared()
    calls: list[tuple[object, ...]] = []

    def phase60(*args: object) -> PreparedWorkflowStep:
        calls.append(args)
        assert args == (supplied, definition, state, events, approved, person)
        return result

    returned = route_approved_next_step_preparation_phase_bridge_continuation(
        supplied,
        definition,
        state,
        events,
        approved,
        person,
        phase60_function=phase60,
    )
    assert returned is result and len(calls) == 1


@pytest.mark.parametrize(
    "terminal, status", [(completion, "succeeded"), (failure, "failed")]
)
def test_terminal_routes_return_same_object_without_dependency(
    tmp_path: Path, terminal, status: str
) -> None:
    state, events = targets(tmp_path, status, 2 if status == "succeeded" else 1)
    value = terminal()
    calls = 0

    def phase60(*args: object):
        nonlocal calls
        calls += 1
        raise AssertionError("must not be called")

    before = (state.read_bytes(), events.read_bytes())
    returned = route_approved_next_step_preparation_phase_bridge_continuation(
        value,
        workflow(),
        state,
        events,
        None,
        None,
        phase60_function=phase60,
    )
    assert returned is value and calls == 0
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "value", [object(), SimpleNamespace(decision="prepare_next_step")]
)
def test_result_type_is_strict(tmp_path: Path, value: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            value, workflow(), state, events, None, None
        )
    assert error.value.detail.classification == "result_type"


@pytest.mark.parametrize(
    "bad_approval,bad_employee",
    [(None, employee()), (approval(), None), (SimpleNamespace(), employee())],
)
def test_prepare_requires_exact_approval_and_employee(
    tmp_path: Path, bad_approval, bad_employee
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            decision(), workflow(), state, events, bad_approval, bad_employee
        )
    assert error.value.detail.classification in {
        "approval_contract",
        "employee_contract",
    }


@pytest.mark.parametrize("value", [completion(), failure()])
def test_terminal_routes_reject_context(tmp_path: Path, value) -> None:
    state, events = targets(
        tmp_path,
        "failed" if isinstance(value, PersistedExecutionOutcome) else "succeeded",
        1 if isinstance(value, PersistedExecutionOutcome) else 2,
    )
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            value, workflow(), state, events, approval(), employee()
        )
    assert error.value.detail.classification in {
        "completion_contract",
        "failure_contract",
    }


@pytest.mark.parametrize(
    "mutator",
    [
        lambda v: replace(v, next_step_id="wrong"),
        lambda v: replace(v, current_step_index=True),
        lambda v: replace(v, reason="wrong"),
    ],
)
def test_prepare_decision_contract_is_strict(tmp_path: Path, mutator) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            mutator(decision()), workflow(), state, events, approval(), employee()
        )
    assert error.value.detail.classification == "decision_contract"


def test_approval_mismatch_is_rejected_before_dependency(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    bad = replace(approval(), next_step_index=1)
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            decision(), workflow(), state, events, bad, employee()
        )
    assert error.value.detail.classification == "approval_contract"


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("approved", False),
        ("approved", 1),
        ("workflow_id", "wrong"),
        ("workflow_id", StringSubclass("workflow")),
        ("current_step_id", "wrong"),
        ("current_step_id", StringSubclass("first")),
        ("current_step_index", 2),
        ("current_step_index", IntegerSubclass(1)),
        ("next_step_id", "wrong"),
        ("next_step_id", StringSubclass("second")),
        ("next_step_index", 1),
        ("next_step_index", IntegerSubclass(2)),
        ("next_employee_id", "wrong"),
        ("next_employee_id", StringSubclass("two")),
    ],
)
def test_every_approval_field_requires_exact_type_and_value(
    tmp_path: Path, field: str, bad_value: object
) -> None:
    state, events = targets(tmp_path)
    bad = replace(approval(), **{field: bad_value})
    calls = 0

    def phase60(*args: object):
        nonlocal calls
        calls += 1
        return prepared()

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            decision(),
            workflow(),
            state,
            events,
            bad,
            employee(),
            phase60_function=phase60,
        )
    assert error.value.detail.classification == "approval_contract"
    assert calls == 0


@pytest.mark.parametrize("bad_id", ["wrong", 2, StringSubclass("two")])
def test_employee_id_requires_exact_string_and_decision_match(
    tmp_path: Path, bad_id: object
) -> None:
    state, events = targets(tmp_path)
    bad_employee = employee().model_copy(update={"id": bad_id})
    calls = 0

    def phase60(*args: object):
        nonlocal calls
        calls += 1
        return prepared()

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            bad_employee,
            phase60_function=phase60,
        )
    assert error.value.detail.classification == "employee_contract"
    assert calls == 0


@pytest.mark.parametrize("which", ["state", "events"])
def test_target_is_file_oserror_is_classified_separately(
    tmp_path: Path, monkeypatch, which: str
) -> None:
    state, events = targets(tmp_path)
    target = state if which == "state" else events
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda self: (_ for _ in ()).throw(OSError()) if self == target else True,
    )
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            decision(), workflow(), state, events, approval(), employee()
        )
    assert error.value.detail.classification == (
        "state_target" if which == "state" else "event_target"
    )


@pytest.mark.parametrize("which", ["state", "events"])
def test_target_read_bytes_oserror_is_classified_separately(
    tmp_path: Path, monkeypatch, which: str
) -> None:
    state, events = targets(tmp_path)
    target = state if which == "state" else events
    original = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (
            (_ for _ in ()).throw(OSError()) if self == target else original(self)
        ),
    )
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            decision(), workflow(), state, events, approval(), employee()
        )
    assert error.value.detail.classification == (
        "state_target" if which == "state" else "event_target"
    )


def test_malformed_dependency_return_restores_both_without_retry(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def phase60(*args: object):
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        return object()

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase60_function=phase60,
        )
    assert error.value.detail.classification == "preparation_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "field", ["step_id", "step_index", "employee_id", "model", "allowed_tool_names"]
)
def test_prepared_return_fields_are_strict(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    value = prepared()
    updates = {
        "step_id": "wrong",
        "step_index": 1,
        "employee_id": "wrong",
        "model": "wrong",
        "allowed_tool_names": ("wrong",),
    }
    bad = replace(value, **{field: updates[field]})

    def phase60(*args: object) -> PreparedWorkflowStep:
        return bad

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase60_function=phase60,
        )
    assert error.value.detail.classification == "preparation_contract"


@pytest.mark.parametrize("failing", ["state", "events", "both"])
def test_rollback_failures_attempt_both_and_are_dependency_rollback(
    tmp_path: Path, monkeypatch, failing: str
) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0
    original_write = Path.write_bytes

    def phase60(*args: object):
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        return object()

    def write_bytes(self: Path, data: bytes) -> int:
        if self == state and data == before[0] and failing in {"state", "both"}:
            raise OSError()
        if self == events and data == before[1] and failing in {"events", "both"}:
            raise OSError()
        return original_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase60_function=phase60,
        )
    assert error.value.detail.classification == "dependency_rollback"
    assert calls == 1
    assert state.read_bytes() == (
        b"changed-state" if failing in {"state", "both"} else before[0]
    )
    assert events.read_bytes() == (
        b"changed-events" if failing in {"events", "both"} else before[1]
    )


def test_safe_dependency_error_preserves_identity_and_restores(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())

    def phase60(*args: object):
        state.write_bytes(b"changed")
        raise ApprovedNextStepPreparationPhaseBridgeError("safe")

    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeError):
        route_approved_next_step_preparation_phase_bridge_continuation(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase60_function=phase60,
        )
    assert (state.read_bytes(), events.read_bytes()) == before


def test_unexpected_dependency_error_is_sanitized(tmp_path: Path) -> None:
    state, events = targets(tmp_path)

    def phase60(*args: object):
        raise RuntimeError("secret")

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeContinuationError
    ) as error:
        route_approved_next_step_preparation_phase_bridge_continuation(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase60_function=phase60,
        )
    assert error.value.detail.classification == "dependency_error"
