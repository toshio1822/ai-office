"""Focused Phase 73 strict-boundary tests using injected Phase 59 fakes."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_outcome_routing_phase_bridge_continuation,
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


class PathSubclass(type(Path())):
    pass


def make_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "a", "instructions": "x"},
                {"id": "two", "name": "Two", "employee": "b", "instructions": "y"},
            ],
        }
    )


def setup(
    tmp_path: Path, index: int = 1, status: str = "succeeded", single: bool = False
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    workflow = make_workflow()
    if single:
        workflow = WorkflowDefinition.model_validate(
            {
                "id": "w",
                "name": "W",
                "description": "D",
                "steps": [
                    {"id": "one", "name": "One", "employee": "a", "instructions": "x"}
                ],
            }
        )
        index = 1
    step = workflow.steps[index - 1]
    state = WorkflowExecutionState(
        "w",
        status,
        step.id,
        index,
        step.employee,
        tuple(item.id for item in workflow.steps[:index] if status == "succeeded"),
        None if status == "succeeded" else "api_error",
    )
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
        "output" if status == "succeeded" else None,
        None if status == "succeeded" else "failure",
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_bytes(serialize_runtime_step_event_jsonl(event).encode())
    result = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        step.id,
        index,
        step.employee,
        None if status == "succeeded" else "api_error",
    )
    return result, workflow, state_path, events_path


def complete(
    index: int = 2, step_id: str = "two", employee: str = "b"
) -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        step_id,
        index,
        employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )


def rewrite_json(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n")


def rewrite_events(path: Path, *events: dict[str, object]) -> None:
    path.write_text(
        "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events)
    )


def event_dict(path: Path) -> dict[str, object]:
    return json.loads(path.read_text().splitlines()[0])


def test_success_delegates_once_with_identity_and_returns_exact_decision(
    tmp_path: Path,
):
    result, workflow, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    decision = WorkflowProgressionDecision(
        "prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available"
    )
    calls = []

    def phase59(*args):
        calls.append(args)
        return decision

    returned = route_classified_outcome_routing_phase_bridge_continuation(
        result, workflow, state, events, phase59_function=phase59
    )
    assert returned is decision
    assert calls == [(result, workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == before


def test_final_success_maps_to_exact_completion_decision(tmp_path: Path):
    result, workflow, state, events = setup(tmp_path, single=True)
    decision = complete(index=1, step_id="one", employee="a")
    calls = []
    returned = route_classified_outcome_routing_phase_bridge_continuation(
        result,
        workflow,
        state,
        events,
        phase59_function=lambda *args: calls.append(args) or decision,
    )
    assert returned is decision and len(calls) == 1


def test_failure_and_completion_are_unchanged_zero_call_stops(tmp_path: Path):
    failure, workflow, state, events = setup(tmp_path, status="failed")
    calls = []
    assert (
        route_classified_outcome_routing_phase_bridge_continuation(
            failure,
            workflow,
            state,
            events,
            phase59_function=lambda *args: calls.append(args),
        )
        is failure
    )
    completion_result, workflow, state, events = setup(
        tmp_path / "complete", single=True
    )
    completion_result = complete(index=1, step_id="one", employee="a")
    assert (
        route_classified_outcome_routing_phase_bridge_continuation(
            completion_result,
            workflow,
            state,
            events,
            phase59_function=lambda *args: calls.append(args),
        )
        is completion_result
    )
    assert calls == []


@pytest.mark.parametrize(
    "field",
    [
        "outcome",
        "workflow_id",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "failure_category",
    ],
)
def test_malformed_success_is_rejected_before_dependency(tmp_path: Path, field: str):
    result, workflow, state, events = setup(tmp_path)
    bad = {
        "outcome": "persisted_failure",
        "workflow_id": "bad",
        "current_step_id": "bad",
        "current_step_index": True,
        "current_employee_id": "bad",
        "failure_category": "api_error",
    }[field]
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            replace(result, **{field: bad}),
            workflow,
            state,
            events,
            phase59_function=lambda *_: pytest.fail("called"),
        )
    assert caught.value.detail.classification in {
        "success_contract",
        "failure_contract",
        "outcome_contract",
    }


@pytest.mark.parametrize(
    "value",
    [
        object(),
        WorkflowProgressionDecision(
            "prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "bad"
        ),
    ],
)
def test_unsupported_result_or_malformed_dependency_is_safe(
    tmp_path: Path, value: object
):
    result, workflow, state, events = setup(tmp_path)
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_classified_outcome_routing_phase_bridge_continuation(
            value, workflow, state, events
        )

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=lambda *_: value
        )
    assert caught.value.detail.classification == "progression_contract"


def test_safe_dependency_error_is_preserved_and_unexpected_is_sanitized(tmp_path: Path):
    from ai_office.engine import ClassifiedPersistedOutcomeRoutingPhaseBridgeError

    result, workflow, state, events = setup(tmp_path)
    safe = ClassifiedPersistedOutcomeRoutingPhaseBridgeError("safe")
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeError) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result,
            workflow,
            state,
            events,
            phase59_function=lambda *_: (_ for _ in ()).throw(safe),
        )
    assert caught.value is safe
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result,
            workflow,
            state,
            events,
            phase59_function=lambda *_: (_ for _ in ()).throw(RuntimeError("secret")),
        )
    assert caught.value.detail.classification == "dependency_error"


@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_dependency_mutation_is_compensated_without_retry(tmp_path: Path, target: str):
    result, workflow, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def mutate(*_):
        nonlocal calls
        calls += 1
        if target in ("state", "both"):
            state.write_bytes(b"changed")
        if target in ("events", "both"):
            events.write_bytes(b"changed")
        return object()

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=mutate
        )
    assert caught.value.detail.classification == "outcome_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_exact_models_and_all_outcome_field_types_are_required(tmp_path: Path):
    result, workflow, state, events = setup(tmp_path)
    values = {
        "outcome": SimpleNamespace(value="persisted_success"),
        "workflow_id": SimpleNamespace(value="w"),
        "current_step_id": SimpleNamespace(value="one"),
        "current_step_index": True,
        "current_employee_id": SimpleNamespace(value="a"),
        "failure_category": SimpleNamespace(value=None),
    }
    for field, value in values.items():
        with pytest.raises(
            ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
        ) as caught:
            route_classified_outcome_routing_phase_bridge_continuation(
                replace(result, **{field: value}), workflow, state, events
            )
        assert caught.value.detail.classification == (
            "failure_contract" if field == "outcome" else "success_contract"
        )
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_classified_outcome_routing_phase_bridge_continuation(
            OutcomeSubclass(*result.__dict__.values()), workflow, state, events
        )

    failure, workflow, state, events = setup(tmp_path / "failure", status="failed")
    for field, value in {
        "outcome": SimpleNamespace(value="persisted_failure"),
        "workflow_id": SimpleNamespace(value="w"),
        "current_step_id": SimpleNamespace(value="one"),
        "current_step_index": True,
        "current_employee_id": SimpleNamespace(value="a"),
        "failure_category": SimpleNamespace(value="api_error"),
    }.items():
        with pytest.raises(
            ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
        ) as caught:
            route_classified_outcome_routing_phase_bridge_continuation(
                replace(failure, **{field: value}), workflow, state, events
            )
        assert caught.value.detail.classification == "failure_contract"


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "unsupported"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("next_step_id", None),
        ("next_step_index", None),
        ("next_employee_id", None),
        ("reason", "wrong"),
    ],
)
def test_prepare_next_step_return_checks_every_field(
    tmp_path: Path, field: str, value: object
):
    result, workflow, state, events = setup(tmp_path)
    valid = WorkflowProgressionDecision(
        "prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available"
    )
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result,
            workflow,
            state,
            events,
            phase59_function=lambda *_: replace(valid, **{field: value}),
        )
    assert caught.value.detail.classification == "progression_contract"


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "prepare_next_step"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("next_step_id", "next"),
        ("next_step_index", 1),
        ("next_employee_id", "next"),
        ("reason", "wrong"),
    ],
)
def test_workflow_complete_return_checks_every_field(
    tmp_path: Path, field: str, value: object
):
    result, workflow, state, events = setup(tmp_path, single=True)
    valid = complete(index=1, step_id="one", employee="a")
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result,
            workflow,
            state,
            events,
            phase59_function=lambda *_: replace(valid, **{field: value}),
        )
    assert caught.value.detail.classification == "progression_contract"


def test_decision_subclass_and_unsupported_dependency_decision_are_rejected(
    tmp_path: Path,
):
    result, workflow, state, events = setup(tmp_path)
    subclass = DecisionSubclass(
        "prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available"
    )
    for returned in (subclass, SimpleNamespace(decision="prepare_next_step")):
        with pytest.raises(
            ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
        ) as caught:
            route_classified_outcome_routing_phase_bridge_continuation(
                result, workflow, state, events, phase59_function=lambda *_: returned
            )
        assert caught.value.detail.classification == "progression_contract"


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", SimpleNamespace(value="prepare_next_step")),
        ("workflow_id", SimpleNamespace(value="w")),
        ("current_step_id", SimpleNamespace(value="one")),
        ("current_step_index", 1.0),
        ("current_employee_id", SimpleNamespace(value="a")),
        ("next_step_id", 2),
        ("next_step_index", 2.0),
        ("next_employee_id", 2),
        ("reason", SimpleNamespace(value="next_step_available")),
    ],
)
def test_prepare_next_step_return_requires_exact_field_types(
    tmp_path: Path, field: str, value: object
):
    result, workflow, state, events = setup(tmp_path)
    valid = WorkflowProgressionDecision(
        "prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available"
    )
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result,
            workflow,
            state,
            events,
            phase59_function=lambda *_: replace(valid, **{field: value}),
        )
    assert caught.value.detail.classification == "progression_contract"


@pytest.mark.parametrize(
    "field,value",
    [
        ("outcome", "persisted_success"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("failure_category", "transport_error"),
    ],
)
def test_persisted_failure_stop_checks_every_field_and_terminal_mismatch(
    tmp_path: Path, field: str, value: object
):
    result, workflow, state, events = setup(tmp_path, status="failed")
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            replace(result, **{field: value}), workflow, state, events
        )
    assert caught.value.detail.classification in {
        "success_contract",
        "failure_contract",
        "terminal_contract",
    }
    rewrite_json(state, "last_failure_category", "transport_error")
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events
        )
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "prepare_next_step"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("next_step_id", "next"),
        ("next_step_index", 1),
        ("next_employee_id", "next"),
        ("reason", "wrong"),
    ],
)
def test_workflow_complete_stop_checks_every_field_and_terminal_mismatch(
    tmp_path: Path, field: str, value: object
):
    _, workflow, state, events = setup(tmp_path, single=True)
    supplied = complete(index=1, step_id="one", employee="a")
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            replace(supplied, **{field: value}), workflow, state, events
        )
    assert caught.value.detail.classification == "completion_contract"
    rewrite_json(state, "workflow_id", "other")
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            supplied, workflow, state, events
        )
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize(
    "field",
    [
        "workflow_id",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "completed_step_ids",
    ],
)
def test_succeeded_terminal_state_field_mismatch_is_rejected(
    tmp_path: Path, field: str
):
    result, workflow, state, events = setup(tmp_path)
    values = {
        "workflow_id": "other",
        "current_step_id": "other",
        "current_step_index": 2,
        "current_employee_id": "other",
        "completed_step_ids": [],
    }
    rewrite_json(state, field, values[field])
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events
        )
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("mode", ["missing", "duplicate", "reordered"])
def test_succeeded_terminal_history_missing_duplicate_and_reordered_is_rejected(
    tmp_path: Path, mode: str
):
    result, workflow, state, events = setup(tmp_path)
    event = event_dict(events)
    if mode == "missing":
        events.unlink()
    elif mode == "duplicate":
        rewrite_events(events, event, event)
    else:
        event["step_id"] = "two"
        event["step_index"] = 2
        event["employee_id"] = "b"
        rewrite_events(events, event)
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events
        )
    assert caught.value.detail.classification == (
        "event_target" if mode == "missing" else "terminal_contract"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_type", "step_failed"),
        ("workflow_id", "other"),
        ("step_id", "other"),
        ("step_index", True),
        ("employee_id", "other"),
        ("previous_status", "ready"),
        ("next_status", "failed"),
        ("provider", ""),
        ("failure_category", "api_error"),
        ("response_id", None),
        ("request_id", 1),
        ("output_text", None),
        ("message", "unexpected"),
    ],
)
def test_each_succeeded_terminal_event_field_is_validated(
    tmp_path: Path, field: str, value: object
):
    result, workflow, state, events = setup(tmp_path)
    rewrite_json(events, field, value)
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events
        )
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("target", ["state", "events"])
def test_missing_and_non_regular_targets_are_rejected(tmp_path: Path, target: str):
    result, workflow, state, events = setup(tmp_path)
    path = state if target == "state" else events
    path.unlink()
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events
        )
    assert caught.value.detail.classification == (
        "state_target" if target == "state" else "event_target"
    )

    path.mkdir()
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events
        )
    assert caught.value.detail.classification == (
        "state_target" if target == "state" else "event_target"
    )


@pytest.mark.parametrize(
    "target,operation",
    [
        ("state", "is_file"),
        ("events", "is_file"),
        ("state", "read_bytes"),
        ("events", "read_bytes"),
    ],
)
def test_target_is_file_and_read_bytes_oserrors_are_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    operation: str,
):
    result, workflow, state, events = setup(tmp_path)
    selected = state if target == "state" else events
    original = getattr(Path, operation)

    def fail(path: Path):
        if path == selected:
            raise OSError("hidden detail")
        return original(path)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events
        )
    assert caught.value.detail.classification == (
        "state_target" if target == "state" else "event_target"
    )


def test_non_callable_dependency_exact_path_type_and_target_conflict_are_prevalidated(
    tmp_path: Path,
):
    result, workflow, state, events = setup(tmp_path)
    for bad_function in (None, object()):
        with pytest.raises(
            ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
        ) as caught:
            route_classified_outcome_routing_phase_bridge_continuation(
                result, workflow, state, events, phase59_function=bad_function
            )
        assert caught.value.detail.classification == "dependency_error"
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result,
            workflow,
            PathSubclass(state),
            events,
        )
    assert caught.value.detail.classification == "state_target"
    for bad_state, bad_events, classification in (
        (str(state), events, "state_target"),
        (state, str(events), "event_target"),
        (state, state, "target_conflict"),
        (
            WorkflowSubclass.model_validate(workflow.model_dump()),
            events,
            "workflow_definition",
        ),
    ):
        with pytest.raises(
            ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
        ) as caught:
            route_classified_outcome_routing_phase_bridge_continuation(
                result,
                workflow if classification != "workflow_definition" else bad_state,
                bad_state if classification != "workflow_definition" else state,
                bad_events,
            )
        assert caught.value.detail.classification == classification


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_safe_dependency_error_after_compensation_preserves_identity(
    tmp_path: Path, mutation: str
):
    result, workflow, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    from ai_office.engine import ClassifiedPersistedOutcomeRoutingPhaseBridgeError

    safe = ClassifiedPersistedOutcomeRoutingPhaseBridgeError("safe")

    def phase59(*_):
        if mutation in ("state", "both"):
            state.write_bytes(b"changed")
        if mutation in ("events", "both"):
            events.write_bytes(b"changed")
        raise safe

    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeError) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=phase59
        )
    assert caught.value is safe
    assert (state.read_bytes(), events.read_bytes()) == before


def test_malformed_return_after_mutation_is_compensated(tmp_path: Path):
    result, workflow, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())

    def phase59(*_):
        state.write_bytes(b"changed state")
        events.write_bytes(b"changed events")
        return object()

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=phase59
        )
    assert caught.value.detail.classification == "outcome_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failures_attempt_both_targets_and_do_not_restore_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_target: str
):
    result, workflow, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    original_write = Path.write_bytes
    attempted: list[Path] = []
    failed = {
        state if failed_target in ("state", "both") else None,
        events if failed_target in ("events", "both") else None,
    }

    def phase59(*_):
        original_write(state, b"changed state")
        original_write(events, b"changed events")
        return object()

    def restore(path: Path, contents: bytes) -> int:
        attempted.append(path)
        if path in failed:
            raise OSError("rollback failure")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", restore)
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=phase59
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempted == [state, events]
    assert (state.read_bytes(), events.read_bytes()) == (
        before[0] if failed_target == "events" else b"changed state",
        before[1] if failed_target == "state" else b"changed events",
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
) -> tuple[PersistedExecutionOutcome, WorkflowDefinition, Path, Path]:
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
    result = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        "six",
        6,
        "s",
        None if status == "succeeded" else "api_error",
    )
    return result, definition, state_path, events_path


def test_phase155_success_delegates_once_with_identity_and_exact_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, workflow, state, events = setup_six(tmp_path, "succeeded")
    decision = complete(index=6, step_id="six", employee="s")
    received: list[object] = []

    def phase59(
        result_arg: object,
        workflow_arg: object,
        state_arg: object,
        events_arg: object,
    ) -> object:
        received.extend((result_arg, workflow_arg, state_arg, events_arg))
        return decision

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_outcome_routing_phase_bridge_continuation(
        result, workflow, state, events, phase59_function=phase59
    )
    assert returned is decision
    assert (
        received[0] is result
        and received[1] is workflow
        and received[2] is state
        and received[3] is events
    )
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

    def alt_phase59(*args: object) -> object:
        alt_calls["count"] += 1
        assert len(args) == 4
        assert args[0] is result and args[1] is workflow
        assert args[2] is state and args[3] is events
        return decision

    alt_returned = route_classified_outcome_routing_phase_bridge_continuation(
        result, workflow, state, events, phase59_function=alt_phase59
    )
    assert alt_returned is decision
    assert alt_calls["count"] == 1
    assert (state.read_bytes(), events.read_bytes()) == alt_before
    events.write_bytes(before[1])

    # 2. WorkflowProgressionDecision(workflow_complete) never enters the
    #    Phase-155 fallback: the same empty-predecessor history stays strict.
    complete_value = complete(index=6, step_id="six", employee="s")
    dep_calls = {"count": 0}

    def forbidden(*_: object) -> object:
        dep_calls["count"] += 1
        raise AssertionError("progression dependency must not be called")

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            complete_value, workflow, state, events, phase59_function=forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert dep_calls["count"] == 0
    assert (state.read_bytes(), events.read_bytes()) == before

    # 3. Terminal succeeded response_id="" stays rejected: the fallback does
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
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert dep_calls["count"] == 0
    events.write_bytes(before[1])

    # 4. Final terminal succeeded output_text="" stays rejected.
    lines = events.read_text().splitlines(keepends=True)
    terminal_line = json.loads(lines[-1])
    terminal_line["output_text"] = ""
    events.write_text(
        "".join(lines[:-1])
        + json.dumps(terminal_line, separators=(",", ":"))
        + "\n"
    )
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=forbidden
        )
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
    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=forbidden
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert read_calls.count(state) == 2  # capture + one failing strict read
    assert read_calls.count(events) == 1  # no fallback retry read
    assert dep_calls["count"] == 0
    assert (state.read_bytes(), events.read_bytes()) == before  # fresh read succeeds


def test_phase155_failure_empty_message_is_unchanged_zero_call_stop(
    tmp_path: Path,
) -> None:
    result, workflow, state, events = setup_six(tmp_path, "failed")
    events.write_text(events.read_text().replace('"safe failure"', '""'))
    calls = 0

    def phase59(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_outcome_routing_phase_bridge_continuation(
        result, workflow, state, events, phase59_function=phase59
    )
    assert returned is result
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before


def test_phase155_multiple_earlier_empty_success_delegates_once(
    tmp_path: Path,
) -> None:
    result, workflow, state, events = setup_six(
        tmp_path, "succeeded", earlier_empty=(2, 3)
    )
    decision = complete(index=6, step_id="six", employee="s")
    received: list[object] = []

    def phase59(
        result_arg: object,
        workflow_arg: object,
        state_arg: object,
        events_arg: object,
    ) -> object:
        received.extend((result_arg, workflow_arg, state_arg, events_arg))
        return decision

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_outcome_routing_phase_bridge_continuation(
        result, workflow, state, events, phase59_function=phase59
    )
    assert returned is decision
    assert (
        received[0] is result
        and received[1] is workflow
        and received[2] is state
        and received[3] is events
    )
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_multiple_earlier_empty_failure_is_unchanged_zero_call_stop(
    tmp_path: Path,
) -> None:
    result, workflow, state, events = setup_six(
        tmp_path, "failed", earlier_empty=(2, 3)
    )
    events.write_text(events.read_text().replace('"safe failure"', '""'))
    calls = 0

    def phase59(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    before = (state.read_bytes(), events.read_bytes())
    returned = route_classified_outcome_routing_phase_bridge_continuation(
        result, workflow, state, events, phase59_function=phase59
    )
    assert returned is result
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step2_output_none_is_rejected_before_dependency(
    tmp_path: Path,
) -> None:
    result, workflow, state, events = setup_six(tmp_path, "succeeded")
    lines = events.read_text().splitlines(keepends=True)
    replacement = serialize_runtime_step_event_jsonl(
        six_predecessor("two", 2, output_text=None)
    )
    events.write_text(lines[0] + replacement + "".join(lines[2:]))
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def phase59(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=phase59
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step5_output_non_string_is_rejected_before_dependency(
    tmp_path: Path,
) -> None:
    result, workflow, state, events = setup_six(tmp_path, "succeeded")
    lines = events.read_text().splitlines(keepends=True)
    replacement = serialize_runtime_step_event_jsonl(
        six_predecessor(
            "five", 5, provider="openai", request_id=None, output_text=1
        )
    )
    events.write_text("".join(lines[:4]) + replacement + "".join(lines[5:]))
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def phase59(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(
        ClassifiedOutcomeRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_classified_outcome_routing_phase_bridge_continuation(
            result, workflow, state, events, phase59_function=phase59
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before
