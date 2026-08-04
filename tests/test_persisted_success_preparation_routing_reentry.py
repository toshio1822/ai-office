"""Tests for Phase 39 routing."""

from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ApprovedNextStepReentryCompatibilityError,
    PersistedSuccessPreparationRoutingCompatibilityError,
    route_persisted_success_progression_reentry,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_success_progression import (
    PersistedSuccessProgressionCompatibilityError,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class ApprovalSubclass(NextStepPreparationApproval):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


class PreparedSubclass(PreparedWorkflowStep):
    pass


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


def decision(**changes: object) -> WorkflowProgressionDecision:
    values: dict[str, object] = {
        "decision": "prepare_next_step",
        "workflow_id": "workflow",
        "current_step_id": "first",
        "current_step_index": 1,
        "current_employee_id": "one",
        "next_step_id": "second",
        "next_step_index": 2,
        "next_employee_id": "two",
        "reason": "next_step_available",
    }
    values.update(changes)
    return WorkflowProgressionDecision(**values)  # type: ignore[arg-type]


def approval() -> NextStepPreparationApproval:
    return NextStepPreparationApproval(True, "workflow", "first", 1, "second", 2, "two")


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        "workflow", "second", 2, "two", "employee", "b", "model", ("tool",)
    )


def targets(tmp_path: Path) -> tuple[Path, Path]:
    state, events = tmp_path / "state", tmp_path / "events"
    state.write_bytes(b"state")
    events.write_bytes(b"events")
    return state, events


def test_prepare_routes_once_and_returns_same_prepared_step(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = [0, 0]
    expected = prepared()

    def progress(*_: object) -> WorkflowProgressionDecision:
        calls[0] += 1
        return decision()

    def prepare(*_: object) -> PreparedWorkflowStep:
        calls[1] += 1
        return expected

    assert (
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=progress,
            preparation_function=prepare,
        )
        is expected
    )
    assert calls == [1, 1]


def test_complete_returns_same_decision_without_preparation(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    complete = decision(
        decision="workflow_complete",
        current_step_id="second",
        current_step_index=2,
        current_employee_id="two",
        next_step_id=None,
        next_step_index=None,
        next_employee_id=None,
        reason="last_step_succeeded",
    )
    calls = 0

    def prepare(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_persisted_success_progression_reentry(
            complete,
            workflow(),
            state,
            events,
            None,
            None,
            progression_function=lambda *_: complete,
            preparation_function=prepare,
        )
        is complete
    )
    assert calls == 0


@pytest.mark.parametrize(
    "change",
    [
        {"current_step_index": 3},
        {"current_step_id": "other"},
        {"next_step_id": "other"},
        {"reason": "other"},
    ],
)
def test_invalid_decision_rejects_before_dependencies(
    tmp_path: Path, change: dict[str, object]
) -> None:
    state, events = targets(tmp_path)
    calls = [0, 0]

    def p(*_: object) -> WorkflowProgressionDecision:
        calls[0] += 1
        raise AssertionError

    def q(*_: object) -> PreparedWorkflowStep:
        calls[1] += 1
        raise AssertionError

    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError):
        route_persisted_success_progression_reentry(
            decision(**change),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=p,
            preparation_function=q,
        )
    assert calls == [0, 0]


def test_restores_preparation_target_mutation(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())

    def prepare(*_: object) -> PreparedWorkflowStep:
        events.unlink()
        return prepared()

    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError) as error:
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=lambda *_: decision(),
            preparation_function=prepare,
        )
    assert error.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    (
        "value",
        "definition",
        "state_value",
        "events_value",
        "actual_approval",
        "actual_employee",
        "progression",
        "preparation",
    ),
    [
        (object(), None, None, None, None, None, None, None),
        (None, object(), None, None, None, None, None, None),
        (None, None, 1, None, None, None, None, None),
        (None, None, None, 1, None, None, None, None),
        (None, None, "same", "same", None, None, None, None),
        (None, None, None, None, object(), None, None, None),
        (None, None, None, None, None, object(), None, None),
        (None, None, None, None, None, None, 1, None),
        (None, None, None, None, None, None, None, 1),
    ],
)
def test_prevalidation_rejects_with_zero_calls_and_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: object,
    definition: object,
    state_value: object,
    events_value: object,
    actual_approval: object,
    actual_employee: object,
    progression: object,
    preparation: object,
) -> None:
    state, events = targets(tmp_path)
    calls: list[str] = []
    writes: list[Path] = []
    original = Path.write_bytes

    def record(path: Path, data: bytes) -> int:
        if path in {state, events}:
            writes.append(path)
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", record)

    def p(*_: object) -> WorkflowProgressionDecision:
        calls.append("p")
        raise AssertionError

    def q(*_: object) -> PreparedWorkflowStep:
        calls.append("q")
        raise AssertionError

    actual_decision = decision() if value is None else value
    actual_workflow = workflow() if definition is None else definition
    actual_state = state if state_value is None else state_value
    actual_events = events if events_value is None else events_value
    if state_value == "same":
        actual_state = actual_events = state
    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError):
        route_persisted_success_progression_reentry(
            actual_decision,
            actual_workflow,
            actual_state,
            actual_events,
            approval() if actual_approval is None else actual_approval,
            employee() if actual_employee is None else actual_employee,
            progression_function=p if progression is None else progression,
            preparation_function=q if preparation is None else preparation,
        )  # type: ignore[arg-type]
    assert calls == [] and writes == []


@pytest.mark.parametrize(
    "invalid",
    [
        DecisionSubclass(**decision().__dict__),
        WorkflowSubclass.model_validate(workflow().model_dump()),
        ApprovalSubclass(*approval().__dict__.values()),
        EmployeeSubclass.model_validate(employee().model_dump()),
        approval().__class__(False, "workflow", "first", 1, "second", 2, "two"),
        approval().__class__(True, "other", "first", 1, "second", 2, "two"),
        approval().__class__(True, "workflow", "other", 1, "second", 2, "two"),
        approval().__class__(True, "workflow", "first", 2, "second", 2, "two"),
        approval().__class__(True, "workflow", "first", 1, "other", 2, "two"),
        approval().__class__(True, "workflow", "first", 1, "second", 3, "two"),
        approval().__class__(True, "workflow", "first", 1, "second", 2, "other"),
        EmployeeDefinition.model_validate({**employee().model_dump(), "id": "other"}),
    ],
)
def test_prevalidation_contracts_reject_before_calls_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: object
) -> None:
    state, events = targets(tmp_path)
    calls: list[str] = []
    writes: list[Path] = []
    original = Path.write_bytes
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original(path, data))[1],
    )
    inputs: dict[str, object] = {
        "decision": decision(),
        "workflow": workflow(),
        "approval": approval(),
        "employee": employee(),
    }
    if isinstance(invalid, WorkflowProgressionDecision):
        inputs["decision"] = invalid
    elif isinstance(invalid, WorkflowDefinition):
        inputs["workflow"] = invalid
    elif isinstance(invalid, NextStepPreparationApproval):
        inputs["approval"] = invalid
    else:
        inputs["employee"] = invalid

    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError):
        route_persisted_success_progression_reentry(
            inputs["decision"],
            inputs["workflow"],
            state,
            events,
            inputs["approval"],
            inputs["employee"],
            progression_function=lambda *_: calls.append("31"),
            preparation_function=lambda *_: calls.append("32"),
        )  # type: ignore[arg-type]
    assert calls == [] and writes == []


@pytest.mark.parametrize("missing", ["state", "events"])
def test_missing_target_rejects_before_calls_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    state, events = targets(tmp_path)
    missing_path = state if missing == "state" else events
    missing_path.unlink()
    writes: list[Path] = []
    original = Path.write_bytes
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original(path, data))[1],
    )
    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError):
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=lambda *_: pytest.fail("Phase 31 must not run"),
            preparation_function=lambda *_: pytest.fail("Phase 32 must not run"),
        )
    assert writes == []


@pytest.mark.parametrize(
    "returned",
    [
        object(),
        DecisionSubclass(**decision().__dict__),
        decision(workflow_id="other"),
        decision(current_step_id="other"),
        decision(current_step_index=2),
        decision(current_employee_id="other"),
        decision(next_step_id="other"),
        decision(next_step_index=3),
        decision(next_employee_id="other"),
        decision(reason="other"),
        decision(
            decision="workflow_complete",
            next_step_id=None,
            next_step_index=None,
            next_employee_id=None,
            reason="last_step_succeeded",
        ),
    ],
)
def test_phase31_contract_rejections_prevent_phase32(
    tmp_path: Path, returned: object
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def prepare(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError):
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=lambda *_: returned,
            preparation_function=prepare,
        )  # type: ignore[arg-type]
    assert calls == 0


@pytest.mark.parametrize(
    "returned",
    [
        object(),
        PreparedSubclass(*prepared().__dict__.values()),
        PreparedWorkflowStep(
            "other", "second", 2, "two", "employee", "b", "model", ("tool",)
        ),
        PreparedWorkflowStep(
            "workflow", "other", 2, "two", "employee", "b", "model", ("tool",)
        ),
        PreparedWorkflowStep(
            "workflow", "second", 3, "two", "employee", "b", "model", ("tool",)
        ),
        PreparedWorkflowStep(
            "workflow", "second", 2, "other", "employee", "b", "model", ("tool",)
        ),
        PreparedWorkflowStep(
            "workflow", "second", 2, "two", "other", "b", "model", ("tool",)
        ),
        PreparedWorkflowStep(
            "workflow", "second", 2, "two", "employee", "other", "model", ("tool",)
        ),
        PreparedWorkflowStep(
            "workflow", "second", 2, "two", "employee", "b", "other", ("tool",)
        ),
        PreparedWorkflowStep(
            "workflow", "second", 2, "two", "employee", "b", "model", ()
        ),
    ],
)
def test_phase32_contract_rejections(tmp_path: Path, returned: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError) as error:
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=lambda *_: decision(),
            preparation_function=lambda *_: returned,
        )  # type: ignore[arg-type]
    assert error.value.detail.classification == "preparation_contract"


@pytest.mark.parametrize("phase", ["progression", "preparation"])
def test_safe_errors_are_same_object_and_unchanged_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    state, events = targets(tmp_path)
    writes: list[Path] = []
    original = Path.write_bytes
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original(path, data))[1],
    )
    expected = (
        PersistedSuccessProgressionCompatibilityError("history_data")
        if phase == "progression"
        else ApprovedNextStepReentryCompatibilityError("history_data")
    )

    def progress(*_: object) -> WorkflowProgressionDecision:
        if phase == "progression":
            raise expected
        return decision()

    def prepare(*_: object) -> PreparedWorkflowStep:
        if phase == "preparation":
            raise expected
        raise AssertionError

    with pytest.raises(type(expected)) as error:
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=progress,
            preparation_function=prepare,
        )
    assert error.value is expected and writes == []


@pytest.mark.parametrize("phase", ["progression", "preparation"])
@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("operation", ["replace", "truncate", "append", "delete"])
def test_dependency_target_mutations_are_restored(
    tmp_path: Path, phase: str, target: str, operation: str
) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    changed = state if target == "state" else events

    def mutate() -> None:
        if operation == "replace":
            changed.write_bytes(b"replacement")
        elif operation == "truncate":
            changed.write_bytes(b"")
        elif operation == "append":
            changed.write_bytes(changed.read_bytes() + b"append")
        else:
            changed.unlink()

    def p(*_: object) -> WorkflowProgressionDecision:
        if phase == "progression":
            mutate()
        return decision()

    def q(*_: object) -> PreparedWorkflowStep:
        if phase == "preparation":
            mutate()
        return prepared()

    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError) as error:
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=p,
            preparation_function=q,
        )
    assert error.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_unexpected_error_is_safe_and_not_retried(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def p(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret path and provider output")

    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError) as error:
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=p,
        )
    assert (
        error.value.detail.classification == "dependency_error"
        and "secret" not in str(error.value)
        and calls == 1
    )


@pytest.mark.parametrize("phase", ["progression", "preparation"])
def test_unchanged_unexpected_errors_do_not_write_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    state, events = targets(tmp_path)
    writes: list[Path] = []
    original = Path.write_bytes
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original(path, data))[1],
    )

    def progress(*_: object) -> WorkflowProgressionDecision:
        if phase == "progression":
            raise RuntimeError("credential=private")
        return decision()

    def prepare(*_: object) -> PreparedWorkflowStep:
        if phase == "preparation":
            raise RuntimeError("credential=private")
        raise AssertionError

    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError) as error:
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=progress,
            preparation_function=prepare,
        )
    assert error.value.detail.classification == "dependency_error" and writes == []


@pytest.mark.parametrize("target", ["state", "events"])
def test_restoration_failure_is_safely_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    state, events = targets(tmp_path)
    original = Path.write_bytes

    def fail(path: Path, data: bytes) -> int:
        if path == (state if target == "state" else events):
            raise OSError
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail)

    def p(*_: object) -> WorkflowProgressionDecision:
        (state if target == "state" else events).unlink()
        return decision()

    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError) as error:
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=p,
        )
    assert error.value.detail.classification == "dependency_rollback"
