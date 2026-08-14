"""Real Phase 58 -> 51 -> 44 -> 37 tail with Phase-155 provenance compatibility."""

# ruff: noqa: E501,E701,E702,F401,I001

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import PersistedExecutionOutcome
from ai_office.engine.persisted_execution_outcome_reentry import (
    classify_persisted_execution_outcome_reentry,
)
from ai_office.engine.persisted_terminal_outcome_classification_bridge_reentry import (
    route_persisted_terminal_outcome_classification_bridge_reentry,
)
from ai_office.engine.persisted_terminal_outcome_classification_phase_bridge_reentry import (
    PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError,
    route_persisted_terminal_outcome_classification_phase_bridge_reentry,
)
from ai_office.engine.persisted_terminal_outcome_classification_routing_reentry import (
    route_persisted_terminal_outcome_classification_reentry,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_SENTINEL = object()


def workflow() -> WorkflowDefinition:
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
                for step_id in _STEP_IDS
            ],
        }
    )


def predecessor_event(
    step_id: str,
    position: int,
    *,
    provider: str = "openai",
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


def terminal_event(status: str, *, message: str = "safe failure") -> RuntimeStepEvent:
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
        message,
    )


def setup(
    tmp_path: Path,
    status: str,
    *,
    earlier_empty: tuple[int, ...] = (2,),
    message: str = "safe failure",
) -> dict[str, object]:
    supplied_workflow = workflow()
    state = WorkflowExecutionState(
        "w",
        status,
        "six",
        6,
        "s",
        tuple(_STEP_IDS) if status == "succeeded" else tuple(_STEP_IDS[:5]),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    events = [
        predecessor_event(
            step_id, position, output_text="" if position in earlier_empty else "output"
        )
        for position, step_id in enumerate(_STEP_IDS[:5], 1)
    ]
    events[4] = predecessor_event(
        "five", 5, provider="openai", request_id=None, output_text=""
    )
    events.append(terminal_event(status, message=message))
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")
    terminal_bytes = serialize_runtime_step_event_jsonl(events[-1]).encode("utf-8")
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(
        state_path, events_path, len(state_bytes), len(terminal_bytes)
    )
    return {
        "result": result,
        "workflow": supplied_workflow,
        "state_path": state_path,
        "events_path": events_path,
    }


def reload_and_assert_provenance(
    values: dict[str, object],
    status: str,
    *,
    earlier_empty: tuple[int, ...] = (2,),
    message: str = "safe failure",
) -> None:
    """Explicit public-loader reload proves earlier empty output, immediate empty
    output, immediate request_id=None, earlier non-empty request IDs, immediate
    provider "openai", and the terminal state/history outcome contract."""
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
        )
    )
    state, events = loaded.state, loaded.events
    for position in range(1, 5):
        assert events[position - 1].step_id == _STEP_IDS[position - 1]
        assert events[position - 1].request_id == f"request-{_STEP_IDS[position - 1]}"
    for position in earlier_empty:
        assert events[position - 1].output_text == ""
    assert events[4].step_id == "five"
    assert events[4].output_text == ""
    assert events[4].request_id is None
    assert events[4].provider == "openai"
    assert state.status == status
    assert state.current_step_id == "six"
    assert state.current_step_index == 6
    assert state.current_employee_id == "s"
    assert state.completed_step_ids == (
        tuple(_STEP_IDS) if status == "succeeded" else tuple(_STEP_IDS[:5])
    )
    assert state.last_failure_category == (
        None if status == "succeeded" else "api_error"
    )
    terminal = events[-1]
    assert terminal.step_id == "six"
    assert terminal.step_index == 6
    assert terminal.employee_id == "s"
    assert terminal.provider == "openai"
    if status == "succeeded":
        assert terminal.event_type == "step_succeeded"
        assert terminal.next_status == "succeeded"
        assert terminal.failure_category is None
        assert terminal.response_id == "response-six"
        assert terminal.request_id == "request-six"
        assert terminal.output_text == "output-six"
        assert terminal.message is None
    else:
        assert terminal.event_type == "step_failed"
        assert terminal.next_status == "failed"
        assert terminal.failure_category == "api_error"
        assert terminal.response_id is None
        assert terminal.request_id == "request-six"
        assert terminal.output_text is None
        assert terminal.message == message


def run_real_chain(
    values: dict[str, object], status: str
) -> tuple[object, dict[str, int], list[tuple[str, tuple[object, ...]]], list[object]]:
    """Real Phase 58 -> 51 -> 44 -> 37 tail, every boundary recorded once.

    Each real boundary is wrapped only to record the call and immediately
    delegate to the next real boundary; the terminal Phase 37 classifier is
    real and produces the outcome that must flow back unchanged.
    """
    calls = {"phase58": 0, "phase51": 0, "phase44": 0, "phase37": 0}
    handoffs: list[tuple[str, tuple[object, ...]]] = []
    phase37_values: list[object] = []

    def phase37(workflow: object, state_path: object, events_path: object) -> object:
        calls["phase37"] += 1
        handoffs.append(("phase37", (workflow, state_path, events_path)))
        outcome = classify_persisted_execution_outcome_reentry(
            workflow, state_path, events_path
        )
        phase37_values.append(outcome)
        return outcome

    def phase44(
        result: object, workflow: object, state_path: object, events_path: object
    ) -> object:
        calls["phase44"] += 1
        handoffs.append(("phase44", (result, workflow, state_path, events_path)))
        return route_persisted_terminal_outcome_classification_reentry(
            result,
            workflow,
            state_path,
            events_path,
            classification_function=phase37,  # type: ignore[arg-type]
        )

    def phase51(
        result: object, workflow: object, state_path: object, events_path: object
    ) -> object:
        calls["phase51"] += 1
        handoffs.append(("phase51", (result, workflow, state_path, events_path)))
        return route_persisted_terminal_outcome_classification_bridge_reentry(
            result,
            workflow,
            state_path,
            events_path,
            classification_routing_function=phase44,  # type: ignore[arg-type]
        )

    out = route_persisted_terminal_outcome_classification_phase_bridge_reentry(
        values["result"],  # type: ignore[arg-type]
        values["workflow"],  # type: ignore[arg-type]
        values["state_path"],  # type: ignore[arg-type]
        values["events_path"],  # type: ignore[arg-type]
        phase51_function=phase51,  # type: ignore[arg-type]
    )
    calls["phase58"] += 1
    return out, calls, handoffs, phase37_values


def assert_chain_ok(
    values: dict[str, object],
    out: object,
    calls: dict[str, int],
    handoffs: list[tuple[str, tuple[object, ...]]],
    phase37_values: list[object],
) -> None:
    # Each real boundary executes exactly once; no retry anywhere.
    assert calls == {"phase58": 1, "phase51": 1, "phase44": 1, "phase37": 1}
    # The Phase 37 generated object flows back through 44/51/58 unchanged.
    assert out is phase37_values[0]
    expected = tuple(
        values[key] for key in ("result", "workflow", "state_path", "events_path")
    )
    assert [name for name, _ in handoffs] == ["phase51", "phase44", "phase37"]
    for name, args in handoffs:
        if name == "phase37":
            # The Phase 44 -> 37 seam is the three-argument classifier seam.
            assert args == expected[1:]
            assert all(
                actual is wanted
                for actual, wanted in zip(args, expected[1:], strict=True)
            )
        else:
            assert all(
                actual is wanted for actual, wanted in zip(args, expected, strict=True)
            )


def assert_targets_unchanged(
    values: dict[str, object], before: tuple[bytes, bytes]
) -> None:
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("status", "message", "earlier_empty"),
    [
        ("succeeded", "safe failure", (2,)),
        ("failed", "", (2,)),
    ],
)
def test_real_chain_accepts_empty_predecessor_output(
    tmp_path: Path, status: str, message: str, earlier_empty: tuple[int, ...]
) -> None:
    values = setup(
        tmp_path, status, earlier_empty=earlier_empty, message=message
    )
    reload_and_assert_provenance(
        values, status, earlier_empty=earlier_empty, message=message
    )
    before = (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        values["events_path"].read_bytes(),  # type: ignore[union-attr]
    )
    out, calls, handoffs, phase37_values = run_real_chain(values, status)
    assert_chain_ok(values, out, calls, handoffs, phase37_values)
    assert_targets_unchanged(values, before)


@pytest.mark.parametrize(
    ("status", "message", "earlier_empty"),
    [
        ("succeeded", "safe failure", (1, 3)),
        ("failed", "safe failure", (1, 3)),
    ],
)
def test_real_chain_accepts_multiple_earlier_empty_outputs(
    tmp_path: Path, status: str, message: str, earlier_empty: tuple[int, ...]
) -> None:
    values = setup(
        tmp_path, status, earlier_empty=earlier_empty, message=message
    )
    reload_and_assert_provenance(
        values, status, earlier_empty=earlier_empty, message=message
    )
    before = (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        values["events_path"].read_bytes(),  # type: ignore[union-attr]
    )
    out, calls, handoffs, phase37_values = run_real_chain(values, status)
    assert_chain_ok(values, out, calls, handoffs, phase37_values)
    assert_targets_unchanged(values, before)


def test_real_chain_rejects_none_predecessor_output_before_any_seam(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "succeeded")
    # Public-loader reload before the rejecting invocation: the intact persisted
    # provenance keeps earlier request IDs non-empty built-in str and immediate
    # step 5 request_id None, and terminal state/history matches the contract.
    reload_and_assert_provenance(values, "succeeded")
    events_path = values["events_path"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    import json

    # Issue exact provenance: step two keeps its non-empty built-in request_id
    # ("request-two"); only output_text becomes None. Immediate step 5 keeps
    # request_id None.
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("two", 2, output_text=None)
    )
    mutated = json.loads(replacement)
    assert mutated["request_id"] == "request-two"
    assert mutated["output_text"] is None
    events_path.write_text(  # type: ignore[union-attr]
        lines[0] + replacement + "".join(lines[2:]), encoding="utf-8"
    )
    before = (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        events_path.read_bytes(),  # type: ignore[union-attr]
    )
    calls = {"phase51": 0, "phase44": 0, "phase37": 0}

    def fail(*_: object) -> object:
        calls["phase51"] += 1
        calls["phase44"] += 1
        calls["phase37"] += 1
        pytest.fail("no dependency may be called")

    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            values["result"],  # type: ignore[arg-type]
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
            phase51_function=fail,  # type: ignore[arg-type]
        )
    assert (
        type(caught.value)
        is PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase51": 0, "phase44": 0, "phase37": 0}
    assert_targets_unchanged(values, before)


def test_real_chain_rejects_non_string_predecessor_output_before_any_seam(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "succeeded")
    # Public-loader reload before the rejecting invocation: the intact persisted
    # provenance keeps earlier request IDs non-empty built-in str and immediate
    # step 5 request_id None, and terminal state/history matches the contract.
    reload_and_assert_provenance(values, "succeeded")
    events_path = values["events_path"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    import json

    # Issue exact case #6: the immediate predecessor (step 5) output_text is
    # mutated to a representative non-string. Step 2 earlier empty (""), step 5
    # request_id None and provider "openai" are preserved.
    assert json.loads(lines[1])["step_id"] == "two"
    assert json.loads(lines[1])["output_text"] == ""
    payload = json.loads(lines[4])
    assert payload["step_id"] == "five"
    assert payload["request_id"] is None
    assert payload["provider"] == "openai"
    assert payload["output_text"] == ""
    payload["output_text"] = 1
    lines[4] = json.dumps(payload, separators=(",", ":")) + "\n"
    events_path.write_text("".join(lines), encoding="utf-8")  # type: ignore[union-attr]
    before = (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        events_path.read_bytes(),  # type: ignore[union-attr]
    )
    calls = {"phase51": 0, "phase44": 0, "phase37": 0}

    def fail(*_: object) -> object:
        calls["phase51"] += 1
        calls["phase44"] += 1
        calls["phase37"] += 1
        pytest.fail("no dependency may be called")

    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            values["result"],  # type: ignore[arg-type]
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
            phase51_function=fail,  # type: ignore[arg-type]
        )
    assert (
        type(caught.value)
        is PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase51": 0, "phase44": 0, "phase37": 0}
    assert_targets_unchanged(values, before)
