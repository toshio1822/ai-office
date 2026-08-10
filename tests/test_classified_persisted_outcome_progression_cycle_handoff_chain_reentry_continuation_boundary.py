"""Focused tests for the Phase 129 classified progression handoff boundary."""

# ruff: noqa: E501,E701,E702

import inspect
import json
from dataclasses import replace
from pathlib import Path
from pathlib import Path as _TestPath
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationCompatibilityError,
    ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary,
    route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
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
            ],
        }
    )


def predecessor_event(step_id: str, index: int, employee: str) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        index,
        employee,
        "running",
        "succeeded",
        "openai",
        None,
        f"response-{step_id}",
        f"request-{step_id}",
        f"output-{step_id}",
        None,
    )


def terminal_event(
    supplied_workflow: WorkflowDefinition,
    index: int,
    status: str,
    **changes: object,
) -> RuntimeStepEvent:
    step = supplied_workflow.steps[index - 1]
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
        f"response-{step.id}" if status == "succeeded" else None,
        f"request-{step.id}",
        f"output-{step.id}" if status == "succeeded" else None,
        None if status == "succeeded" else "safe failure",
    )
    return replace(event, **changes)


def setup(
    tmp_path: Path,
    *,
    status: str = "succeeded",
    index: int = 3,
) -> tuple[
    object,
    WorkflowDefinition,
    Path,
    Path,
    bytes,
    bytes,
]:
    supplied_workflow = workflow()
    current = supplied_workflow.steps[index - 1]
    completed = (
        tuple(step.id for step in supplied_workflow.steps[:index])
        if status == "succeeded"
        else tuple(step.id for step in supplied_workflow.steps[: index - 1])
    )
    state_model = WorkflowExecutionState(
        "w",
        status,
        current.id,
        index,
        current.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )
    prior = tuple(
        predecessor_event(step.id, position, step.employee)
        for position, step in enumerate(supplied_workflow.steps[: index - 1], 1)
    )
    events = (*prior, terminal_event(supplied_workflow, index, status))
    state_bytes = serialize_workflow_execution_state_json(state_model).encode("utf-8")
    event_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result: object = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        current.id,
        index,
        current.employee,
        None if status == "succeeded" else "api_error",
    )
    return result, supplied_workflow, state_path, events_path, state_bytes, event_bytes


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        "four",
        4,
        "d",
        None,
        None,
        None,
        "last_step_succeeded",
    )


def expected_decision(
    supplied_workflow: WorkflowDefinition, index: int
) -> WorkflowProgressionDecision:
    if index == len(supplied_workflow.steps):
        step = supplied_workflow.steps[index - 1]
        return WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            step.id,
            index,
            step.employee,
            None,
            None,
            None,
            "last_step_succeeded",
        )
    current = supplied_workflow.steps[index - 1]
    following = supplied_workflow.steps[index]
    return WorkflowProgressionDecision(
        "prepare_next_step",
        "w",
        current.id,
        index,
        current.employee,
        following.id,
        index + 1,
        following.employee,
        "next_step_available",
    )


def failure_stop() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_failure", "w", "three", 3, "c", "api_error"
    )


def classification(
    caught: pytest.ExceptionInfo[
        ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationCompatibilityError
    ],
) -> str:
    return caught.value.detail.classification


def call(
    result: object,
    supplied_workflow: object,
    state: object,
    events: object,
    dependency: object,
) -> object:
    return route_classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary(
        result,
        supplied_workflow,
        state,
        events,
        phase122_function=dependency,
    )


def assert_rejected(
    result: object,
    supplied_workflow: object,
    state: object,
    events: object,
    expected: str,
    dependency: object,
) -> None:
    with pytest.raises(
        ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationCompatibilityError
    ) as caught:
        call(result, supplied_workflow, state, events, dependency)
    assert classification(caught) == expected


def rewrite_event(events: Path, index: int, **changes: object) -> None:
    lines = events.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[index])
    payload.update(changes)
    lines[index] = json.dumps(payload, separators=(",", ":"))
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_public_signature_and_default_identity() -> None:
    signature = inspect.signature(
        route_classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary
    )
    parameters = list(signature.parameters.values())
    assert [parameter.name for parameter in parameters] == [
        "result",
        "workflow",
        "state_path",
        "events_path",
        "phase122_function",
    ]
    assert all(parameter.annotation is object for parameter in parameters[:4])
    assert [parameter.kind for parameter in parameters[:4]] == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD
    ] * 4
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert (
        parameters[4].default
        is route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary
    )


def test_source_audit_uses_only_public_phase122_dependency() -> None:
    source = Path(
        "src/ai_office/engine/"
        "classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert (
        "route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary"
        in source
    )
    assert "phase115" not in source.lower()
    assert "route_classified_persisted_outcome_progression_cycle_reentry_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


@pytest.mark.parametrize("index", [1, 2], ids=["index-one", "index-two"])
def test_success_continuation_index_below_three_is_rejected_before_phase122(
    tmp_path: Path, index: int
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path, index=index)
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(result, supplied_workflow, state, events, "success_contract", forbidden)
    assert calls == 0


@pytest.mark.parametrize("index", [3, 4], ids=["intermediate", "final"])
def test_success_routes_delegate_once_in_canonical_order_and_return_identity(
    tmp_path: Path, index: int
) -> None:
    result, supplied_workflow, state, events, before_state, before_events = setup(
        tmp_path, index=index
    )
    decision = expected_decision(supplied_workflow, index)
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        return decision

    returned = call(result, supplied_workflow, state, events, dependency)
    assert returned is decision
    assert calls == [(result, supplied_workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_empty_success_terminal_output_is_accepted_by_phase129_fallback(
    tmp_path: Path,
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path)
    rewrite_event(events, 2, output_text="")
    before_state, before_events = state.read_bytes(), events.read_bytes()
    decision = expected_decision(supplied_workflow, 3)
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        return decision

    assert call(result, supplied_workflow, state, events, dependency) is decision
    assert calls == [(result, supplied_workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    ("state_field", "event_field"),
    [("current_step_id", "step_id"), ("current_employee_id", "employee_id")],
)
def test_empty_success_fallback_keeps_workflow_current_step_linkage(
    tmp_path: Path, state_field: str, event_field: str
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path, index=4)
    rewrite_event(events, 3, output_text="", **{event_field: "wrong"})
    exact_state = WorkflowExecutionState(
        "w", "succeeded", "four", 4, "d", ("one", "two", "three", "four"), None
    )
    state.write_bytes(
        serialize_workflow_execution_state_json(
            replace(exact_state, **{state_field: "wrong"})
        ).encode("utf-8")
    )
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        supplied_workflow,
        state,
        events,
        "terminal_contract",
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize("route", ["persisted_failure", "workflow_complete"])
def test_stop_routes_return_exact_identity_and_make_zero_phase122_calls(
    tmp_path: Path, route: str
) -> None:
    if route == "persisted_failure":
        result, supplied_workflow, state, events, before_state, before_events = setup(
            tmp_path, status="failed"
        )
    else:
        result = completion()
        (
            _success,
            supplied_workflow,
            state,
            events,
            _,
            _,
        ) = setup(tmp_path, index=4)
        before_state, before_events = state.read_bytes(), events.read_bytes()

    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase 122 must not be called")

    returned = call(result, supplied_workflow, state, events, forbidden)
    assert returned is result
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_workflow_complete_stop_rejects_empty_success_output_in_phase129(
    tmp_path: Path,
) -> None:
    result = completion()
    _, supplied_workflow, state, events, *_ = setup(tmp_path, index=4)
    rewrite_event(events, 3, output_text="")
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        supplied_workflow,
        state,
        events,
        "terminal_contract",
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize(
    "bad",
    [
        object(),
        SimpleNamespace(outcome="persisted_success"),
        SimpleNamespace(
            outcome="persisted_success",
            workflow_id="w",
            current_step_id="three",
            current_step_index=3,
            current_employee_id="c",
            failure_category=None,
        ),
        OutcomeChild("persisted_success", "w", "three", 3, "c", None),
        DecisionChild(
            "workflow_complete", "w", "four", 4, "d", None, None, None, "last_step_succeeded"
        ),
        WorkflowExecutionPersistenceResult(Path("state"), Path("events"), 1, 1),
        WorkflowProgressionDecision(
            "prepare_next_step", "w", "three", 3, "c", "four", 4, "d", "next_step_available"
        ),
    ],
)
def test_only_exact_result_models_are_accepted_before_phase122(
    tmp_path: Path, bad: object
) -> None:
    _, supplied_workflow, state, events, *_ = setup(tmp_path)
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    expected = (
        "completion_contract"
        if type(bad) is WorkflowProgressionDecision
        else "result_type"
    )
    assert_rejected(bad, supplied_workflow, state, events, expected, forbidden)
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", "persisted_failure"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(3)),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("failure_category", "api_error"),
    ],
)
def test_persisted_success_fields_are_exact_and_linked(
    tmp_path: Path, field: str, value: object
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path)
    assert isinstance(result, PersistedExecutionOutcome)
    bad = replace(result, **{field: value})
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    expected = "failure_contract" if value == "persisted_failure" else "success_contract"
    assert_rejected(bad, supplied_workflow, state, events, expected, forbidden)
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", "persisted_success"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(3)),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("failure_category", None),
    ],
)
def test_persisted_failure_fields_are_exact_and_linked(
    tmp_path: Path, field: str, value: object
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path, status="failed")
    assert isinstance(result, PersistedExecutionOutcome)
    bad = replace(result, **{field: value})
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    expected = "success_contract" if value == "persisted_success" else "failure_contract"
    assert_rejected(bad, supplied_workflow, state, events, expected, forbidden)
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "prepare_next_step"),
        ("workflow_id", "other"),
        ("current_step_id", "three"),
        ("current_step_index", IntChild(4)),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("next_step_id", "four"),
        ("next_step_index", 4),
        ("next_employee_id", "d"),
        ("reason", "wrong"),
    ],
)
def test_workflow_complete_stop_fields_are_strict_and_zero_call(
    tmp_path: Path, field: str, value: object
) -> None:
    _, supplied_workflow, state, events, *_ = setup(tmp_path, index=4)
    bad = replace(completion(), **{field: value})
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(bad, supplied_workflow, state, events, "completion_contract", forbidden)
    assert calls == 0


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        (WorkflowChild.model_validate(workflow().model_dump()), "workflow_definition"),
        (
            SimpleNamespace(
                id="w",
                name="W",
                description="D",
                steps=workflow().steps,
            ),
            "workflow_definition",
        ),
    ],
)
def test_workflow_subclass_and_attribute_substitute_are_rejected(
    tmp_path: Path, supplied: object, expected: str
) -> None:
    result, _, state, events, *_ = setup(tmp_path)
    assert_rejected(result, supplied, state, events, expected, lambda *_: object())


def test_workflow_step_attribute_substitute_is_rejected_before_phase122(
    tmp_path: Path,
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path)
    substitute = SimpleNamespace(
        id="one", name="One", employee="a", instructions="one"
    )
    supplied = supplied_workflow.model_copy(
        update={"steps": [substitute, *supplied_workflow.steps[1:]]}
    )
    assert type(supplied) is WorkflowDefinition
    assert_rejected(
        result,
        supplied,
        state,
        events,
        "workflow_definition",
        lambda *_: pytest.fail("called"),
    )


@pytest.mark.parametrize("target", ["state", "events"])
def test_path_subclass_missing_and_directory_targets_are_rejected_before_phase122(
    tmp_path: Path, target: str
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path)
    selected = state if target == "state" else events
    supplied_state: object = PathChild(state) if target == "state" else state
    supplied_events: object = PathChild(events) if target == "events" else events
    assert_rejected(
        result,
        supplied_workflow,
        supplied_state,
        supplied_events,
        f"{'event' if target == 'events' else target}_target",
        lambda *_: pytest.fail("called"),
    )
    selected.unlink()
    assert_rejected(
        result,
        supplied_workflow,
        state,
        events,
        f"{'event' if target == 'events' else target}_target",
        lambda *_: pytest.fail("called"),
    )
    selected.mkdir()
    assert_rejected(
        result,
        supplied_workflow,
        state,
        events,
        f"{'event' if target == 'events' else target}_target",
        lambda *_: pytest.fail("called"),
    )


@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
@pytest.mark.parametrize("target", ["state", "events"])
def test_target_oserrors_are_safely_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    operation: str,
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path)
    selected = state if target == "state" else events
    original = getattr(_TestPath, operation)

    def fail(path: Path, *args: object, **kwargs: object):
        if path == selected:
            raise OSError("target-secret")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(_TestPath, operation, fail)
    assert_rejected(
        result,
        supplied_workflow,
        state,
        events,
        f"{'event' if target == 'events' else target}_target",
        lambda *_: pytest.fail("called"),
    )


@pytest.mark.parametrize(
    "mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"]
)
def test_predecessor_and_terminal_history_matrix_is_rejected_before_phase122(
    tmp_path: Path, mode: str
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path)
    lines = events.read_text(encoding="utf-8").splitlines()
    if mode == "duplicate":
        lines = [lines[0], lines[0], lines[1], lines[2]]
    elif mode == "missing":
        lines = []
    elif mode == "reordered":
        lines = [lines[1], lines[0], lines[2]]
    elif mode == "unrelated":
        payload = json.loads(lines[1])
        payload["workflow_id"] = "other"
        lines[1] = json.dumps(payload, separators=(",", ":"))
    elif mode == "malformed":
        events.write_bytes(b"{not-json}\n")
        lines = []
    elif mode == "extra":
        lines = [*lines, lines[-1]]
    if mode != "malformed":
        events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result, supplied_workflow, state, events, "terminal_contract", forbidden
    )
    assert calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_id": "other"},
        {"step_id": "other"},
        {"step_index": 4},
        {"employee_id": "other"},
        {"provider": 4},
        {"provider": "other"},
        {"request_id": 4},
        {"request_id": ""},
        {"response_id": None},
        {"output_text": None},
        {"event_type": "step_failed", "next_status": "failed"},
        {"next_status": "failed"},
        {"failure_category": "api_error"},
    ],
)
def test_success_terminal_event_linkage_is_rejected_before_phase122(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path)
    rewrite_event(events, 2, **changes)
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result, supplied_workflow, state, events, "terminal_contract", forbidden
    )
    assert calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_id": "other"},
        {"step_id": "other"},
        {"step_index": 4},
        {"employee_id": "other"},
        {"provider": 4},
        {"request_id": 4},
        {"request_id": ""},
        {"failure_category": None},
        {"message": None},
        {"event_type": "step_succeeded", "next_status": "succeeded"},
        {"next_status": "succeeded"},
    ],
)
def test_failure_terminal_event_linkage_is_rejected_before_phase122(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path, status="failed")
    rewrite_event(events, 2, **changes)
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result, supplied_workflow, state, events, "terminal_contract", forbidden
    )
    assert calls == 0


def test_mismatched_persisted_terminal_state_is_rejected_before_phase122(
    tmp_path: Path,
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path)
    bad_state = WorkflowExecutionState(
        "w", "failed", "three", 3, "c", ("one", "two"), "api_error"
    )
    state.write_bytes(serialize_workflow_execution_state_json(bad_state).encode("utf-8"))
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result, supplied_workflow, state, events, "terminal_contract", forbidden
    )
    assert calls == 0


@pytest.mark.parametrize("index", [3, 4], ids=["prepare-next", "workflow-complete"])
def test_exact_phase122_decisions_are_accepted_by_identity(
    tmp_path: Path, index: int
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path, index=index)
    decision = expected_decision(supplied_workflow, index)
    calls = 0

    def dependency(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        return decision

    returned = call(result, supplied_workflow, state, events, dependency)
    assert returned is decision
    assert calls == 1


@pytest.mark.parametrize(
    "bad",
    [
        object(),
        SimpleNamespace(decision="prepare_next_step"),
        SimpleNamespace(
            decision="prepare_next_step",
            workflow_id="w",
            current_step_id="three",
            current_step_index=3,
            current_employee_id="c",
            next_step_id="four",
            next_step_index=4,
            next_employee_id="d",
            reason="next_step_available",
        ),
        DecisionChild(
            "prepare_next_step", "w", "three", 3, "c", "four", 4, "d", "next_step_available"
        ),
        PersistedExecutionOutcome("persisted_success", "w", "three", 3, "c", None),
    ],
)
def test_phase122_return_must_be_exact_supported_progression_decision(
    tmp_path: Path, bad: object
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    assert_rejected(
        result, supplied_workflow, state, events, "progression_contract", dependency
    )
    assert calls == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "workflow_complete"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(3)),
        ("current_employee_id", "other"),
        ("next_step_id", "wrong"),
        ("next_step_index", 5),
        ("next_step_index", True),
        ("next_step_index", IntChild(4)),
        ("next_employee_id", "wrong"),
        ("reason", "wrong"),
    ],
)
def test_prepare_next_step_return_contract_is_revalidated(
    tmp_path: Path, field: str, value: object
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path)
    decision = replace(expected_decision(supplied_workflow, 3), **{field: value})
    calls = 0

    def dependency(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        return decision

    assert_rejected(
        result, supplied_workflow, state, events, "progression_contract", dependency
    )
    assert calls == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "prepare_next_step"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(4)),
        ("current_employee_id", "other"),
        ("next_step_id", "four"),
        ("next_step_index", 4),
        ("next_employee_id", "d"),
        ("reason", "wrong"),
    ],
)
def test_workflow_complete_return_contract_is_revalidated(
    tmp_path: Path, field: str, value: object
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path, index=4)
    decision = replace(expected_decision(supplied_workflow, 4), **{field: value})
    calls = 0

    def dependency(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        return decision

    assert_rejected(
        result, supplied_workflow, state, events, "progression_contract", dependency
    )
    assert calls == 1


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_phase122_error_identity_and_compensation(
    tmp_path: Path, mutation: str
) -> None:
    result, supplied_workflow, state, events, before_state, before_events = setup(tmp_path)
    safe_error = ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationError(
        "progression_contract"
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        raise safe_error

    with pytest.raises(
        ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationError
    ) as caught:
        call(result, supplied_workflow, state, events, dependency)
    assert caught.value is safe_error
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_phase122_error_is_sanitized_and_compensated(
    tmp_path: Path, mutation: str
) -> None:
    result, supplied_workflow, state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        raise RuntimeError("secret detail")

    with pytest.raises(
        ClassifiedPersistedOutcomeProgressionCycleHandoffChainReentryContinuationCompatibilityError
    ) as caught:
        call(result, supplied_workflow, state, events, dependency)
    assert classification(caught) == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_normal_phase122_target_mutation_is_compensated_without_retry(
    tmp_path: Path, mutation: str
) -> None:
    result, supplied_workflow, state, events, before_state, before_events = setup(tmp_path)
    decision = expected_decision(supplied_workflow, 3)
    calls = 0

    def dependency(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        return decision

    assert_rejected(
        result, supplied_workflow, state, events, "progression_contract", dependency
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("failed_targets", [{"state"}, {"events"}, {"state", "events"}])
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_targets: set[str],
) -> None:
    result, supplied_workflow, state, events, before_state, before_events = setup(tmp_path)
    original_write = _TestPath.write_bytes
    attempts: list[str] = []
    armed = False
    calls = 0

    def write(path: Path, data: bytes) -> int:
        nonlocal armed
        if armed and data == before_state:
            attempts.append("state")
            if "state" in failed_targets:
                raise OSError("state rollback secret")
        if armed and data == before_events:
            attempts.append("events")
            if "events" in failed_targets:
                raise OSError("events rollback secret")
        return original_write(path, data)

    monkeypatch.setattr(_TestPath, "write_bytes", write)
    decision = expected_decision(supplied_workflow, 3)

    def dependency(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls, armed
        calls += 1
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        armed = True
        return decision

    assert_rejected(
        result, supplied_workflow, state, events, "dependency_rollback", dependency
    )
    assert calls == 1
    assert attempts.count("state") == 1
    assert attempts.count("events") == 1
    assert "rollback secret" not in str(attempts)


def test_same_target_conflict_and_noncallable_dependency_are_zero_call(
    tmp_path: Path,
) -> None:
    result, supplied_workflow, state, events, *_ = setup(tmp_path)
    assert_rejected(
        result,
        supplied_workflow,
        state,
        state,
        "target_conflict",
        lambda *_: pytest.fail("called"),
    )
    assert_rejected(
        result,
        supplied_workflow,
        state,
        events,
        "dependency_error",
        None,
    )
