"""Focused and real-default regressions for Issue #400 Phase 183."""

# ruff: noqa: E501,E701,E702,F401,I001

import importlib.util
import inspect
import json
from pathlib import Path

import pytest

from ai_office.engine import (
    PersistedExecutionOutcome,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase182Error,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase183Error,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail,
    WorkflowProgressionDecision,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary as phase182,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_orchestration_boundary as phase183,
    route_runtime_result_to_progression_orchestration_boundary as phase172,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase155Error,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
)
from ai_office.storage import load_workflow_execution_state, serialize_runtime_step_event_jsonl

_HARNESS_PATH = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary.py"
)


def _harness():
    spec = importlib.util.spec_from_file_location("phase182_harness_for_183", _HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _phase179_harness():
    return _harness()._phase179_harness()


def _case(tmp_path: Path, *, steps: int = 8, current: int = 6) -> dict[str, object]:
    return _harness()._case(tmp_path, steps=steps, current=current)


def _args(case: dict[str, object], **overrides: object) -> list[object]:
    second = (
        _harness()._second_context(case)
        if case.get("next_employee") is not None
        else {"tools": None, "api_key": None, "approval": None}
    )
    names = (
        "result", "workflow", "preparation_approval", "employee", "state_path",
        "events_path", "resolved_tools", "api_key", "execution_approval",
        "transport", "next_preparation_approval", "next_employee",
        "next_resolved_tools", "next_api_key", "next_execution_approval",
        "next_transport",
    )
    values = (
        case["result"], case["workflow"], case["approval"], case["employee"],
        case["state_path"], case["events_path"], case["tools"], case["api_key"],
        case["execution_approval"], None, case["next_approval"], case["next_employee"],
        second["tools"], second["api_key"], second["approval"], None,
    )
    return [overrides.get(name, value) for name, value in zip(names, values, strict=True)]


def _call(case: dict[str, object], **kwargs: object) -> object:
    dependency_names = {"phase182_function", "phase172_function"}
    dependencies = {
        name: kwargs.pop(name) for name in tuple(kwargs) if name in dependency_names
    }
    return phase183(*_args(case, **kwargs), **dependencies)


def _classification(fn, expected: str) -> None:
    with pytest.raises(Phase183Error) as caught:
        fn()
    assert caught.value.detail.classification == expected
    assert "secret" not in str(caught.value)


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_01_public_signature_exports_defaults_and_source_audit() -> None:
    params = list(inspect.signature(phase183).parameters.values())
    assert [p.name for p in params[:16]] == [
        "result", "workflow", "preparation_approval", "employee", "state_path",
        "events_path", "resolved_tools", "api_key", "execution_approval", "transport",
        "next_preparation_approval", "next_employee", "next_resolved_tools", "next_api_key",
        "next_execution_approval", "next_transport",
    ]
    assert [p.name for p in params[16:]] == ["phase182_function", "phase172_function"]
    assert params[16].default is phase182
    assert params[17].default is phase172
    assert issubclass(
        Phase183Error,
        RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryError,
    )
    assert RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail
    source = Path(phase183.__code__.co_filename).read_text()
    assert source.count("phase182_function(") == 1
    assert source.count("phase172_function(") == 1
    assert "route_runtime_result_transition_persistence" not in source
    assert "route_persisted_transition_outcome_classification" not in source
    assert "route_classified_persisted_outcome_progression" not in source
    assert "route_progression_to_approved_preparation" not in source
    assert "while " not in source and "for " not in source[source.index("def route_") : source.index("def _check_inputs")]


def test_02_phase182_exactly_once_canonical_sixteen_positional_no_kwargs(tmp_path: Path) -> None:
    case = _case(tmp_path / "phase182")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def dependency(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return phase182(*args, **kwargs)

    out = _call(
        case,
        phase182_function=dependency,
        transport=case["h"].success_transport([]),  # type: ignore[index]
        next_transport=case["h"].success_transport([]),  # type: ignore[index]
    )
    assert type(out) is WorkflowProgressionDecision
    assert len(calls) == 1 and len(calls[0][0]) == 16 and calls[0][1] == {}
    assert calls[0][0][0] is case["result"] and calls[0][0][1] is case["workflow"]
    assert calls[0][0][2] is case["approval"] and calls[0][0][3] is case["employee"]
    assert calls[0][0][10] is case["next_approval"]
    assert calls[0][0][11] is case["next_employee"]
    assert calls[0][0][12] is case["tools"]


def test_03_phase172_exactly_once_four_positional_no_kwargs_and_exact_runtime_result(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "phase172")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def dependency(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return phase172(*args, **kwargs)

    out = _call(
        case,
        phase182_function=lambda *args: phase182(*args),
        phase172_function=dependency,
        transport=case["h"].success_transport([]),  # type: ignore[index]
        next_transport=case["h"].success_transport([]),  # type: ignore[index]
    )
    assert type(out) is WorkflowProgressionDecision
    assert len(calls) == 1 and len(calls[0][0]) == 4 and calls[0][1] == {}
    assert type(calls[0][0][0]) is StepRuntimeExecutionSuccess
    assert calls[0][0][0].step_index == 8
    assert calls[0][0][1] is case["workflow"]
    assert calls[0][0][2] is case["state_path"] and calls[0][0][3] is case["events_path"]


def test_04_phase182_runtime_result_is_linked_to_post_phase182_running_snapshot(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "linkage")
    phase172_calls: list[object] = []
    malformed = _harness()._runtime_success(case, 8)
    case["state_path"].write_text(case["state_path"].read_text().replace('"current_step_index":6', '"current_step_index":7'))  # type: ignore[index]
    _classification(
        lambda: _call(
            case,
            phase182_function=lambda *args: malformed,
            phase172_function=lambda *args: phase172_calls.append(args),
        ),
        "phase182_contract",
    )
    assert phase172_calls == []

    # Phase 183 must reuse the Phase 161 / Phase 155 predecessor provenance
    # contract rather than requiring every older event to be OpenAI.
    def establish_phase182(case: dict[str, object]) -> object:
        return phase182(
            *_args(
                case,
                transport=case["h"].success_transport([]),  # type: ignore[index]
                next_transport=case["h"].success_transport([]),  # type: ignore[index]
            )
        )

    def rewrite_event(case: dict[str, object], position: int, **changes: object) -> None:
        events = _events(case["events_path"])  # type: ignore[arg-type]
        events[position - 1].update(changes)
        case["events_path"].write_text(  # type: ignore[index]
            "".join(
                serialize_runtime_step_event_jsonl(RuntimeStepEvent(**event))
                for event in events
            ),
            encoding="utf-8",
        )

    older_non_openai = _case(tmp_path / "older-non-openai")
    older_result = establish_phase182(older_non_openai)
    rewrite_event(older_non_openai, 2, provider="other", request_id="older-request")
    older_out = phase183(
        *_args(older_non_openai),
        phase182_function=lambda *args: older_result,
        phase172_function=phase172,
    )
    assert type(older_out) is WorkflowProgressionDecision
    assert older_out.decision == "workflow_complete"

    immediate_none = _case(tmp_path / "immediate-none")
    immediate_result = establish_phase182(immediate_none)
    rewrite_event(immediate_none, 7, provider="openai", request_id=None)
    immediate_out = phase183(
        *_args(immediate_none),
        phase182_function=lambda *args: immediate_result,
        phase172_function=phase172,
    )
    assert type(immediate_out) is WorkflowProgressionDecision
    assert immediate_out.decision == "workflow_complete"

    immediate_non_openai = _case(tmp_path / "immediate-non-openai")
    immediate_non_openai_result = establish_phase182(immediate_non_openai)
    rewrite_event(
        immediate_non_openai,
        7,
        provider="other",
        request_id="immediate-request",
    )
    calls: list[object] = []
    _classification(
        lambda: phase183(
            *_args(immediate_non_openai),
            phase182_function=lambda *args: immediate_non_openai_result,
            phase172_function=lambda *args: calls.append(args),
        ),
        "phase182_contract",
    )
    assert calls == []

    for position in range(1, 5):
        incompatible = _case(tmp_path / f"early-none-{position}")
        incompatible_result = establish_phase182(incompatible)
        rewrite_event(incompatible, position, provider="openai", request_id=None)
        calls: list[object] = []
        _classification(
            lambda: phase183(
                *_args(incompatible),
                phase182_function=lambda *args: incompatible_result,
                phase172_function=lambda *args: calls.append(args),
            ),
            "phase182_contract",
        )
        assert calls == []

    for position in (5, 6, 7):
        incompatible = _case(tmp_path / f"aged-none-non-openai-{position}")
        incompatible_result = establish_phase182(incompatible)
        rewrite_event(incompatible, position, provider="other", request_id=None)
        calls: list[object] = []
        _classification(
            lambda: phase183(
                *_args(incompatible),
                phase182_function=lambda *args: incompatible_result,
                phase172_function=lambda *args: calls.append(args),
            ),
            "phase182_contract",
        )
        assert calls == []


def test_05_original_workflow_complete_exact_identity_unchanged_phase172_zero(
    tmp_path: Path,
) -> None:
    h = _phase179_harness()
    values = h.terminal_setup(tmp_path / "complete", steps=6)
    stop = h.complete_decision(values["workflow"], 6)
    before = (values["state_path"].read_bytes(), values["events_path"].read_bytes())
    calls: list[object] = []
    out = phase183(
        stop, values["workflow"], None, None, values["state_path"], values["events_path"],
        None, None, None, None, None, None, None, None, None, None,
        phase182_function=lambda *args: stop,
        phase172_function=lambda *args: calls.append(args),
    )
    assert out is stop and calls == []
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before


def test_06_original_persisted_failure_exact_identity_unchanged_phase172_zero(
    tmp_path: Path,
) -> None:
    h = _phase179_harness()
    values = h.terminal_setup(tmp_path / "failure", steps=6, fail=True)
    stop = h.failure_outcome(values["workflow"], 6)
    before = (values["state_path"].read_bytes(), values["events_path"].read_bytes())
    calls: list[object] = []
    out = phase183(
        stop, values["workflow"], None, None, values["state_path"], values["events_path"],
        None, None, None, None, None, None, None, None, None, None,
        phase182_function=lambda *args: stop,
        phase172_function=lambda *args: calls.append(args),
    )
    assert out is stop and calls == []
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before


def test_07_runtime_to_phase182_workflow_complete_stop_is_exact_and_phase172_zero(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "runtime-complete", steps=7)
    calls: list[object] = []
    out = _call(
        case,
        transport=case["h"].success_transport([]),  # type: ignore[index]
        next_transport=case["h"].success_transport([]),  # type: ignore[index]
        phase172_function=lambda *args: calls.append(args),
    )
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "workflow_complete" and out.current_step_index == 7
    assert calls == []


def test_08_runtime_to_phase182_persisted_failure_stop_is_exact_and_phase172_zero(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "runtime-failure")
    calls: list[object] = []
    out = _call(
        case,
        transport=case["h"].failure_transport([]),  # type: ignore[index]
        next_transport=case["h"].success_transport([]),  # type: ignore[index]
        phase172_function=lambda *args: calls.append(args),
    )
    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure" and out.current_step_index == 7
    assert calls == []


def test_09_strict_input_domain_subclasses_targets_and_dependency_configuration(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "domain")
    h = _harness()
    persisted_success = PersistedExecutionOutcome("persisted_success", "w", "step-6", 6, "e6", None)

    class ResultSubclass(type(case["result"])):
        pass

    subclass = ResultSubclass(
        case["result"].workflow_id, case["result"].step_id, case["result"].step_index,
        case["result"].employee_id, case["result"].invocation_result,
    )
    for value in (object(), subclass, h._phase179_harness().prepare_decision(case["workflow"], 6), persisted_success):
        _classification(lambda value=value: _call(case, result=value), "result_type")
    _classification(lambda: _call(case, workflow=object()), "workflow_definition")
    _classification(lambda: _call(case, state_path="bad"), "state_target")
    _classification(lambda: _call(case, events_path=object()), "event_target")
    _classification(lambda: _call(case, events_path=case["state_path"]), "target_conflict")
    _classification(lambda: _call(case, phase182_function=None), "configuration")
    _classification(lambda: _call(case, phase172_function=object()), "configuration")


def test_10_operational_inputs_are_not_prevalidated_before_phase182(tmp_path: Path) -> None:
    h = _phase179_harness()
    values = h.terminal_setup(tmp_path / "stop", steps=6)
    stop = h.complete_decision(values["workflow"], 6)
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        return stop

    out = phase183(
        stop, values["workflow"], object(), object(), values["state_path"], values["events_path"],
        object(), object(), object(), object(), object(), object(), object(), object(), object(), object(),
        phase182_function=dependency,
    )
    assert out is stop and len(calls) == 1 and len(calls[0]) == 16


def test_11_phase182_safe_error_identity_no_outer_rollback_phase172_zero(tmp_path: Path) -> None:
    case = _case(tmp_path / "safe")
    before = case["state_path"].read_bytes()
    safe = Phase182Error("dependency_error")
    phase172_calls: list[object] = []

    def dependency(*args: object) -> object:
        case["state_path"].write_bytes(before + b"changed")
        raise safe

    with pytest.raises(Phase182Error) as caught:
        _call(case, phase182_function=dependency, phase172_function=lambda *args: phase172_calls.append(args))
    assert caught.value is safe
    assert phase172_calls == [] and case["state_path"].read_bytes() == before + b"changed"


def test_12_phase182_unexpected_error_is_sanitized_without_outer_rollback(tmp_path: Path) -> None:
    case = _case(tmp_path / "unexpected")
    before = case["events_path"].read_bytes()
    phase172_calls: list[object] = []

    def dependency(*args: object) -> object:
        case["events_path"].write_bytes(before + b"secret")
        raise RuntimeError("secret provider payload")

    _classification(
        lambda: _call(case, phase182_function=dependency, phase172_function=lambda *args: phase172_calls.append(args)),
        "dependency_error",
    )
    assert phase172_calls == [] and case["events_path"].read_bytes() == before + b"secret"


def test_13_malformed_phase182_output_and_unlinked_stop_are_phase182_contract_no_phase172(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "malformed")
    calls: list[object] = []
    _classification(
        lambda: _call(case, phase182_function=lambda *args: object(), phase172_function=lambda *args: calls.append(args)),
        "phase182_contract",
    )
    assert calls == []
    case = _case(tmp_path / "stop-mismatch")
    stop = _phase179_harness().complete_decision(case["workflow"], 8)
    _classification(
        lambda: _call(case, phase182_function=lambda *args: stop, phase172_function=lambda *args: calls.append(args)),
        "phase182_contract",
    )
    assert calls == []

    # A workflow-complete stop is valid only at the final workflow step.
    non_final = _case(tmp_path / "non-final-stop", steps=8)
    _phase179_harness().terminal_setup(non_final["state_path"].parent, steps=7)
    non_final_stop = _phase179_harness().complete_decision(non_final["workflow"], 7)
    _classification(
        lambda: _call(
            non_final,
            phase182_function=lambda *args: non_final_stop,
            phase172_function=lambda *args: calls.append(args),
        ),
        "phase182_contract",
    )

    # Stop proof rejects corrupted canonical event fields, not just identity.
    corrupted = _case(tmp_path / "corrupt-stop", steps=8)
    _phase179_harness().terminal_setup(corrupted["state_path"].parent, steps=7)
    event_lines = _events(corrupted["events_path"])
    event_lines[-1]["provider"] = "synthetic-corruption"
    corrupted["events_path"].write_text(
        "".join(json.dumps(event) + "\n" for event in event_lines), encoding="utf-8"
    )
    corrupted_stop = _phase179_harness().complete_decision(corrupted["workflow"], 7)
    _classification(
        lambda: _call(
            corrupted,
            phase182_function=lambda *args: corrupted_stop,
            phase172_function=lambda *args: calls.append(args),
        ),
        "phase182_contract",
    )


def test_14_phase172_safe_error_identity_no_outer_rollback_no_retry(tmp_path: Path) -> None:
    case = _case(tmp_path / "safe172")
    safe = Phase172Error("phase161_contract")
    calls: list[object] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        case["state_path"].write_text("changed", encoding="utf-8")
        raise safe

    with pytest.raises(Phase172Error) as caught:
        _call(
            case,
            phase182_function=lambda *args: phase182(*args),
            phase172_function=dependency,
            transport=case["h"].success_transport([]),  # type: ignore[index]
            next_transport=case["h"].success_transport([]),  # type: ignore[index]
        )
    assert caught.value is safe and len(calls) == 1
    assert case["state_path"].read_text() == "changed"


def test_15_phase172_unexpected_error_is_sanitized_without_outer_rollback_or_retry(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "unexpected172")
    calls: list[object] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        case["events_path"].write_text("secret", encoding="utf-8")
        raise RuntimeError("secret detail")

    _classification(
        lambda: _call(
            case,
            phase172_function=dependency,
            transport=case["h"].success_transport([]),  # type: ignore[index]
            next_transport=case["h"].success_transport([]),  # type: ignore[index]
        ),
        "dependency_error",
    )
    assert len(calls) == 1 and case["events_path"].read_text() == "secret"


def test_16_malformed_phase172_output_keeps_phase172_owned_effects_without_outer_rollback(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "malformed172")
    calls: list[object] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        case["events_path"].write_text("phase172-owned", encoding="utf-8")
        return object()

    _classification(
        lambda: _call(
            case,
            phase172_function=dependency,
            transport=case["h"].success_transport([]),  # type: ignore[index]
            next_transport=case["h"].success_transport([]),  # type: ignore[index]
        ),
        "phase172_contract",
    )
    assert len(calls) == 1 and case["events_path"].read_text() == "phase172-owned"


def test_17_real_default_eight_step_success_persists_step8_once_and_completes(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "real-success")
    first_calls: list[object] = []
    second_calls: list[object] = []
    out = _call(
        case,
        transport=case["h"].success_transport(first_calls),  # type: ignore[index]
        next_transport=case["h"].success_transport(second_calls),  # type: ignore[index]
    )
    state = load_workflow_execution_state(case["state_path"])
    events = _events(case["events_path"])
    assert type(out) is WorkflowProgressionDecision and out.decision == "workflow_complete"
    assert len(first_calls) == 1 and len(second_calls) == 1
    assert state.status == "succeeded" and state.current_step_index == 8
    assert [event["step_id"] for event in events] == [f"step-{i}" for i in range(1, 9)]
    assert events[-1]["step_id"] == "step-8" and events[-1]["event_type"] == "step_succeeded"


def test_18_real_default_eight_step_failure_persists_step8_once_and_stops(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "real-failure")
    first_calls: list[object] = []
    second_calls: list[object] = []
    out = _call(
        case,
        transport=case["h"].success_transport(first_calls),  # type: ignore[index]
        next_transport=case["h"].failure_transport(second_calls),  # type: ignore[index]
    )
    state = load_workflow_execution_state(case["state_path"])
    events = _events(case["events_path"])
    assert type(out) is PersistedExecutionOutcome and out.outcome == "persisted_failure"
    assert out.failure_category == "api_error"
    assert len(first_calls) == 1 and len(second_calls) == 1
    assert state.status == "failed" and state.current_step_index == 8
    assert [event["step_id"] for event in events] == [f"step-{i}" for i in range(1, 9)]
    assert events[-1]["step_id"] == "step-8" and events[-1]["event_type"] == "step_failed"


def test_19_real_default_nine_step_success_returns_prepare_step9_and_stops(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "real-nine", steps=9)
    first_calls: list[object] = []
    second_calls: list[object] = []
    out = _call(
        case,
        transport=case["h"].success_transport(first_calls),  # type: ignore[index]
        next_transport=case["h"].success_transport(second_calls),  # type: ignore[index]
    )
    state = load_workflow_execution_state(case["state_path"])
    events = _events(case["events_path"])
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "prepare_next_step" and out.next_step_id == "step-9"
    assert out.next_step_index == 9 and out.reason == "next_step_available"
    assert len(first_calls) == 1 and len(second_calls) == 1
    assert state.status == "succeeded" and state.current_step_index == 8
    assert len(events) == 8 and all(event["step_id"] != "step-9" for event in events)


def test_20_real_default_stop_and_error_separation_keeps_zero_phase172_and_running_snapshot(
    tmp_path: Path,
) -> None:
    h = _phase179_harness()
    for label, failed in (("complete", False), ("failure", True)):
        values = h.terminal_setup(tmp_path / label, steps=6, fail=failed)
        stop = h.failure_outcome(values["workflow"], 6) if failed else h.complete_decision(values["workflow"], 6)
        calls: list[object] = []
        out = phase183(
            stop, values["workflow"], None, None, values["state_path"], values["events_path"],
            None, None, None, None, None, None, None, None, None, None,
            phase172_function=lambda *args: calls.append(args),
        )
        assert out is stop and calls == []

    case = _case(tmp_path / "invalid-approval")
    first_calls: list[object] = []
    second_calls: list[object] = []
    post_phase182: list[tuple[bytes, bytes]] = []

    def observe_phase182(*args: object) -> object:
        value = phase182(*args)
        post_phase182.append((case["state_path"].read_bytes(), case["events_path"].read_bytes()))
        return value

    with pytest.raises(Phase155Error) as caught:
        _call(
            case,
            phase182_function=observe_phase182,
            next_execution_approval=None,
            transport=case["h"].success_transport(first_calls),  # type: ignore[index]
            next_transport=case["h"].success_transport(second_calls),  # type: ignore[index]
        )
    assert caught.value.detail.classification == "approval_contract"
    assert len(post_phase182) == 0
    assert len(first_calls) == 1 and len(second_calls) == 0
    assert load_workflow_execution_state(case["state_path"]).current_step_index == 8
    assert "step-8" not in case["events_path"].read_text()
