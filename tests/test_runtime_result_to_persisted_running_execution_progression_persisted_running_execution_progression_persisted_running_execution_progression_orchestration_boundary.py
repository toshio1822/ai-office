"""Focused Issue #410 Phase 188 contract tests."""

# ruff: noqa: E501,E701,E702,F401,I001

from __future__ import annotations

import importlib.util
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.engine import (
    PersistedExecutionOutcome,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase188Error,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail,
    WorkflowProgressionDecision,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_persisted_running_execution_progression_orchestration_boundary as phase188,
)
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationError as Phase144Error,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationError as Phase143Error,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError as Phase172CompatibilityError,
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
    route_runtime_result_to_progression_orchestration_boundary as phase172,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase161Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary import (
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary as phase187,
)
from ai_office.runtime import RuntimeStepEvent, StepRuntimeExecutionFailure, StepRuntimeExecutionSuccess
from ai_office.storage import load_workflow_execution_state, serialize_runtime_step_event_jsonl

_PHASE187_TEST = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary.py"
)
_TERMINAL_TEST = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_orchestration_boundary.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _h():
    return _load(_PHASE187_TEST, "phase187_harness_for_188")


def _terminal():
    return _load(_TERMINAL_TEST, "terminal_harness_for_188")


def _case(tmp_path: Path, *, steps: int = 9) -> dict[str, object]:
    return _h()._case(tmp_path, steps=steps)


def _args(case: dict[str, object], *, first_transport=None, next_transport=None, following_transport=None) -> list[object]:
    args = _h()._args(
        case,
        _h()._contexts(case),
        first_transport=first_transport,
        next_transport=next_transport,
    )
    args[-1] = following_transport
    return args


def _call(case: dict[str, object], **dependencies: object) -> object:
    first_calls: list[object] = []
    second_calls: list[object] = []
    following_transport = dependencies.pop("following_transport", None)
    if following_transport is None:
        following_transport = case["h"].success_transport([])  # type: ignore[attr-defined]
    args = _args(
        case,
        first_transport=case["h"].success_transport(first_calls),  # type: ignore[attr-defined]
        next_transport=case["h"].success_transport(second_calls),  # type: ignore[attr-defined]
        following_transport=following_transport,
    )
    return phase188(*args, **dependencies)


def _terminal_case(tmp_path: Path, *, failed: bool):
    values = _terminal().terminal_setup(tmp_path, steps=9, fail=failed)
    stop = _terminal().failure_outcome(values["workflow"], 9) if failed else _terminal().complete_decision(values["workflow"], 9)
    return values, stop


def _rewrite_event(path: Path, position: int, **changes: object) -> None:
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    lines[position - 1].update(changes)
    path.write_text(
        "".join(
            serialize_runtime_step_event_jsonl(RuntimeStepEvent(**line))
            for line in lines
        )
    )


def _err(callable_, classification: str) -> None:
    with pytest.raises(Phase188Error) as caught:
        callable_()
    assert caught.value.detail.classification == classification
    assert "secret" not in str(caught.value)


def test_01_public_api_signature_exports_defaults_error_hierarchy_and_source_audit() -> None:
    params = list(inspect.signature(phase188).parameters.values())
    assert [param.name for param in params[:22]] == [
        "result", "workflow", "preparation_approval", "employee", "state_path", "events_path",
        "resolved_tools", "api_key", "execution_approval", "transport", "next_preparation_approval",
        "next_employee", "next_resolved_tools", "next_api_key", "next_execution_approval", "next_transport",
        "following_preparation_approval", "following_employee", "following_resolved_tools", "following_api_key",
        "following_execution_approval", "following_transport",
    ]
    assert [param.name for param in params[22:]] == ["phase187_function", "phase172_function"]
    assert params[22].default is phase187 and params[23].default is phase172
    assert issubclass(Phase188Error, RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryError)
    assert RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail
    source = Path(phase188.__code__.co_filename).read_text()
    assert source.count("phase187_function(") == 1
    assert source.count("phase172_function(") == 1
    for forbidden in (
        "route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(",
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(",
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(",
        "route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(",
    ):
        assert forbidden not in source
    route_source = source[source.index("def route_") : source.index("def _check_inputs")]
    assert "while " not in route_source and "for " not in route_source


def test_02_phase187_exactly_once_twenty_two_positional_no_kwargs_and_identity(tmp_path: Path) -> None:
    case = _case(tmp_path / "phase187")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def dependency(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return phase187(*args, **kwargs)

    out = _call(case, phase187_function=dependency, phase172_function=phase172)
    assert type(out) is WorkflowProgressionDecision
    assert len(calls) == 1 and len(calls[0][0]) == 22 and calls[0][1] == {}
    assert calls[0][0][0] is case["result"] and calls[0][0][1] is case["workflow"]
    assert calls[0][0][16] is case["following_approval"]
    assert calls[0][0][17] is case["following_employee"]


def test_03_phase172_exactly_once_four_positional_no_kwargs_and_phase187_result_identity(tmp_path: Path) -> None:
    case = _case(tmp_path / "phase172")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    produced: list[object] = []

    def p187(*args: object) -> object:
        value = phase187(*args)
        produced.append(value)
        return value

    def p172(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return phase172(*args, **kwargs)

    out = _call(case, phase187_function=p187, phase172_function=p172)
    assert type(out) is WorkflowProgressionDecision
    assert len(produced) == 1 and len(calls) == 1
    assert len(calls[0][0]) == 4 and calls[0][1] == {}
    assert calls[0][0][0] is produced[0]
    assert calls[0][0][1] is case["workflow"]
    assert calls[0][0][2] is case["state_path"] and calls[0][0][3] is case["events_path"]


def test_04_original_workflow_complete_identity_phase172_zero_targets_unchanged(tmp_path: Path) -> None:
    values, stop = _terminal_case(tmp_path / "complete", failed=False)
    before = (values["state_path"].read_bytes(), values["events_path"].read_bytes())
    calls: list[object] = []
    args = [stop, values["workflow"], None, None, values["state_path"], values["events_path"], None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None]
    out = phase188(*args, phase187_function=lambda *a: stop, phase172_function=lambda *a: calls.append(a))
    assert out is stop and calls == []
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before


def test_05_original_persisted_failure_identity_phase172_zero_targets_unchanged(tmp_path: Path) -> None:
    values, stop = _terminal_case(tmp_path / "failure", failed=True)
    before = (values["state_path"].read_bytes(), values["events_path"].read_bytes())
    calls: list[object] = []
    args = [stop, values["workflow"], None, None, values["state_path"], values["events_path"], None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None]
    out = phase188(*args, phase187_function=lambda *a: stop, phase172_function=lambda *a: calls.append(a))
    assert out is stop and calls == []
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before


def test_06_phase187_generated_workflow_complete_is_exact_stop_and_keeps_phase187_bytes(tmp_path: Path) -> None:
    case = _case(tmp_path / "generated-complete")
    source = _terminal().terminal_setup(tmp_path / "source", steps=9)
    stop = _terminal().complete_decision(source["workflow"], 9)
    before = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
    phase172_calls: list[object] = []

    def p187(*args: object) -> object:
        case["state_path"].write_bytes(source["state_path"].read_bytes())
        case["events_path"].write_bytes(source["events_path"].read_bytes())
        return stop

    out = _call(case, phase187_function=p187, phase172_function=lambda *a: phase172_calls.append(a))
    assert out is stop and phase172_calls == []
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) != before
    assert case["state_path"].read_bytes() == source["state_path"].read_bytes()


def test_07_phase187_generated_persisted_failure_is_exact_stop_and_keeps_phase187_bytes(tmp_path: Path) -> None:
    case = _case(tmp_path / "generated-failure")
    source = _terminal().terminal_setup(tmp_path / "source", steps=9, fail=True)
    stop = _terminal().failure_outcome(source["workflow"], 9)
    phase172_calls: list[object] = []

    def p187(*args: object) -> object:
        case["state_path"].write_bytes(source["state_path"].read_bytes())
        case["events_path"].write_bytes(source["events_path"].read_bytes())
        return stop

    out = _call(case, phase187_function=p187, phase172_function=lambda *a: phase172_calls.append(a))
    assert out is stop and phase172_calls == []
    assert case["state_path"].read_bytes() == source["state_path"].read_bytes()
    assert case["events_path"].read_bytes() == source["events_path"].read_bytes()


def test_08_real_default_nine_step_success_persists_step9_and_completes(tmp_path: Path) -> None:
    case = _case(tmp_path / "success")
    first: list[object] = []
    second: list[object] = []
    third: list[object] = []
    out = phase188(*_args(case, first_transport=case["h"].success_transport(first), next_transport=case["h"].success_transport(second), following_transport=case["h"].success_transport(third)))  # type: ignore[attr-defined]
    state = load_workflow_execution_state(case["state_path"])
    events = [json.loads(line) for line in case["events_path"].read_text().splitlines() if line.strip()]
    assert type(out) is WorkflowProgressionDecision and out.decision == "workflow_complete"
    assert len(first) == len(second) == len(third) == 1
    assert state.status == "succeeded" and state.current_step_index == 9
    assert len(events) == 9 and events[-1]["step_id"] == "step-9"


def test_09_real_default_nine_step_failure_persists_step9_and_stops(tmp_path: Path) -> None:
    case = _case(tmp_path / "failure")
    first: list[object] = []
    second: list[object] = []
    third: list[object] = []
    out = phase188(*_args(case, first_transport=case["h"].success_transport(first), next_transport=case["h"].success_transport(second), following_transport=case["h"].failure_transport(third)))  # type: ignore[attr-defined]
    state = load_workflow_execution_state(case["state_path"])
    events = [json.loads(line) for line in case["events_path"].read_text().splitlines() if line.strip()]
    assert type(out) is PersistedExecutionOutcome and out.outcome == "persisted_failure"
    assert out.failure_category == "api_error"
    assert len(first) == len(second) == len(third) == 1
    assert state.status == "failed" and state.current_step_index == 9
    assert len(events) == 9 and events[-1]["event_type"] == "step_failed"


def test_10_real_default_ten_step_success_returns_prepare_step10_without_readvance(tmp_path: Path) -> None:
    case = _case(tmp_path / "ten", steps=10)
    first: list[object] = []
    second: list[object] = []
    third: list[object] = []
    out = phase188(*_args(case, first_transport=case["h"].success_transport(first), next_transport=case["h"].success_transport(second), following_transport=case["h"].success_transport(third)))  # type: ignore[attr-defined]
    state = load_workflow_execution_state(case["state_path"])
    events = [json.loads(line) for line in case["events_path"].read_text().splitlines() if line.strip()]
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "prepare_next_step" and out.next_step_index == 10 and out.next_step_id == "step-10"
    assert state.status == "succeeded" and state.current_step_index == 9
    assert len(events) == 9 and all(event["step_id"] != "step-10" for event in events)
    assert len(first) == len(second) == len(third) == 1


def test_11_provenance_compatibility_is_narrow_and_reuses_phase172(tmp_path: Path) -> None:
    for label, provider, request_id, valid in (
        ("aged-openai-none", "openai", None, True),
        ("older-non-openai", "other", "older-request", True),
        ("early-none", "openai", None, False),
        ("non-openai-none", "other", None, False),
    ):
        case = _case(tmp_path / label)
        position = 5 if label in ("aged-openai-none", "non-openai-none") else 2
        calls: list[object] = []

        def phase187_after_step9(*args: object) -> object:
            value = phase187(*args)
            _rewrite_event(
                case["events_path"],
                position,
                provider=provider,
                request_id=request_id,
            )
            return value

        if valid:
            out = _call(
                case,
                phase187_function=phase187_after_step9,
                following_transport=case["h"].failure_transport(calls),  # type: ignore[attr-defined]
            )
            assert type(out) is PersistedExecutionOutcome and len(calls) == 1
        else:
            with pytest.raises(Phase161Error) as caught:
                _call(
                    case,
                    phase187_function=phase187_after_step9,
                    following_transport=case["h"].failure_transport(calls),  # type: ignore[attr-defined]
                )
            assert caught.value.detail.classification == "runtime_contract"
            assert len(calls) == 1


def test_12_recognized_phase187_safe_errors_preserve_identity_zero_phase172_and_no_rollback(tmp_path: Path) -> None:
    impl = _load(Path(phase188.__code__.co_filename), "phase188_impl_for_safe_errors")
    safe_types = tuple(impl._SAFE_PHASE187_ERRORS)
    for index, safe_type in enumerate(safe_types):
        case = _case(tmp_path / str(index))
        before = case["state_path"].read_bytes()
        error = safe_type("safe")
        phase172_calls: list[object] = []

        def p187(*args: object, error=error) -> object:
            case["state_path"].write_bytes(before + b"phase187-owned")
            raise error

        with pytest.raises(safe_type) as caught:
            _call(case, phase187_function=p187, phase172_function=lambda *a: phase172_calls.append(a))
        assert caught.value is error and phase172_calls == []
        assert case["state_path"].read_bytes() == before + b"phase187-owned"


def test_13_unexpected_phase187_exception_is_sanitized_without_outer_rollback(tmp_path: Path) -> None:
    case = _case(tmp_path / "unexpected")
    before = case["events_path"].read_bytes()
    phase172_calls: list[object] = []

    def p187(*args: object) -> object:
        case["events_path"].write_bytes(before + b"secret")
        raise RuntimeError("secret provider payload")

    _err(lambda: _call(case, phase187_function=p187, phase172_function=lambda *a: phase172_calls.append(a)), "dependency_error")
    assert phase172_calls == [] and case["events_path"].read_bytes() == before + b"secret"


def test_14_malformed_substitute_and_wrong_step_phase187_outputs_are_contract_errors(tmp_path: Path) -> None:
    for index, mode in enumerate(("object", "substitute", "wrong-step")):
        case = _case(tmp_path / str(index))
        valid = _h()._runtime(case)
        if mode == "object":
            value: object = object()
        elif mode == "substitute":
            class Substitute(StepRuntimeExecutionSuccess):
                pass
            value = Substitute(valid.workflow_id, valid.step_id, valid.step_index, valid.employee_id, valid.invocation_result)
        else:
            value = replace(valid, step_index=8)
        phase172_calls: list[object] = []
        _err(lambda value=value: _call(case, phase187_function=lambda *a, value=value: value, phase172_function=lambda *a: phase172_calls.append(a)), "phase187_contract")
        assert phase172_calls == []


def test_15_inconsistent_post_phase187_snapshot_is_contract_error_and_phase187_bytes_remain(tmp_path: Path) -> None:
    case = _case(tmp_path / "snapshot")
    before = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
    phase172_calls: list[object] = []

    def p187(*args: object) -> object:
        value = phase187(*args)
        snapshot = json.loads(case["state_path"].read_text())
        snapshot["current_step_id"] = "wrong-step"
        case["state_path"].write_text(json.dumps(snapshot))
        return value

    _err(lambda: _call(case, phase187_function=p187, phase172_function=lambda *a: phase172_calls.append(a)), "phase187_contract")
    assert phase172_calls == []
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) != before


def test_16_recognized_phase172_safe_errors_preserve_identity_no_outer_rollback_or_retry(tmp_path: Path) -> None:
    safe_types = (Phase172Error, Phase172CompatibilityError, Phase161Error, Phase143Error, Phase144Error)
    for index, safe_type in enumerate(safe_types):
        case = _case(tmp_path / str(index))
        before = case["state_path"].read_bytes()
        error = safe_type("safe")
        calls: list[object] = []

        def p172(*args: object, error=error) -> object:
            calls.append(args)
            case["state_path"].write_bytes(before + b"phase172-owned")
            raise error

        with pytest.raises(safe_type) as caught:
            _call(case, phase172_function=p172)
        assert caught.value is error and len(calls) == 1
        assert case["state_path"].read_bytes() == before + b"phase172-owned"


def test_17_unexpected_phase172_exception_is_sanitized_without_outer_rollback_or_retry(tmp_path: Path) -> None:
    case = _case(tmp_path / "unexpected")
    before = case["events_path"].read_bytes()
    calls: list[object] = []

    def p172(*args: object) -> object:
        calls.append(args)
        case["events_path"].write_bytes(before + b"phase172-owned")
        raise RuntimeError("secret")

    _err(lambda: _call(case, phase172_function=p172), "dependency_error")
    assert len(calls) == 1 and case["events_path"].read_bytes() == before + b"phase172-owned"


def test_18_malformed_substitute_and_inconsistent_phase172_outputs_are_contract_errors_once(tmp_path: Path) -> None:
    for index, mode in enumerate(("object", "wrong-decision", "mutated")):
        case = _case(tmp_path / str(index))
        calls: list[object] = []

        def p172(*args: object, mode=mode) -> object:
            calls.append(args)
            if mode == "object":
                return object()
            if mode == "wrong-decision":
                return WorkflowProgressionDecision("w", "step-9", 9, "e9", "prepare_next_step", "step-10", 10, "e10", "next_step_available")
            case["events_path"].write_text("phase172-owned")
            return object()

        _err(lambda: _call(case, phase172_function=p172), "phase172_contract")
        assert len(calls) == 1


def test_19_phase172_valid_result_is_returned_exactly_and_phase188_does_not_reenter(tmp_path: Path) -> None:
    case = _case(tmp_path / "identity")
    result: list[object] = []

    def p172(*args: object) -> object:
        value = phase172(*args)
        result.append(value)
        return value

    out = _call(case, phase172_function=p172)
    assert len(result) == 1 and out is result[0]


def test_20_explicit_no_readvance_source_audit_and_only_public_dependencies() -> None:
    source = Path(phase188.__code__.co_filename).read_text()
    assert "route_runtime_result_to_progression_orchestration_boundary" in source
    assert "route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary" in source
    for forbidden in (
        "route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(",
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(",
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(",
        "route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(",
        "route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(",
    ):
        assert forbidden not in source
    assert "retry" not in source.lower()
    assert "automatic continuation" not in source.lower()
    assert "step-10" not in source
