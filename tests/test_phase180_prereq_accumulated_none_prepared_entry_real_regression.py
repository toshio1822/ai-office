"""Real Phase 180 prerequisite: preserved accumulated-aged None through prepared-step-start entry layers (Issue #390).

After Phase 179 produces an approved-preparation ``PreparedWorkflowStep`` for a
step >= 7 whose durable predecessor history carries accumulated-aged
``request_id=None`` (provider ``"openai"``, positions >= 5, current step
index >= 7), the prepared-step-start entry layers (Phase 146 outer-chain and
Phase 138 outer) must accept that real provenance on the prepare route.
Before Issue #390 those entry layers independently rejected any aged
non-immediate predecessor with ``request_id=None`` via ``terminal_contract``,
so the Phase 179 result could not be rolled into a prepared-step start.

Issue #390 adds the bounded ``allow_accumulated_openai_none`` relaxation on
the prepared route only: an aged predecessor at position >= 5 whose provider
is exactly openai and whose request_id is None is accepted when
``current_step_index >= 7``.  These tests prove the exact Phase 146 and
Phase 138 prepared entries accept accumulated-aged-None provenance (real
deep-chain delegation returns the exact prepared start unchanged), while
below-threshold positions, non-openai accumulated None, and aged-None on the
stop routes remain strictly rejected.

Requirement-to-test mapping (Issue #390):
- A -> test_a_phase146_accumulated_none_prepare_accepts
- B -> test_b_phase138_accumulated_none_prepare_accepts
- C -> test_c_phase146_chain_non_contiguous_position5_none_accepts
- D -> test_d_phase146_inline_strict_prepare_negatives
- E -> test_e_phase138_inline_strict_prepare_negatives
- F -> test_f_phase146_stop_routes_exact_identity
- G -> test_g_phase146_stop_routes_aged_none_strict
- H -> test_h_phase138_stop_routes_aged_none_strict
"""

# ruff: noqa: E501,E701,E702,F401,I001

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as real_phase146,
)
from ai_office.engine import (
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as real_phase138,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase146Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as Phase138Error,
)
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_HARNESS146 = Path(__file__).with_name(
    "test_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py"
)


def _harness146():
    spec = importlib.util.spec_from_file_location("_phase146_harness", _HARNESS146)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _workflow(steps: int) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{i}",
                    "name": f"Step {i}",
                    "employee": f"e{i}",
                    "instructions": f"step-{i}",
                }
                for i in range(1, steps + 1)
            ],
        }
    )


def _employee(index: int) -> EmployeeDefinition:
    step = _workflow(20).steps[index - 1]
    return EmployeeDefinition.model_validate(
        {
            "id": step.employee,
            "name": "Next Employee",
            "role": "role",
            "instructions": "employee instructions",
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def _prepared(index: int) -> PreparedWorkflowStep:
    wf = _workflow(20)
    step = wf.steps[index - 1]
    person = _employee(index)
    return PreparedWorkflowStep(
        wf.id,
        step.id,
        index,
        person.id,
        person.instructions,
        step.instructions,
        person.model,
        tuple(person.allowed_tools),
    )


def _start_for(value: PreparedWorkflowStep) -> PreparedStepExecutionStart:
    wf = _workflow(20)
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            value.model,
            value.employee_instructions,
            value.step_instructions,
            value.allowed_tool_names,
        ),
        WorkflowExecutionState(
            value.workflow_id,
            "running",
            value.step_id,
            value.step_index,
            value.employee_id,
            tuple(step.id for step in wf.steps[: value.step_index - 1]),
            None,
        ),
    )


def _predecessor_event(step_id: str, index: int, **changes: object) -> RuntimeStepEvent:
    return replace(
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            step_id,
            index,
            f"e{index}",
            "running",
            "succeeded",
            "openai",
            None,
            f"response-{step_id}",
            f"request-{step_id}",
            f"output-{step_id}",
            None,
        ),
        **changes,
    )


def _write_prepared_scenario(
    tmp_path: Path,
    *,
    prepared_index: int = 8,
    current: int = 7,
    none_positions: tuple[int, ...] = (),
    non_openai_positions: tuple[int, ...] = (),
) -> dict:
    """Write a prepared-route durable snapshot: prior steps 1..``current``
    succeeded, with the requested None/openai overrides on aged predecessors.
    ``prepared_index`` is the next step the real entry layer will prepare."""
    wf = _workflow(20)
    current_step = wf.steps[current - 1]
    predecessors = []
    for position in range(1, current):
        changes: dict = {}
        if position in none_positions:
            changes["request_id"] = None
            changes["provider"] = "openai"
        if position in non_openai_positions:
            changes["provider"] = "anthropic"
        predecessors.append(
            _predecessor_event(wf.steps[position - 1].id, position, **changes)
        )
    terminal = _predecessor_event(
        current_step.id, current, request_id="request", output_text="output"
    )
    state = WorkflowExecutionState(
        "w",
        "succeeded",
        current_step.id,
        current,
        current_step.employee,
        tuple(step.id for step in wf.steps[:current]),
        None,
    )
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return {
        "workflow": wf,
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_bytes,
        "before_events": event_bytes,
    }


def _recording_stub(return_value: object):
    calls: list = []

    def stub(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return return_value

    return stub, calls


# --- A: real Phase 146 prepared entry accepts accumulated aged None ------
def test_a_phase146_accumulated_none_prepare_accepts(tmp_path: Path) -> None:
    # Issue #390: the real Phase 146 outer-chain prepared route accepts a
    # real accumulated-aged-None predecessor history (positions 5 and 6 with
    # request_id=None / provider openai, current step 7) and delegates to the
    # real Phase 138 entry to produce the exact prepared start for step 8.
    scenario = _write_prepared_scenario(
        tmp_path / "a", prepared_index=8, current=7, none_positions=(5, 6)
    )
    wf = scenario["workflow"]
    prepared = _prepared(8)
    employee = _employee(8)
    expected = _start_for(prepared)
    stub, calls = _recording_stub(expected)
    out = real_phase146(
        prepared,
        wf,
        employee,
        scenario["state_path"],
        scenario["events_path"],
        phase138_function=stub,
    )
    assert out is expected
    assert len(calls) == 1
    assert calls[0][0] == (prepared, wf, employee, scenario["state_path"], scenario["events_path"])
    assert calls[0][1] == {}
    assert scenario["state_path"].read_bytes() == scenario["before_state"]
    assert scenario["events_path"].read_bytes() == scenario["before_events"]


# --- B: real Phase 138 prepared entry accepts accumulated aged None -------
def test_b_phase138_accumulated_none_prepare_accepts(tmp_path: Path) -> None:
    # Issue #390: the real Phase 138 outer prepared route independently
    # accepts the same accumulated-aged-None provenance and delegates to the
    # real Phase 131 entry to produce the exact prepared start for step 8.
    scenario = _write_prepared_scenario(
        tmp_path / "b", prepared_index=8, current=7, none_positions=(5, 6)
    )
    wf = scenario["workflow"]
    prepared = _prepared(8)
    employee = _employee(8)
    expected = _start_for(prepared)
    stub, calls = _recording_stub(expected)
    out = real_phase138(
        prepared,
        wf,
        employee,
        scenario["state_path"],
        scenario["events_path"],
        phase131_function=stub,
    )
    assert out is expected
    assert len(calls) == 1
    assert scenario["state_path"].read_bytes() == scenario["before_state"]
    assert scenario["events_path"].read_bytes() == scenario["before_events"]


# --- C: real Phase 146 chain over real Phase 138 with non-contiguous None --
def test_c_phase146_chain_non_contiguous_position5_none_accepts(
    tmp_path: Path,
) -> None:
    # Non-contiguous accumulated control: only position 5 is None/openai,
    # position 6 keeps a non-empty request id.  The real Phase 146 entry
    # (delegating to the real Phase 138) still prepares step 8.
    scenario = _write_prepared_scenario(
        tmp_path / "c", prepared_index=8, current=7, none_positions=(5,)
    )
    wf = scenario["workflow"]
    prepared = _prepared(8)
    employee = _employee(8)
    expected = _start_for(prepared)
    stub, calls = _recording_stub(expected)
    out = real_phase146(
        prepared,
        wf,
        employee,
        scenario["state_path"],
        scenario["events_path"],
        phase138_function=stub,
    )
    assert out is expected
    assert len(calls) == 1
    assert scenario["state_path"].read_bytes() == scenario["before_state"]
    assert scenario["events_path"].read_bytes() == scenario["before_events"]


# --- D: Phase 146 inline strict prepare negatives -------------------------
def test_d_phase146_inline_strict_prepare_negatives(tmp_path: Path) -> None:
    wf = _workflow(20)
    prepared = _prepared(8)
    employee = _employee(8)

    # (a) accumulated None at position 4 (below the >= 5 threshold) stays
    # strict on the Phase 146 prepare route.
    below = _write_prepared_scenario(
        tmp_path / "below", prepared_index=8, current=7, none_positions=(4,)
    )
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            prepared,
            wf,
            employee,
            below["state_path"],
            below["events_path"],
            phase138_function=lambda *a, **k: _start_for(prepared),
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert below["state_path"].read_bytes() == below["before_state"]
    assert below["events_path"].read_bytes() == below["before_events"]

    # (b) accumulated None at position 5 with a non-openai provider stays
    # strict on the Phase 146 prepare route.
    non_openai = _write_prepared_scenario(
        tmp_path / "nonopenai",
        prepared_index=8,
        current=7,
        none_positions=(5,),
        non_openai_positions=(5,),
    )
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            prepared,
            wf,
            employee,
            non_openai["state_path"],
            non_openai["events_path"],
            phase138_function=lambda *a, **k: _start_for(prepared),
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert non_openai["state_path"].read_bytes() == non_openai["before_state"]
    assert non_openai["events_path"].read_bytes() == non_openai["before_events"]

    # (c) an aged openai None below the current_step_index >= 7 boundary (a
    # six-step history whose immediate predecessor at position 5 is None but
    # whose state carries the empty output) stays strict: the accumulated
    # relaxation is bounded to current_step_index >= 7, and the deeper aged
    # None at position 4 is below the position >= 5 threshold too.
    early = _write_prepared_scenario(
        tmp_path / "early",
        prepared_index=7,
        current=6,
        none_positions=(4,),
    )
    prepared_early = _prepared(7)
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            prepared_early,
            wf,
            _employee(7),
            early["state_path"],
            early["events_path"],
            phase138_function=lambda *a, **k: _start_for(prepared_early),
        )
    assert caught.value.detail.classification == "terminal_contract"


# --- E: Phase 138 inline strict prepare negatives -------------------------
def test_e_phase138_inline_strict_prepare_negatives(tmp_path: Path) -> None:
    wf = _workflow(20)
    prepared = _prepared(8)
    employee = _employee(8)

    # (a) below-threshold position 4 stays strict.
    below = _write_prepared_scenario(
        tmp_path / "below", prepared_index=8, current=7, none_positions=(4,)
    )
    with pytest.raises(Phase138Error) as caught:
        real_phase138(
            prepared,
            wf,
            employee,
            below["state_path"],
            below["events_path"],
            phase131_function=lambda *a, **k: _start_for(prepared),
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (b) non-openai position 5 stays strict.
    non_openai = _write_prepared_scenario(
        tmp_path / "nonopenai",
        prepared_index=8,
        current=7,
        none_positions=(5,),
        non_openai_positions=(5,),
    )
    with pytest.raises(Phase138Error) as caught:
        real_phase138(
            prepared,
            wf,
            employee,
            non_openai["state_path"],
            non_openai["events_path"],
            phase131_function=lambda *a, **k: _start_for(prepared),
        )
    assert caught.value.detail.classification == "terminal_contract"


# --- F: Phase 146 stop routes exact identity, bytes unchanged -------------
def test_f_phase146_stop_routes_exact_identity(tmp_path: Path) -> None:
    # (a) canonical workflow_complete stop: exact identity + bytes unchanged.
    d1 = tmp_path / "d1"
    values = _write_terminal_stop(d1, steps=6, fail=False)
    wf = values["workflow"]
    stop = _completion(wf, 6)
    out = real_phase146(
        stop,
        wf,
        None,
        values["state_path"],
        values["events_path"],
        phase138_function=lambda *a, **k: None,
    )
    assert out is stop
    assert values["state_path"].read_bytes() == values["before_state"]
    assert values["events_path"].read_bytes() == values["before_events"]

    # (b) canonical persisted_failure stop: exact identity + bytes unchanged.
    d2 = tmp_path / "d2"
    values2 = _write_terminal_stop(d2, steps=6, fail=True)
    wf2 = values2["workflow"]
    stop2 = _failure(wf2, 6)
    out2 = real_phase146(
        stop2,
        wf2,
        None,
        values2["state_path"],
        values2["events_path"],
        phase138_function=lambda *a, **k: None,
    )
    assert out2 is stop2
    assert values2["state_path"].read_bytes() == values2["before_state"]
    assert values2["events_path"].read_bytes() == values2["before_events"]


# --- G: Phase 146 stop routes stay strict against aged None ---------------
def test_g_phase146_stop_routes_aged_none_strict(tmp_path: Path) -> None:
    # The accumulated relaxation is gated to the prepare route only, so an
    # aged openai None at positions 5 and 6 is still rejected on the
    # Phase 146 stop routes (both workflow_complete and persisted_failure).
    # (a) workflow_complete stop.
    d1 = tmp_path / "d1"
    values = _write_terminal_stop(d1, steps=8, fail=False)
    wf = values["workflow"]
    state_path, events_path = values["state_path"], values["events_path"]
    injected = _inject_aged_none(events_path)
    stop = _completion(wf, 8)
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            stop, wf, None, state_path, events_path,
            phase138_function=lambda *a, **k: None,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert state_path.read_bytes() == values["before_state"]
    assert events_path.read_bytes() == injected

    # (b) persisted_failure stop.
    d2 = tmp_path / "d2"
    values2 = _write_terminal_stop(d2, steps=8, fail=True)
    wf2 = values2["workflow"]
    state_path2, events_path2 = values2["state_path"], values2["events_path"]
    injected2 = _inject_aged_none(events_path2)
    stop2 = _failure(wf2, 8)
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            stop2, wf2, None, state_path2, events_path2,
            phase138_function=lambda *a, **k: None,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert state_path2.read_bytes() == values2["before_state"]
    assert events_path2.read_bytes() == injected2


# --- H: Phase 138 stop routes stay strict against aged None ---------------
def test_h_phase138_stop_routes_aged_none_strict(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    values = _write_terminal_stop(d1, steps=8, fail=False)
    wf = values["workflow"]
    state_path, events_path = values["state_path"], values["events_path"]
    injected = _inject_aged_none(events_path)
    stop = _completion(wf, 8)
    with pytest.raises(Phase138Error) as caught:
        real_phase138(
            stop, wf, None, state_path, events_path,
            phase131_function=lambda *a, **k: None,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert state_path.read_bytes() == values["before_state"]
    assert events_path.read_bytes() == injected

    d2 = tmp_path / "d2"
    values2 = _write_terminal_stop(d2, steps=8, fail=True)
    wf2 = values2["workflow"]
    state_path2, events_path2 = values2["state_path"], values2["events_path"]
    injected2 = _inject_aged_none(events_path2)
    stop2 = _failure(wf2, 8)
    with pytest.raises(Phase138Error) as caught:
        real_phase138(
            stop2, wf2, None, state_path2, events_path2,
            phase131_function=lambda *a, **k: None,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert state_path2.read_bytes() == values2["before_state"]
    assert events_path2.read_bytes() == injected2


# --- shared low-level helpers ---------------------------------------------
def _write_terminal_stop(tmp_path: Path, *, steps: int, fail: bool) -> dict:
    """Terminal snapshot matching a canonical workflow_complete / persisted
    failure stop at the final step ``steps`` of a ``steps``-step workflow."""
    wf = _workflow(steps)
    last = steps
    if fail:
        state = WorkflowExecutionState(
            "w", "failed", wf.steps[last - 1].id, last, wf.steps[last - 1].employee,
            tuple(step.id for step in wf.steps[: last - 1]), "api_error",
        )
    else:
        state = WorkflowExecutionState(
            "w", "succeeded", wf.steps[last - 1].id, last, wf.steps[last - 1].employee,
            tuple(step.id for step in wf.steps[:last]), None,
        )
    events = [_predecessor_event(wf.steps[i].id, i + 1) for i in range(last - 1)]
    final = wf.steps[last - 1]
    if fail:
        events.append(
            RuntimeStepEvent(
                "step_failed", "w", final.id, last, final.employee, "running",
                "failed", "openai", "api_error", None, None, None, "safe failure",
            )
        )
    else:
        events.append(
            RuntimeStepEvent(
                "step_succeeded", "w", final.id, last, final.employee, "running",
                "succeeded", "openai", None, f"response-{final.id}",
                f"request-{final.id}", f"output-{final.id}", None,
            )
        )
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode("utf-8"))
    events_path.write_bytes(
        b"".join(serialize_runtime_step_event_jsonl(e).encode("utf-8") for e in events)
    )
    return {
        "workflow": wf,
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_path.read_bytes(),
        "before_events": events_path.read_bytes(),
    }


def _inject_aged_none(events_path: Path) -> bytes:
    import json

    lines = events_path.read_text(encoding="utf-8").splitlines()
    for idx in (4, 5):
        payload = json.loads(lines[idx])
        payload["request_id"] = None
        payload["provider"] = "openai"
        lines[idx] = json.dumps(payload, separators=(",", ":"))
    injected = ("\n".join(lines) + "\n").encode("utf-8")
    events_path.write_bytes(injected)
    return injected


def _completion(wf: WorkflowDefinition, index: int) -> WorkflowProgressionDecision:
    step = wf.steps[index - 1]
    return WorkflowProgressionDecision(
        "workflow_complete", "w", step.id, index, step.employee, None, None, None,
        "last_step_succeeded",
    )


def _failure(wf: WorkflowDefinition, index: int) -> PersistedExecutionOutcome:
    step = wf.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure", "w", step.id, index, step.employee, "api_error"
    )
