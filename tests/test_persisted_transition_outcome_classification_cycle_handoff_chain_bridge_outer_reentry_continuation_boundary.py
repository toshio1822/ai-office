"""Focused Phase 143 persisted-transition outcome-classification cycle handoff chain bridge outer tests."""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import PersistedExecutionOutcome, WorkflowProgressionDecision
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as OuterCompatibilityError,
    route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as public_phase143,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationError as Phase135Error,
    route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary as public_phase135,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.invocation import ModelInvocationFailure, ModelInvocationRequest, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    WorkflowExecutionPersistenceResult,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class WorkflowChild(WorkflowDefinition):
    pass


class StepChild(WorkflowStepDefinition):
    pass


class PersistenceChild(WorkflowExecutionPersistenceResult):
    pass


class IntChild(int):
    pass


class DecisionChild(WorkflowProgressionDecision):
    pass


class OutcomeChild(PersistedExecutionOutcome):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "e", "instructions": "two"},
                {"id": "three", "name": "Three", "employee": "e", "instructions": "three"},
                {"id": "four", "name": "Four", "employee": "e", "instructions": "four"},
                {"id": "five", "name": "Five", "employee": "e", "instructions": "five"},
            ],
        }
    )


def predecessor_event(
    step_id: str, step_index: int, provider: object = "openai", **changes: object
) -> RuntimeStepEvent:
    return replace(
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            step_id,
            step_index,
            "e",
            "running",
            "succeeded",
            provider,  # type: ignore[arg-type]
            None,
            f"response-{step_id}",
            f"request-{step_id}",
            f"output-{step_id}",
            None,
        ),
        **changes,
    )


def terminal_event(status: str, **changes: object) -> RuntimeStepEvent:
    if status == "succeeded":
        event = RuntimeStepEvent(
            "step_succeeded",
            "w",
            "five",
            5,
            "e",
            "running",
            "succeeded",
            "openai",
            None,
            "response-five",
            "request-five",
            "output-five",
            None,
        )
    else:
        event = RuntimeStepEvent(
            "step_failed",
            "w",
            "five",
            5,
            "e",
            "running",
            "failed",
            "openai",
            "api_error",
            None,
            "request-five",
            None,
            "safe failure",
        )
    return replace(event, **changes)


def write_terminal_targets(
    tmp_path: Path, status: str, *, provider: object = "openai"
) -> tuple[Path, Path, bytes, bytes]:
    state = WorkflowExecutionState(
        "w",
        status,
        "five",
        5,
        "e",
        (
            ("one", "two", "three", "four", "five")
            if status == "succeeded"
            else ("one", "two", "three", "four")
        ),
        None if status == "succeeded" else "api_error",
    )
    predecessors = tuple(
        predecessor_event(step.id, index, "other" if index < 4 else "openai")
        for index, step in enumerate(workflow().steps[:4], 1)
    )
    terminal = terminal_event(status, provider=provider)
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def persistence_result(state: Path, events: Path) -> WorkflowExecutionPersistenceResult:
    terminal_line = events.read_bytes().splitlines(keepends=True)[-1]
    return WorkflowExecutionPersistenceResult(
        state, events, len(state.read_bytes()), len(terminal_line)
    )


def values(tmp_path: Path, status: str = "succeeded") -> dict[str, object]:
    state, events, before_state, before_events = write_terminal_targets(tmp_path, status)
    return {
        "result": persistence_result(state, events),
        "workflow": workflow(),
        "state_path": state,
        "events_path": events,
        "before_state": before_state,
        "before_events": before_events,
    }


def expected_outcome(status: str = "succeeded") -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        "five",
        5,
        "e",
        None if status == "succeeded" else "api_error",
    )


def phase135_fake(
    result: object, _workflow: object, state_path: Path, events_path: Path
) -> PersistedExecutionOutcome:
    assert type(result) is WorkflowExecutionPersistenceResult
    assert result.state_path is state_path and result.events_path is events_path
    state = load_workflow_execution_state(state_path)
    succeeded = state.status == "succeeded"
    return PersistedExecutionOutcome(
        "persisted_success" if succeeded else "persisted_failure",
        state.workflow_id,
        state.current_step_id,
        state.current_step_index,
        state.current_employee_id,
        None if succeeded else state.last_failure_category,
    )


def _arguments(data: dict[str, object]) -> dict[str, object]:
    return {key: data[key] for key in ("result", "workflow", "state_path", "events_path")}


def call(data: dict[str, object], dependency: object) -> object:
    supplied = _arguments(data)
    supplied["phase135_function"] = dependency
    return public_phase143(**supplied)  # type: ignore[arg-type]


def reject(data: dict[str, object], classification: str, **changes: object) -> None:
    supplied = _arguments(data)
    supplied.update(changes)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    supplied["phase135_function"] = dependency
    with pytest.raises(OuterCompatibilityError) as caught:
        public_phase143(**supplied)  # type: ignore[arg-type]
    assert caught.value.detail.classification == classification
    assert calls == 0


def reject_after_call(
    data: dict[str, object], dependency: object, classification: str
) -> None:
    before = (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    )
    calls = 0

    def counted(*args: object) -> object:
        nonlocal calls
        calls += 1
        return dependency(*args)  # type: ignore[operator]

    supplied = _arguments(data)
    supplied["phase135_function"] = counted
    with pytest.raises(OuterCompatibilityError) as caught:
        public_phase143(**supplied)  # type: ignore[arg-type]
    assert caught.value.detail.classification == classification
    assert calls == 1
    assert (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    ) == before


def stop_values(
    tmp_path: Path, kind: str, provider: object = "other"
) -> tuple[dict[str, object], object]:
    state, events, before_state, before_events = write_terminal_targets(
        tmp_path, "succeeded" if kind == "complete" else "failed", provider=provider
    )
    result: object = (
        WorkflowProgressionDecision(
            "workflow_complete", "w", "five", 5, "e", None, None, None, "last_step_succeeded"
        )
        if kind == "complete"
        else PersistedExecutionOutcome("persisted_failure", "w", "five", 5, "e", "api_error")
    )
    return {
        "result": result,
        "workflow": workflow(),
        "state_path": state,
        "events_path": events,
        "before_state": before_state,
        "before_events": before_events,
    }, result


def assert_unchanged(data: dict[str, object]) -> None:
    assert (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    ) == (data["before_state"], data["before_events"])


def test_public_signature_and_source_audit() -> None:
    function = public_phase143
    params = list(inspect.signature(function).parameters.values())
    assert [param.name for param in params[:4]] == [
        "result",
        "workflow",
        "state_path",
        "events_path",
    ]
    assert all(param.annotation is object for param in params[:4])
    assert all(
        param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for param in params[:4]
    )
    assert params[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert params[4].name == "phase135_function"
    assert params[4].default is public_phase135
    source = Path(
        "src/ai_office/engine/persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary" in source
    assert "phase128" not in source.lower()
    assert "route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary" not in source
    assert "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary" not in source
    assert "route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary" not in source
    assert "route_runtime_result_transition_persistence" not in source
    assert "route_classified_persisted_outcome_reentry" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_valid_classification_routes_call_phase135_once_canonically_and_preserve_identity(
    tmp_path: Path, status: str
) -> None:
    data = values(tmp_path, status)
    expected = expected_outcome(status)
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        assert tuple(args) == (
            data["result"],
            data["workflow"],
            data["state_path"],
            data["events_path"],
        )
        return expected

    assert call(data, dependency) is expected
    assert calls == [
        (data["result"], data["workflow"], data["state_path"], data["events_path"])
    ]
    assert_unchanged(data)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_immediate_predecessor_empty_output_text_delegates_once_canonical_order(
    tmp_path: Path, status: str
) -> None:
    data = values(tmp_path, status)
    events = data["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("four", 4, "openai", output_text="")
    )
    events.write_text("".join(lines[:3]) + replacement + "".join(lines[4:]), encoding="utf-8")  # type: ignore[union-attr]
    expected = expected_outcome(status)
    seen: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        seen.append(args)
        return expected

    assert call(data, dependency) is expected
    assert len(seen) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            seen[0],
            tuple(
                data[key] for key in ("result", "workflow", "state_path", "events_path")
            ),
            strict=True,
        )
    )


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_earlier_empty_output_text_survives_later_succeeded_predecessor(
    tmp_path: Path, status: str
) -> None:
    data = values(tmp_path, status)
    events = data["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    earlier = serialize_runtime_step_event_jsonl(
        predecessor_event("one", 1, "other", output_text="")
    )
    events.write_text(earlier + "".join(lines[1:]), encoding="utf-8")  # type: ignore[union-attr]
    expected = expected_outcome(status)

    def dependency(*args: object) -> object:
        return expected

    assert call(data, dependency) is expected


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_predecessor_nonempty_output_text_remains_accepted(
    tmp_path: Path, status: str
) -> None:
    data = values(tmp_path, status)
    assert call(data, phase135_fake) == expected_outcome(status)


@pytest.mark.parametrize("output_text", [4, None, ["output"]])
def test_predecessor_output_text_non_string_is_rejected(
    tmp_path: Path, output_text: object
) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("four", 4, "openai", output_text=output_text)
    )
    events.write_text("".join(lines[:3]) + replacement + "".join(lines[4:]), encoding="utf-8")  # type: ignore[union-attr]
    reject(data, "persistence_contract")


@pytest.mark.parametrize("response_id", [None, ""])
def test_predecessor_empty_output_text_still_requires_response_id(
    tmp_path: Path, response_id: object
) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("four", 4, "openai", output_text="", response_id=response_id)
    )
    events.write_text("".join(lines[:3]) + replacement + "".join(lines[4:]), encoding="utf-8")  # type: ignore[union-attr]
    reject(data, "persistence_contract")


@pytest.mark.parametrize("request_id", [None, ""])
def test_predecessor_empty_output_text_still_requires_request_id(
    tmp_path: Path, request_id: object
) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("four", 4, "openai", output_text="", request_id=request_id)
    )
    events.write_text("".join(lines[:3]) + replacement + "".join(lines[4:]), encoding="utf-8")  # type: ignore[union-attr]
    if request_id is None:
        expected = expected_outcome()
        seen: list[tuple[object, ...]] = []
        rewritten = (
            data["state_path"].read_bytes(),  # type: ignore[union-attr]
            data["events_path"].read_bytes(),  # type: ignore[union-attr]
        )

        def dependency(*args: object) -> object:
            seen.append(args)
            return expected

        assert call(data, dependency) is expected
        assert len(seen) == 1
        assert all(
            actual is wanted
            for actual, wanted in zip(
                seen[0],
                tuple(
                    data[key]
                    for key in ("result", "workflow", "state_path", "events_path")
                ),
                strict=True,
            )
        )
        assert (
            data["state_path"].read_bytes(),  # type: ignore[union-attr]
            data["events_path"].read_bytes(),  # type: ignore[union-attr]
        ) == rewritten
    else:
        reject(data, "persistence_contract")


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_immediate_predecessor_empty_output_text_still_requires_openai_provider(
    tmp_path: Path, status: str
) -> None:
    data = values(tmp_path, status)
    events = data["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("four", 4, "other", output_text="")
    )
    events.write_text("".join(lines[:3]) + replacement + "".join(lines[4:]), encoding="utf-8")  # type: ignore[union-attr]
    reject(data, "persistence_contract")


@pytest.mark.parametrize("provider", ["other", 4])
def test_immediate_predecessor_provider_contract_is_strict(
    tmp_path: Path, provider: object
) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("four", 4, provider)
    ).encode()
    events.write_bytes(b"".join(lines[:3]) + replacement + lines[4])  # type: ignore[union-attr]
    reject(data, "persistence_contract")


@pytest.mark.parametrize("request_id", [None, "", 4])
def test_predecessor_request_id_contract_is_strict(
    tmp_path: Path, request_id: object
) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("four", 4, "openai", request_id=request_id)
    ).encode()
    events.write_bytes(b"".join(lines[:3]) + replacement + lines[4])  # type: ignore[union-attr]
    if request_id is None:
        expected = expected_outcome()
        seen: list[tuple[object, ...]] = []
        rewritten = (
            data["state_path"].read_bytes(),  # type: ignore[union-attr]
            data["events_path"].read_bytes(),  # type: ignore[union-attr]
        )

        def dependency(*args: object) -> object:
            seen.append(args)
            return expected

        assert call(data, dependency) is expected
        assert len(seen) == 1
        assert all(
            actual is wanted
            for actual, wanted in zip(
                seen[0],
                tuple(
                    data[key]
                    for key in ("result", "workflow", "state_path", "events_path")
                ),
                strict=True,
            )
        )
        assert (
            data["state_path"].read_bytes(),  # type: ignore[union-attr]
            data["events_path"].read_bytes(),  # type: ignore[union-attr]
        ) == rewritten
    else:
        reject(data, "persistence_contract")


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_immediate_predecessor_none_request_id_empty_output_delegates(
    tmp_path: Path, status: str
) -> None:
    data = values(tmp_path, status)
    events = data["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("four", 4, "openai", output_text="", request_id=None)
    )
    events.write_text("".join(lines[:3]) + replacement + "".join(lines[4:]), encoding="utf-8")  # type: ignore[union-attr]
    expected = expected_outcome(status)
    seen: list[tuple[object, ...]] = []
    rewritten = (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    )

    def dependency(*args: object) -> object:
        seen.append(args)
        return expected

    assert call(data, dependency) is expected
    assert len(seen) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            seen[0],
            tuple(
                data[key] for key in ("result", "workflow", "state_path", "events_path")
            ),
            strict=True,
        )
    )
    assert (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    ) == rewritten


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_immediate_predecessor_none_request_id_nonempty_output_delegates(
    tmp_path: Path, status: str
) -> None:
    data = values(tmp_path, status)
    events = data["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("four", 4, "openai", request_id=None)
    )
    events.write_text("".join(lines[:3]) + replacement + "".join(lines[4:]), encoding="utf-8")  # type: ignore[union-attr]
    expected = expected_outcome(status)
    seen: list[tuple[object, ...]] = []
    rewritten = (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    )

    def dependency(*args: object) -> object:
        seen.append(args)
        return expected

    assert call(data, dependency) is expected
    assert len(seen) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            seen[0],
            tuple(
                data[key] for key in ("result", "workflow", "state_path", "events_path")
            ),
            strict=True,
        )
    )
    assert (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    ) == rewritten


def test_earlier_predecessor_none_request_id_is_rejected_before_phase135(
    tmp_path: Path,
) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("two", 2, "other", request_id=None)
    ).encode()
    events.write_bytes(b"".join(lines[:1]) + replacement + b"".join(lines[2:]))  # type: ignore[union-attr]
    reject(data, "persistence_contract")


def test_immediate_predecessor_empty_request_id_is_rejected(tmp_path: Path) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("four", 4, "openai", request_id="")
    ).encode()
    events.write_bytes(b"".join(lines[:3]) + replacement + b"".join(lines[4:]))  # type: ignore[union-attr]
    reject(data, "persistence_contract")


def test_exact_persistence_result_model_is_required(tmp_path: Path) -> None:
    data = values(tmp_path)
    result = data["result"]  # type: ignore[assignment]
    substitute = SimpleNamespace(
        state_path=result.state_path,
        events_path=result.events_path,
        state_bytes_written=result.state_bytes_written,
        event_bytes_appended=result.event_bytes_appended,
    )
    persistence_child = PersistenceChild(
        result.state_path,
        result.events_path,
        result.state_bytes_written,
        result.event_bytes_appended,
    )
    for bad in (persistence_child, substitute):
        reject(data, "result_type", result=bad)


def test_exact_workflow_and_step_models_are_required(tmp_path: Path) -> None:
    data = values(tmp_path)
    child = WorkflowChild.model_validate(workflow().model_dump())
    compatible = SimpleNamespace(id="w", name="W", description="D", steps=workflow().steps)
    for bad in (child, compatible):
        reject(data, "workflow_definition", workflow=bad)
    for step in (
        StepChild(id="one", name="One", employee="e", instructions="one"),
        SimpleNamespace(id="one", name="One", employee="e", instructions="one"),
    ):
        candidate = WorkflowDefinition.model_construct(
            id="w", name="W", description="D", steps=[step, *workflow().steps[1:]]
        )
        reject(data, "workflow_definition", workflow=candidate)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 1),
        ("name", 1),
        ("description", 1),
        ("steps", tuple()),
        ("steps", SimpleNamespace()),
    ],
)
def test_workflow_fields_are_exact(tmp_path: Path, field: str, value: object) -> None:
    candidate = WorkflowDefinition.model_construct(
        **(workflow().model_dump() | {field: value})
    )
    reject(values(tmp_path), "workflow_definition", workflow=candidate)


@pytest.mark.parametrize("field", ["state_path", "events_path"])
def test_persistence_target_identity_is_exact(tmp_path: Path, field: str) -> None:
    data = values(tmp_path)
    result = data["result"]  # type: ignore[assignment]
    reject(data, "persistence_contract", result=replace(result, **{field: Path("different")}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("state_bytes_written", True),
        ("state_bytes_written", IntChild(1)),
        ("state_bytes_written", 0),
        ("state_bytes_written", -1),
        ("state_bytes_written", 1.0),
        ("event_bytes_appended", True),
        ("event_bytes_appended", IntChild(1)),
        ("event_bytes_appended", 0),
        ("event_bytes_appended", -1),
        ("event_bytes_appended", 1.0),
    ],
)
def test_persistence_counts_require_exact_positive_builtin_int_before_phase135(
    tmp_path: Path, field: str, value: object
) -> None:
    data = values(tmp_path)
    reject(data, "persistence_contract", result=replace(data["result"], **{field: value}))


@pytest.mark.parametrize("field", ["state_bytes_written", "event_bytes_appended"])
def test_positive_but_wrong_persistence_counts_are_rejected_before_phase135(
    tmp_path: Path, field: str
) -> None:
    data = values(tmp_path)
    reject(
        data,
        "persistence_contract",
        result=replace(data["result"], **{field: getattr(data["result"], field) + 1}),
    )


def _history_matrix_case(tmp_path: Path, mode: str) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_text().splitlines(keepends=True)  # type: ignore[union-attr]
    if mode == "duplicate":
        content = lines[0] + lines[0] + lines[1] + lines[2] + lines[3] + lines[4]
    elif mode == "missing":
        content = lines[0] + lines[1] + lines[3] + lines[4]
    elif mode == "reordered":
        content = lines[1] + lines[0] + lines[2] + lines[3] + lines[4]
    elif mode == "unrelated":
        content = (
            serialize_runtime_step_event_jsonl(predecessor_event("wrong", 99))
            + "".join(lines[1:])
        )
    elif mode == "malformed":
        content = "{malformed}\n"
    else:
        content = "".join(lines) + lines[4]
    events.write_text(content)  # type: ignore[union-attr]
    reject(data, "persistence_contract")


@pytest.mark.parametrize("mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"])
def test_history_matrix_cases(tmp_path: Path, mode: str) -> None:
    _history_matrix_case(tmp_path, mode)


def _replace_terminal(data: dict[str, object], event: RuntimeStepEvent) -> None:
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    appended = serialize_runtime_step_event_jsonl(event).encode("utf-8")
    events.write_bytes(b"".join(lines[:-1]) + appended)  # type: ignore[union-attr]


def _replace_state(data: dict[str, object], state: WorkflowExecutionState) -> None:
    data["state_path"].write_bytes(  # type: ignore[union-attr]
        serialize_workflow_execution_state_json(state).encode()
    )


@pytest.mark.parametrize(
    "field",
    [
        "malformed_bytes",
        "workflow_id",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "completed_step_ids",
        "last_failure_category",
        "status",
    ],
)
def test_persisted_terminal_state_matrix_is_rejected_before_phase135(
    tmp_path: Path, field: str
) -> None:
    data = values(tmp_path)
    state = load_workflow_execution_state(data["state_path"])  # type: ignore[arg-type]
    if field == "malformed_bytes":
        data["state_path"].write_bytes(b"{malformed persisted state}\n")  # type: ignore[union-attr]
    elif field == "workflow_id":
        _replace_state(data, replace(state, workflow_id="other-workflow"))
    elif field == "current_step_id":
        _replace_state(data, replace(state, current_step_id="other-step"))
    elif field == "current_step_index":
        _replace_state(data, replace(state, current_step_index=3))
    elif field == "current_employee_id":
        _replace_state(data, replace(state, current_employee_id="other-employee"))
    elif field == "completed_step_ids":
        _replace_state(
            data, replace(state, completed_step_ids=("one", "two", "wrong", "four", "five"))
        )
    elif field == "status":
        _replace_state(data, replace(state, status="running"))
    else:
        _replace_state(
            data,
            replace(
                state,
                last_failure_category=(
                    "api_error" if state.status == "succeeded" else "transport_error"
                ),
            ),
        )
    reject(data, "persistence_contract")


@pytest.mark.parametrize(
    "field,value",
    [
        ("failure_category", "transport_error"),
        ("message", "wrong failure message"),
        ("request_id", "wrong-request"),
        ("response_id", "unexpected-response"),
        ("output_text", "unexpected-output"),
        ("provider", "other"),
        ("provider", 4),
    ],
)
def test_failed_terminal_event_semantics_are_rejected_before_phase135(
    tmp_path: Path, field: str, value: object
) -> None:
    data = values(tmp_path, "failed")
    _replace_terminal(data, terminal_event("failed", **{field: value}))
    reject(data, "persistence_contract")


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_type", "step_failed"),
        ("next_status", "failed"),
        ("failure_category", "api_error"),
        ("response_id", None),
        ("output_text", None),
        ("message", "wrong success message"),
    ],
)
def test_invalid_persisted_terminal_event_semantics_are_rejected_before_phase135(
    tmp_path: Path, field: str, value: object
) -> None:
    data = values(tmp_path)
    _replace_terminal(data, terminal_event("succeeded", **{field: value}))
    reject(data, "persistence_contract")


def test_invalid_persisted_predecessor_history_is_rejected_before_phase135(
    tmp_path: Path,
) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    rewritten = (
        serialize_runtime_step_event_jsonl(predecessor_event("two", 1)).encode()
        + serialize_runtime_step_event_jsonl(predecessor_event("one", 2)).encode()
        + serialize_runtime_step_event_jsonl(predecessor_event("three", 3)).encode()
        + serialize_runtime_step_event_jsonl(predecessor_event("four", 4)).encode()
    )
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    events.write_bytes(rewritten + lines[-1])  # type: ignore[union-attr]
    reject(data, "persistence_contract")


@pytest.mark.parametrize(
    "return_value",
    [
        object(),
        SimpleNamespace(
            outcome="persisted_success",
            workflow_id="w",
            current_step_id="five",
            current_step_index=5,
            current_employee_id="e",
            failure_category=None,
        ),
        WorkflowProgressionDecision(
            "workflow_complete", "w", "five", 5, "e", None, None, None, "last_step_succeeded"
        ),
    ],
)
def test_malformed_outcome_returns_are_rejected_and_compensated(
    tmp_path: Path, return_value: object
) -> None:
    data = values(tmp_path)
    before = (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return return_value

    with pytest.raises(OuterCompatibilityError) as caught:
        call(data, dependency)
    assert caught.value.detail.classification == "outcome_contract"
    assert calls == 1
    assert (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    ) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("outcome", "persisted_failure"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", 4),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("failure_category", "api_error"),
    ],
)
def test_success_outcome_field_contract_is_revalidated_and_compensated(
    tmp_path: Path, field: str, value: object
) -> None:
    data = values(tmp_path)
    expected = expected_outcome("succeeded")

    def dependency(*_: object) -> object:
        return replace(expected, **{field: value})

    reject_after_call(data, dependency, "outcome_contract")


@pytest.mark.parametrize(
    "field,value",
    [
        ("outcome", "persisted_success"),
        ("failure_category", None),
        ("failure_category", "transport_error"),
        ("current_step_index", 4),
    ],
)
def test_failed_outcome_field_contract_is_revalidated_and_compensated(
    tmp_path: Path, field: str, value: object
) -> None:
    data = values(tmp_path, "failed")
    expected = expected_outcome("failed")

    def dependency(*_: object) -> object:
        return replace(expected, **{field: value})

    reject_after_call(data, dependency, "outcome_contract")


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_outcome_but_mutated_targets_is_compensated_without_retry(
    tmp_path: Path, mutation: str
) -> None:
    data = values(tmp_path)
    before = (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            data["state_path"].write_bytes(b"mutated-state")  # type: ignore[union-attr]
        if mutation in ("events", "both"):
            data["events_path"].write_bytes(b"mutated-events")  # type: ignore[union-attr]
        return expected_outcome("succeeded")

    with pytest.raises(OuterCompatibilityError) as caught:
        call(data, dependency)
    assert caught.value.detail.classification == "outcome_contract"
    assert calls == 1
    assert (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    ) == before


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_safe_phase135_error_identity_is_preserved_after_compensation(
    tmp_path: Path, mutation: str | None
) -> None:
    data = values(tmp_path)
    state, events = data["state_path"], data["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    supplied_error = Phase135Error("safe detail")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"mutated-state")  # type: ignore[union-attr]
        if mutation in ("events", "both"):
            events.write_bytes(b"mutated-events")  # type: ignore[union-attr]
        raise supplied_error

    with pytest.raises(Phase135Error) as caught:
        call(data, dependency)
    assert caught.value is supplied_error and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_unexpected_error_is_sanitized_and_compensated(
    tmp_path: Path, mutation: str | None
) -> None:
    data = values(tmp_path)
    state, events = data["state_path"], data["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"mutated-state")  # type: ignore[union-attr]
        if mutation in ("events", "both"):
            events.write_bytes(b"mutated-events")  # type: ignore[union-attr]
        raise RuntimeError("secret detail")

    with pytest.raises(OuterCompatibilityError) as caught:
        call(data, dependency)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value) and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path, failed_target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = values(tmp_path)
    state, events = data["state_path"], data["events_path"]
    original_write = Path.write_bytes
    restore_calls = {"state": 0, "events": 0}
    dependency_calls = 0

    def restore(path: Path, payload: bytes) -> int:
        key = "state" if path == state else "events"
        restore_calls[key] += 1
        if failed_target in (key, "both"):
            raise OSError("rollback")
        return original_write(path, payload)

    monkeypatch.setattr(Path, "write_bytes", restore)

    def dependency(*_: object) -> object:
        nonlocal dependency_calls
        dependency_calls += 1
        original_write(state, b"mutated-state")
        original_write(events, b"mutated-events")
        return object()

    with pytest.raises(OuterCompatibilityError) as caught:
        call(data, dependency)
    assert caught.value.detail.classification == "dependency_rollback"
    assert restore_calls == {"state": 1, "events": 1}
    assert dependency_calls == 1


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_allow_non_openai_terminal_provider_and_are_zero_call(
    tmp_path: Path, kind: str
) -> None:
    data, result = stop_values(tmp_path, kind, provider="other")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert call(data, dependency) is result
    assert calls == 0
    assert_unchanged(data)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_subclasses_and_compatible_substitutes_are_zero_call_rejected(
    tmp_path: Path, kind: str
) -> None:
    data, result = stop_values(tmp_path, kind)
    child = (
        DecisionChild(*result.__dict__.values())  # type: ignore[union-attr]
        if kind == "complete"
        else OutcomeChild(*result.__dict__.values())  # type: ignore[union-attr]
    )
    substitute = SimpleNamespace(**result.__dict__)  # type: ignore[union-attr]
    for replacement in (child, substitute):
        reject(data, "result_type", result=replacement)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_current_step_index_bool_and_int_subclass_are_zero_call_rejected(
    tmp_path: Path, kind: str
) -> None:
    data, result = stop_values(tmp_path, kind)
    classification = "completion_contract" if kind == "complete" else "failure_contract"
    for replacement in (True, IntChild(5)):
        reject(data, classification, result=replace(result, current_step_index=replacement))


def test_stop_malformed_values_and_unsupported_results_are_zero_call_rejected(
    tmp_path: Path,
) -> None:
    data, complete = stop_values(tmp_path, "complete")
    reject(data, "completion_contract", result=replace(complete, reason="wrong"))
    data, failure = stop_values(tmp_path, "failure")
    reject(data, "failure_contract", result=replace(failure, failure_category="unknown"))
    unsupported = WorkflowProgressionDecision(
        "prepare_next_step", "w", "five", 5, "e", None, None, None, "unsupported"
    )
    reject(values(tmp_path), "completion_contract", result=unsupported)


def test_workflow_complete_stop_empty_terminal_output_is_rejected(
    tmp_path: Path,
) -> None:
    data, _result = stop_values(tmp_path, "complete")
    state_path, events_path = data["state_path"], data["events_path"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        terminal_event("succeeded", output_text="")
    )
    events_path.write_text("".join(lines[:-1]) + replacement, encoding="utf-8")  # type: ignore[union-attr]
    before = state_path.read_bytes(), events_path.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(OuterCompatibilityError) as caught:
        public_phase143(**_arguments(data), phase135_function=dependency)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (state_path.read_bytes(), events_path.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_allow_empty_predecessor_output_text_zero_call_unchanged(
    tmp_path: Path, kind: str
) -> None:
    data, result = stop_values(tmp_path, kind)
    state_path, events_path = data["state_path"], data["events_path"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("four", 4, "openai", output_text="")
    )
    events_path.write_text("".join(lines[:3]) + replacement + "".join(lines[4:]), encoding="utf-8")  # type: ignore[union-attr]
    before = state_path.read_bytes(), events_path.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert (
        public_phase143(**_arguments(data), phase135_function=dependency)  # type: ignore[arg-type]
        is result
    )
    assert calls == 0
    assert (state_path.read_bytes(), events_path.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "value",
    [
        RunningStatePersistenceResult(state_bytes_written=1),
        StepRuntimeExecutionSuccess(
            workflow_id="w",
            step_id="one",
            step_index=1,
            employee_id="e",
            invocation_result=ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out"),
        ),
        StepRuntimeExecutionFailure(
            workflow_id="w",
            step_id="one",
            step_index=1,
            employee_id="e",
            invocation_result=ModelInvocationFailure("openai", "api_error", "safe", "q", 500, None, None),
        ),
        PreparedStepExecutionStart(
            request=ModelInvocationRequest(
                model="m",
                system_instructions="s",
                task_instructions="t",
                allowed_tools=(),
            ),
            running_state=WorkflowExecutionState(
                workflow_id="w",
                status="running",
                current_step_id="five",
                current_step_index=5,
                current_employee_id="e",
                completed_step_ids=(),
                last_failure_category=None,
            ),
        ),
        SimpleNamespace(request="request", running_state="state"),
    ],
)
def test_direct_non_phase142_results_are_zero_call_rejected(
    tmp_path: Path, value: object
) -> None:
    reject(values(tmp_path), "result_type", result=value)


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_missing_and_directory_targets_are_rejected_before_phase135(
    tmp_path: Path, target: str
) -> None:
    data = values(tmp_path)
    path = data[target]
    path.unlink()  # type: ignore[union-attr]
    reject(data, "state_target" if target == "state_path" else "event_target")
    path.mkdir()  # type: ignore[union-attr]
    reject(data, "state_target" if target == "state_path" else "event_target")


def test_target_conflict_and_non_callable_dependency_are_rejected(tmp_path: Path) -> None:
    data = values(tmp_path)
    reject(data, "target_conflict", events_path=data["state_path"])
    with pytest.raises(OuterCompatibilityError) as caught:
        public_phase143(**_arguments(data), phase135_function=object())  # type: ignore[arg-type]
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_target_oserror_is_classified_by_target(
    tmp_path: Path,
    operation: str,
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = values(tmp_path)
    selected = data[target]
    original = getattr(Path, operation)

    def raising(path: Path, *args: object, **kwargs: object) -> object:
        if path == selected:
            raise OSError("synthetic")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, raising)
    reject(data, "state_target" if target == "state_path" else "event_target")


def accumulated_workflow() -> WorkflowDefinition:
    """Eight-step workflow exposing positions 5 and 6 as accumulated history."""
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{index}",
                    "name": f"Step {index}",
                    "employee": "e",
                    "instructions": f"step-{index}",
                }
                for index in range(1, 9)
            ],
        }
    )


def write_accumulated_targets(
    tmp_path: Path,
    status: str,
    *,
    five_provider: object = "openai",
    six_provider: object = "openai",
) -> tuple[Path, Path, bytes, bytes]:
    """Terminal step-7 targets with six predecessors.

    Positions 1-4 use the default non-openai predecessors; positions 5 and 6
    carry accumulated None request ids with the openai provider by default.
    """
    wf = accumulated_workflow()
    state = WorkflowExecutionState(
        "w",
        status,
        "step-7",
        7,
        "e",
        (
            tuple(step.id for step in wf.steps[:7])
            if status == "succeeded"
            else tuple(step.id for step in wf.steps[:6])
        ),
        None if status == "succeeded" else "api_error",
    )
    predecessors = []
    for index, step in enumerate(wf.steps[:6], 1):
        provider = "other"
        changes: dict[str, object] = {}
        if index >= 5:
            provider = five_provider if index == 5 else six_provider
            changes["request_id"] = None
        predecessors.append(predecessor_event(step.id, index, provider, **changes))
    terminal_changes: dict[str, object] = {
        "step_id": "step-7",
        "step_index": 7,
        "request_id": "request-step-7",
    }
    if status == "succeeded":
        terminal_changes.update(
            response_id="response-step-7", output_text="output-step-7"
        )
    terminal = terminal_event(status, **terminal_changes)
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def accumulated_values(
    tmp_path: Path,
    status: str = "succeeded",
    *,
    five_provider: object = "openai",
    six_provider: object = "openai",
) -> dict[str, object]:
    state, events, before_state, before_events = write_accumulated_targets(
        tmp_path,
        status,
        five_provider=five_provider,
        six_provider=six_provider,
    )
    return {
        "result": persistence_result(state, events),
        "workflow": accumulated_workflow(),
        "state_path": state,
        "events_path": events,
        "before_state": before_state,
        "before_events": before_events,
    }


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_accumulated_none_request_id_positions_five_six_delegates_once(
    tmp_path: Path, status: str
) -> None:
    data = accumulated_values(tmp_path, status)
    expected = phase135_fake(
        data["result"], data["workflow"], data["state_path"], data["events_path"]
    )
    seen: list[tuple[object, ...]] = []
    rewritten = (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    )

    def dependency(*args: object) -> object:
        seen.append(args)
        return expected

    assert call(data, dependency) is expected
    assert len(seen) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            seen[0],
            tuple(
                data[key] for key in ("result", "workflow", "state_path", "events_path")
            ),
            strict=True,
        )
    )
    assert (
        data["state_path"].read_bytes(),  # type: ignore[union-attr]
        data["events_path"].read_bytes(),  # type: ignore[union-attr]
    ) == rewritten


def test_accumulated_none_position_five_non_openai_provider_is_rejected_before_phase135(
    tmp_path: Path,
) -> None:
    data = accumulated_values(tmp_path, five_provider="other")
    reject(data, "persistence_contract")


def test_accumulated_none_position_four_remains_rejected_before_phase135(
    tmp_path: Path,
) -> None:
    data = accumulated_values(tmp_path)
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("step-4", 4, "other", request_id=None)
    ).encode()
    events.write_bytes(b"".join(lines[:3]) + replacement + b"".join(lines[4:]))  # type: ignore[union-attr]
    reject(data, "persistence_contract")
