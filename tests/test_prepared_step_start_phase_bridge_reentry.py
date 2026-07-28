"""Issue #110's Phase 54 bridge contract matrix, using injected fakes only."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    PreparedStepStartBridgeError,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_prepared_step_start_phase_bridge_reentry,
)
from ai_office.engine.prepared_step_start_phase_bridge_reentry import (
    PreparedStepStartPhaseBridgeCompatibilityError,
)
from ai_office.invocation import ModelInvocationRequest
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
    )  # type: ignore[arg-type]


def targets(tmp_path: Path, status: str, index: int) -> tuple[Path, Path]:
    step, person = ("first", "one") if index == 1 else ("second", "two")
    state = WorkflowExecutionState(
        "workflow",
        status,
        step,
        index,
        person,
        ("first",) if index == 1 or status == "failed" else ("first", "second"),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    events = ([event("succeeded", 1)] if index == 2 else []) + [event(status, index)]
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events)
    )
    return state_path, events_path


def start() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "employee", "b", ("tool",)),
        WorkflowExecutionState(
            "workflow", "running", "second", 2, "two", ("first",), None
        ),
    )


def assert_rejected(
    tmp_path: Path,
    result: object,
    definition: object = None,
    person: object = None,
    *,
    classification: str,
    state: Path | None = None,
    events: Path | None = None,
    dependency: object = None,
) -> None:
    definition = workflow() if definition is None else definition
    person = employee() if person is None else person
    state, events = (
        targets(tmp_path, "succeeded", 1) if state is None else (state, events)
    )
    assert state is not None and events is not None
    before = (
        (state.read_bytes(), events.read_bytes())
        if isinstance(state, Path)
        and isinstance(events, Path)
        and state.is_file()
        and events.is_file()
        else None
    )
    calls = 0

    def phase47(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return start()

    with pytest.raises(PreparedStepStartPhaseBridgeCompatibilityError) as caught:
        route_prepared_step_start_phase_bridge_reentry(
            result,
            definition,
            person,
            state,
            events,
            start_bridge_function=phase47 if dependency is None else dependency,
        )
    assert caught.value.detail.classification == classification
    assert calls == 0
    if before is not None:
        assert (state.read_bytes(), events.read_bytes()) == before


def test_prepared_step_delegates_once_with_identical_objects(tmp_path: Path) -> None:
    state, events = targets(tmp_path, "succeeded", 1)
    result, definition, person, expected = prepared(), workflow(), employee(), start()
    calls = 0

    def phase47(*args: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        assert args == (result, definition, person, state, events)
        assert all(
            actual is supplied
            for actual, supplied in zip(
                args, (result, definition, person, state, events), strict=True
            )
        )
        return expected

    before = state.read_bytes(), events.read_bytes()
    assert (
        route_prepared_step_start_phase_bridge_reentry(
            result, definition, person, state, events, start_bridge_function=phase47
        )
        is expected
    )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["completion", "failure"])
def test_terminal_routes_stop_without_phase47(tmp_path: Path, kind: str) -> None:
    status = "succeeded" if kind == "completion" else "failed"
    state, events = targets(tmp_path, status, 2)
    result: object = (
        WorkflowProgressionDecision(
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
        if kind == "completion"
        else PersistedExecutionOutcome(
            "persisted_failure", "workflow", "second", 2, "two", "api_error"
        )
    )
    before = state.read_bytes(), events.read_bytes()
    assert (
        route_prepared_step_start_phase_bridge_reentry(
            result,
            workflow(),
            None,
            state,
            events,
            start_bridge_function=lambda *_: pytest.fail("Phase 47 must not run"),
        )
        is result
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", "other"),
        ("step_id", "other"),
        ("step_index", 0),
        ("step_index", 1),
        ("step_index", -1),
        ("step_index", 3),
        ("step_index", True),
        ("step_index", "2"),
        ("employee_id", "other"),
        ("employee_instructions", "other"),
        ("step_instructions", "other"),
        ("model", "other"),
        ("allowed_tool_names", ("other",)),
        ("allowed_tool_names", ["tool"]),
        ("allowed_tool_names", ("tool", 1)),
    ],
)
def test_prepared_step_rejects_every_malformed_field(
    tmp_path: Path, field: str, value: object
) -> None:
    assert_rejected(
        tmp_path,
        replace(prepared(), **{field: value}),
        classification="prepared_step_contract",
    )


@pytest.mark.parametrize(
    ("kind", "field", "value", "classification"),
    [
        ("completion", "decision", "prepare_next_step", "completion_contract"),
        ("completion", "workflow_id", "other", "completion_contract"),
        ("completion", "current_step_id", "first", "completion_contract"),
        ("completion", "current_step_index", 1, "completion_contract"),
        ("completion", "current_employee_id", "one", "completion_contract"),
        ("completion", "next_step_id", "second", "completion_contract"),
        ("completion", "next_step_index", 2, "completion_contract"),
        ("completion", "next_employee_id", "two", "completion_contract"),
        ("completion", "reason", "other", "completion_contract"),
        ("failure", "outcome", "persisted_success", "failure_contract"),
        ("failure", "workflow_id", "other", "failure_contract"),
        ("failure", "current_step_id", "first", "failure_contract"),
        ("failure", "current_step_index", 0, "failure_contract"),
        ("failure", "current_step_index", -1, "failure_contract"),
        ("failure", "current_step_index", 3, "failure_contract"),
        ("failure", "current_step_index", True, "failure_contract"),
        ("failure", "current_step_index", "2", "failure_contract"),
        ("failure", "current_employee_id", "one", "failure_contract"),
        ("failure", "failure_category", None, "failure_contract"),
        ("failure", "failure_category", "other", "failure_contract"),
    ],
)
def test_terminal_results_reject_every_malformed_field(
    tmp_path: Path, kind: str, field: str, value: object, classification: str
) -> None:
    original: object = (
        WorkflowProgressionDecision(
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
        if kind == "completion"
        else PersistedExecutionOutcome(
            "persisted_failure", "workflow", "second", 2, "two", "api_error"
        )
    )
    state, events = targets(
        tmp_path, "succeeded" if kind == "completion" else "failed", 2
    )
    assert_rejected(
        tmp_path,
        replace(original, **{field: value}),
        person=None,
        classification=classification,
        state=state,
        events=events,
    )


@pytest.mark.parametrize("value", [object(), prepared])
def test_rejects_non_exact_result_types(tmp_path: Path, value: object) -> None:
    assert_rejected(tmp_path, value, classification="result_type")


def test_rejects_subclasses_and_substitutes(tmp_path: Path) -> None:
    class PreparedSubclass(PreparedWorkflowStep):
        pass

    class WorkflowSubclass(WorkflowDefinition):
        pass

    class EmployeeSubclass(EmployeeDefinition):
        pass

    assert_rejected(
        tmp_path,
        PreparedSubclass(*prepared().__dict__.values()),
        classification="result_type",
    )
    assert_rejected(
        tmp_path, prepared(), definition=object(), classification="workflow_definition"
    )
    assert_rejected(
        tmp_path, prepared(), person=object(), classification="employee_contract"
    )
    # Pydantic model subclasses are rejected before data access as well.
    assert_rejected(
        tmp_path,
        prepared(),
        definition=WorkflowSubclass.model_validate(workflow().model_dump()),
        classification="workflow_definition",
    )
    assert_rejected(
        tmp_path,
        prepared(),
        person=EmployeeSubclass.model_validate(employee().model_dump()),
        classification="employee_contract",
    )


@pytest.mark.parametrize(
    ("state_kind", "events_kind", "classification"),
    [
        (object(), Path("events.jsonl"), "state_target"),
        (Path("state.json"), object(), "event_target"),
    ],
)
def test_rejects_non_path_targets(
    tmp_path: Path, state_kind: object, events_kind: object, classification: str
) -> None:
    state, events = targets(tmp_path, "succeeded", 1)
    assert_rejected(
        tmp_path,
        prepared(),
        classification=classification,
        state=state_kind if not isinstance(state_kind, Path) else state,
        events=events_kind if not isinstance(events_kind, Path) else events,
    )


@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_rejects_missing_or_conflicting_targets(tmp_path: Path, target: str) -> None:
    state, events = targets(tmp_path, "succeeded", 1)
    if target == "state":
        state.unlink()
        expected = "state_target"
    elif target == "events":
        events.unlink()
        expected = "event_target"
    else:
        events = state
        expected = "target_conflict"
    with pytest.raises(PreparedStepStartPhaseBridgeCompatibilityError) as caught:
        route_prepared_step_start_phase_bridge_reentry(
            prepared(), workflow(), employee(), state, events
        )
    assert caught.value.detail.classification == expected


def test_rejects_non_callable_dependency(tmp_path: Path) -> None:
    assert_rejected(
        tmp_path, prepared(), classification="start_contract", dependency=object()
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", "other"),
        ("status", "failed"),
        ("current_step_id", "second"),
        ("current_step_index", 2),
        ("current_employee_id", "two"),
        ("completed_step_ids", ()),
        ("last_failure_category", "api_error"),
    ],
)
def test_prepared_route_requires_exact_preceding_state(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path, "succeeded", 1)
    # Directly modifying the serialized state makes the strict history contract fail.
    prior = WorkflowExecutionState(
        "workflow", "succeeded", "first", 1, "one", ("first",), None
    )
    state.write_text(
        serialize_workflow_execution_state_json(replace(prior, **{field: value}))
    )
    assert_rejected(
        tmp_path,
        prepared(),
        classification="terminal_contract",
        state=state,
        events=events,
    )


@pytest.mark.parametrize("kind", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_dependency_mutations_are_compensated(
    tmp_path: Path, kind: str, target: str
) -> None:
    state, events = targets(tmp_path, "succeeded", 1)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def phase47(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        paths = (
            (state, events)
            if target == "both"
            else ((state,) if target == "state" else (events,))
        )
        for path in paths:
            if kind == "replace":
                path.write_bytes(b"secret replacement")
            elif kind == "delete":
                path.unlink()
            elif kind == "truncate":
                path.write_bytes(b"")
            else:
                path.write_bytes(path.read_bytes() + b"secret append")
        return start()

    with pytest.raises(PreparedStepStartPhaseBridgeCompatibilityError) as caught:
        route_prepared_step_start_phase_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_bridge_function=phase47,
        )
    assert caught.value.detail.classification == "start_contract"
    assert "secret" not in str(caught.value)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
def test_dependency_errors_are_safe_and_not_retried(tmp_path: Path, kind: str) -> None:
    state, events = targets(tmp_path, "succeeded", 1)
    before = state.read_bytes(), events.read_bytes()
    calls = 0
    safe = PreparedStepStartBridgeError("safe Phase 47 error")

    def phase47(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        if kind == "safe":
            raise safe
        raise RuntimeError("secret dependency detail")

    expected = (
        PreparedStepStartBridgeError
        if kind == "safe"
        else PreparedStepStartPhaseBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_prepared_step_start_phase_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_bridge_function=phase47,
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
        assert "secret" not in str(caught.value)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    ("part", "field", "value"),
    [
        ("request", "model", "other"),
        ("request", "system_instructions", "other"),
        ("request", "task_instructions", "other"),
        ("request", "allowed_tools", ("other",)),
        ("request", "allowed_tools", ["tool"]),
        ("running", "workflow_id", "other"),
        ("running", "status", "failed"),
        ("running", "current_step_id", "first"),
        ("running", "current_step_index", 1),
        ("running", "current_employee_id", "one"),
        ("running", "completed_step_ids", ()),
        ("running", "last_failure_category", "api_error"),
    ],
)
def test_phase47_return_requires_every_exact_field(
    tmp_path: Path, part: str, field: str, value: object
) -> None:
    returned = start()
    returned = replace(
        returned,
        **{
            "request" if part == "request" else "running_state": replace(
                getattr(returned, "request" if part == "request" else "running_state"),
                **{field: value},
            )
        },
    )
    state, events = targets(tmp_path, "succeeded", 1)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def phase47(*_: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    with pytest.raises(PreparedStepStartPhaseBridgeCompatibilityError) as caught:
        route_prepared_step_start_phase_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_bridge_function=phase47,
        )
    assert caught.value.detail.classification == "start_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("returned", [object(), PreparedStepExecutionStart])
def test_rejects_non_exact_phase47_return_type(
    tmp_path: Path, returned: object
) -> None:
    state, events = targets(tmp_path, "succeeded", 1)
    with pytest.raises(PreparedStepStartPhaseBridgeCompatibilityError) as caught:
        route_prepared_step_start_phase_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_bridge_function=lambda *_: returned,
        )
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("mutation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("outcome", ["normal", "safe", "unexpected"])
def test_rollback_failure_is_sanitized_for_every_target_matrix(
    tmp_path: Path,
    target: str,
    mutation: str,
    outcome: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, events = targets(tmp_path, "succeeded", 1)
    original_write = Path.write_bytes
    original = state.read_bytes(), events.read_bytes()

    def fail_restore(path: Path, data: bytes) -> int:
        if data in original:
            # The fake only changes targets to a distinct marker.
            # Restoration writes the captured original bytes.
            if (target == "both" or target == "state") and path == state:
                raise OSError("secret rollback")
            if (target == "both" or target == "events") and path == events:
                raise OSError("secret rollback")
        return original_write(path, data)

    def phase47(*_: object) -> PreparedStepExecutionStart:
        for path in (
            (state, events)
            if target == "both"
            else ((state,) if target == "state" else (events,))
        ):
            if mutation == "delete":
                path.unlink()
            elif mutation == "truncate":
                path.write_bytes(b"")
            elif mutation == "append":
                path.write_bytes(path.read_bytes() + b"changed")
            else:
                path.write_bytes(b"changed")
        if outcome == "safe":
            raise PreparedStepStartBridgeError("secret safe error")
        if outcome == "unexpected":
            raise RuntimeError("secret unexpected error")
        return start()

    monkeypatch.setattr(Path, "write_bytes", fail_restore)
    with pytest.raises(PreparedStepStartPhaseBridgeCompatibilityError) as caught:
        route_prepared_step_start_phase_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            start_bridge_function=phase47,
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert "secret" not in str(caught.value)
