"""Focused Issue #406 Phase 186 orchestration contract tests."""

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
    PreparedStepExecutionStart,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase186Error,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryFailureDetail,
    WorkflowProgressionDecision,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary as phase186,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase147Error,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase147,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as Phase139Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase183Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase182Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase178Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase181Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryError as Phase180Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError as Phase179Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase184CompatibilityError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError as Phase184Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError as Phase185Error,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_step_start_orchestration_boundary as phase185,
)
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    load_workflow_execution_state,
    serialize_workflow_execution_state_json,
)

_PHASE185_TEST_PATH = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_step_start_orchestration_boundary.py"
)
_PHASE184_TEST_PATH = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_approved_preparation_orchestration_boundary.py"
)
_TERMINAL_TEST_PATH = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_orchestration_boundary.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _harness():
    return _load(_PHASE185_TEST_PATH, "phase185_harness_for_186")


def _terminal_harness():
    return _load(_TERMINAL_TEST_PATH, "terminal_harness_for_186")


def _prepared_case(tmp_path: Path) -> tuple[dict[str, object], PreparedStepExecutionStart]:
    h = _harness()
    case = h._case(tmp_path)
    first: list[object] = []
    second: list[object] = []
    start = phase185(
        *h._args(
            case,
            transport=case["h"].success_transport(first),
            next_transport=case["h"].success_transport(second),
        )
    )
    assert type(start) is PreparedStepExecutionStart
    assert len(first) == 1 and len(second) == 1
    case["phase185_state_bytes"] = case["state_path"].read_bytes()
    case["phase185_event_bytes"] = case["events_path"].read_bytes()
    return case, start


def _call(case: dict[str, object], **overrides: object) -> object:
    dependencies = {
        name: overrides.pop(name)
        for name in tuple(overrides)
        if name in {"phase185_function", "phase147_function"}
    }
    return phase186(*_harness()._args(case, **overrides), **dependencies)


def _terminal_case(tmp_path: Path, *, failed: bool = False) -> tuple[dict[str, object], object]:
    h = _terminal_harness()
    values = h.terminal_setup(tmp_path, steps=6, fail=failed)
    stop = h.failure_outcome(values["workflow"], 6) if failed else h.complete_decision(values["workflow"], 6)
    case: dict[str, object] = {
        "result": stop,
        "workflow": values["workflow"],
        "approval": None,
        "employee": None,
        "state_path": values["state_path"],
        "events_path": values["events_path"],
        "tools": None,
        "api_key": None,
        "execution_approval": None,
        "next_approval": None,
        "next_employee": None,
        "next_execution_approval": None,
        "following_approval": None,
        "following_employee": None,
        "before": (values["state_path"].read_bytes(), values["events_path"].read_bytes()),
    }
    return case, stop


def _stop_call(case: dict[str, object], **overrides: object) -> object:
    values = {
        "result": case["result"],
        "workflow": case["workflow"],
        "preparation_approval": None,
        "employee": None,
        "state_path": case["state_path"],
        "events_path": case["events_path"],
        "resolved_tools": None,
        "api_key": None,
        "execution_approval": None,
        "transport": None,
        "next_preparation_approval": None,
        "next_employee": None,
        "next_resolved_tools": None,
        "next_api_key": None,
        "next_execution_approval": None,
        "next_transport": None,
        "following_preparation_approval": None,
        "following_employee": None,
    }
    values.update(overrides)
    dependencies = {
        name: values.pop(name)
        for name in ("phase185_function", "phase147_function")
        if name in values
    }
    return phase186(*[values[key] for key in (
        "result", "workflow", "preparation_approval", "employee", "state_path", "events_path",
        "resolved_tools", "api_key", "execution_approval", "transport", "next_preparation_approval",
        "next_employee", "next_resolved_tools", "next_api_key", "next_execution_approval",
        "next_transport", "following_preparation_approval", "following_employee",
    )], **dependencies)


def _write_expected_running(start: PreparedStepExecutionStart, state_path: Path) -> RunningStatePersistenceResult:
    data = serialize_workflow_execution_state_json(start.running_state).encode()
    state_path.write_bytes(data)
    return RunningStatePersistenceResult(len(data))


def _classification(fn, expected: str) -> None:
    with pytest.raises(Phase186Error) as caught:
        fn()
    assert caught.value.detail.classification == expected
    assert "secret" not in str(caught.value)


def _rewrite_event(path: Path, position: int, **changes: object) -> None:
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    lines[position - 1].update(changes)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))


def test_01_public_signature_exports_defaults_and_source_audit() -> None:
    params = list(inspect.signature(phase186).parameters.values())
    assert [param.name for param in params[:18]] == [
        "result", "workflow", "preparation_approval", "employee", "state_path", "events_path",
        "resolved_tools", "api_key", "execution_approval", "transport", "next_preparation_approval",
        "next_employee", "next_resolved_tools", "next_api_key", "next_execution_approval", "next_transport",
        "following_preparation_approval", "following_employee",
    ]
    assert [param.name for param in params[18:]] == ["phase185_function", "phase147_function"]
    assert params[18].default is phase185
    assert params[19].default is phase147
    assert issubclass(
        Phase186Error,
        RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryError,
    )
    assert RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryFailureDetail
    source = Path(phase186.__code__.co_filename).read_text()
    assert source.count("phase185_function(") == 1
    assert source.count("phase147_function(") == 1
    assert "phase139_function(" not in source and "phase132_function(" not in source
    assert "phase155" not in source
    assert "while " not in source[source.index("def route_"):source.index("def _check_inputs")]


def test_02_phase185_exactly_once_with_canonical_eighteen_positional_no_kwargs(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "calls")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def dependency(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return start

    out = _call(case, phase185_function=dependency, phase147_function=lambda *args: _write_expected_running(start, args[3]))
    assert type(out) is RunningStatePersistenceResult
    assert len(calls) == 1 and len(calls[0][0]) == 18 and calls[0][1] == {}
    assert calls[0][0][0] is case["result"] and calls[0][0][1] is case["workflow"]
    assert calls[0][0][2] is case["approval"] and calls[0][0][3] is case["employee"]
    assert calls[0][0][10] is case["next_approval"] and calls[0][0][11] is case["next_employee"]
    assert calls[0][0][16] is case["following_approval"] and calls[0][0][17] is case["following_employee"]


def test_03_phase147_exactly_once_with_canonical_five_positional_following_employee(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "phase147")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def dependency(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return _write_expected_running(start, args[3])

    out = _call(case, phase185_function=lambda *args: start, phase147_function=dependency)
    assert type(out) is RunningStatePersistenceResult
    assert len(calls) == 1 and len(calls[0][0]) == 5 and calls[0][1] == {}
    assert calls[0][0] == (start, case["workflow"], case["following_employee"], case["state_path"], case["events_path"])


def test_04_real_default_phase185_to_phase147_persists_step9_and_never_executes_it(tmp_path: Path) -> None:
    h = _harness()
    case = h._case(tmp_path / "real")
    first: list[object] = []
    second: list[object] = []
    out = phase186(
        *h._args(
            case,
            transport=case["h"].success_transport(first),
            next_transport=case["h"].success_transport(second),
        )
    )
    assert type(out) is RunningStatePersistenceResult
    state = load_workflow_execution_state(case["state_path"])
    assert state.status == "running"
    assert state.current_step_id == "step-9" and state.current_step_index == 9
    assert state.current_employee_id == "e9"
    assert state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 9))
    assert state.last_failure_category is None
    events = [json.loads(line) for line in case["events_path"].read_text().splitlines() if line.strip()]
    assert len(events) == 8 and all(event["step_id"] != "step-9" for event in events)
    assert len(first) == 1 and len(second) == 1


def test_05_following_employee_is_the_step9_owner_not_first_or_next_employee(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "ownership")
    seen: list[object] = []

    def dependency(*args: object) -> object:
        seen.append(args[2])
        return _write_expected_running(start, args[3])

    out = _call(case, phase185_function=lambda *args: start, phase147_function=dependency)
    assert type(out) is RunningStatePersistenceResult
    assert seen == [case["following_employee"]]
    assert seen[0] is not case["employee"] and seen[0] is not case["next_employee"]


def test_06_original_workflow_complete_is_identity_and_phase147_zero_call(tmp_path: Path) -> None:
    case, stop = _terminal_case(tmp_path / "complete")
    calls: list[object] = []
    out = _stop_call(case, phase185_function=lambda *args: stop, phase147_function=lambda *args: calls.append(args))
    assert out is stop and calls == []
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == case["before"]


def test_07_original_persisted_failure_is_identity_and_phase147_zero_call(tmp_path: Path) -> None:
    case, stop = _terminal_case(tmp_path / "failure", failed=True)
    calls: list[object] = []
    out = _stop_call(case, phase185_function=lambda *args: stop, phase147_function=lambda *args: calls.append(args))
    assert out is stop and calls == []
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == case["before"]


def test_08_original_stop_mutation_is_phase185_contract_without_pre_phase185_rollback(tmp_path: Path) -> None:
    case, stop = _terminal_case(tmp_path / "mutation")
    before = case["before"]
    owned_state = before[0] + b"phase185-owned"
    owned_events = before[1] + b"phase185-owned"
    phase147_calls: list[object] = []

    def dependency(*args: object) -> object:
        case["state_path"].write_bytes(owned_state)
        case["events_path"].write_bytes(owned_events)
        return stop

    _classification(
        lambda: _stop_call(case, phase185_function=dependency, phase147_function=lambda *args: phase147_calls.append(args)),
        "phase185_contract",
    )
    assert phase147_calls == []
    assert case["state_path"].read_bytes() == owned_state
    assert case["events_path"].read_bytes() == owned_events


def test_09_real_phase185_workflow_complete_and_failure_stops_skip_phase147(tmp_path: Path) -> None:
    for label, steps, failed in (("complete", 7, False), ("failure", 8, True)):
        h = _harness()
        case = h._case(tmp_path / label, steps=steps)
        first: list[object] = []
        second: list[object] = []
        phase147_calls: list[object] = []
        out = phase186(
            *h._args(
                case,
                transport=case["h"].success_transport(first),
                next_transport=case["h"].failure_transport(second) if failed else case["h"].success_transport(second),
            ),
            phase147_function=lambda *args: phase147_calls.append(args),
        )
        assert (type(out) is PersistedExecutionOutcome) is failed
        assert (type(out) is WorkflowProgressionDecision) is not failed
        assert phase147_calls == []
        assert len(first) == 1 and len(second) == (1 if failed else 0)
        assert case["events_path"].read_bytes() != case["events_before"]


def test_10_phase185_owned_operational_inputs_are_not_prevalidated_before_dependency(tmp_path: Path) -> None:
    case, stop = _terminal_case(tmp_path / "no-prevalidation")
    seen: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        seen.append(args)
        return stop

    out = _stop_call(
        case,
        preparation_approval=object(), employee=object(), resolved_tools=object(), api_key=object(),
        execution_approval=object(), transport=object(), next_preparation_approval=object(),
        next_employee=object(), next_resolved_tools=object(), next_api_key=object(),
        next_execution_approval=object(), next_transport=object(), following_preparation_approval=object(),
        following_employee=object(), phase185_function=dependency,
        phase147_function=lambda *args: pytest.fail("Phase147 called"),
    )
    assert out is stop and len(seen) == 1 and len(seen[0]) == 18


def test_11_phase185_safe_error_identity_has_no_pre_phase185_rollback(tmp_path: Path) -> None:
    for error_type in (
        Phase185Error,
        Phase184Error,
        Phase184CompatibilityError,
        Phase183Error,
        Phase182Error,
        Phase181Error,
        Phase180Error,
        Phase179Error,
        Phase178Error,
    ):
        case, _ = _prepared_case(tmp_path / error_type.__name__)
        before = case["state_path"].read_bytes()
        safe = error_type("safe")
        phase147_calls: list[object] = []

        def dependency(*args: object, before=before, safe=safe) -> object:
            case["state_path"].write_bytes(before + b"owned")
            raise safe

        with pytest.raises(error_type) as caught:
            _call(
                case,
                phase185_function=dependency,
                phase147_function=lambda *args: phase147_calls.append(args),
            )
        assert caught.value is safe
        assert phase147_calls == []
        assert case["state_path"].read_bytes() == before + b"owned"


def test_12_unexpected_phase185_error_is_sanitized_without_rollback(tmp_path: Path) -> None:
    case, _ = _prepared_case(tmp_path / "unexpected")
    before = case["events_path"].read_bytes()

    def dependency(*args: object) -> object:
        case["events_path"].write_bytes(before + b"owned")
        raise RuntimeError("secret provider payload")

    _classification(lambda: _call(case, phase185_function=dependency, phase147_function=lambda *args: pytest.fail("called")), "dependency_error")
    assert case["events_path"].read_bytes() == before + b"owned"


def test_13_malformed_phase185_output_is_contract_error_without_rollback(tmp_path: Path) -> None:
    case, _ = _prepared_case(tmp_path / "malformed")
    before = case["state_path"].read_bytes()

    def dependency(*args: object) -> object:
        case["state_path"].write_bytes(before + b"owned")
        return object()

    _classification(lambda: _call(case, phase185_function=dependency, phase147_function=lambda *args: pytest.fail("called")), "phase185_contract")
    assert case["state_path"].read_bytes() == before + b"owned"


def test_14_provenance_narrowness_reuses_phase147_and_phase139_contracts(tmp_path: Path) -> None:
    valid = _prepared_case(tmp_path / "aged-none")[0]
    valid_start = _prepared_case(tmp_path / "aged-none-start")[1]
    _rewrite_event(valid["events_path"], 5, provider="openai", request_id=None)
    assert type(_call(valid, phase185_function=lambda *args: valid_start)) is RunningStatePersistenceResult

    older = _prepared_case(tmp_path / "older")[0]
    older_start = _prepared_case(tmp_path / "older-start")[1]
    _rewrite_event(older["events_path"], 2, provider="anthropic", request_id="older-request")
    assert type(_call(older, phase185_function=lambda *args: older_start)) is RunningStatePersistenceResult

    for label, position, changes in (("early", 4, {"provider": "openai", "request_id": None}), ("non-openai", 5, {"provider": "anthropic", "request_id": None})):
        case, start = _prepared_case(tmp_path / label)
        _rewrite_event(case["events_path"], position, **changes)
        before = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
        with pytest.raises((Phase147Error, Phase139Error)) as caught:
            _call(case, phase185_function=lambda *args, start=start: start)
        assert caught.value.detail.classification == "terminal_contract"
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == before

    unknown_case, original_stop = _terminal_case(tmp_path / "unknown-category", failed=True)
    unknown_case["result"] = _terminal_harness().runtime_failure(
        unknown_case["workflow"], 5
    )
    unknown_stop = replace(original_stop, failure_category="unknown")
    unknown_case["state_path"].write_bytes(
        unknown_case["state_path"].read_bytes().replace(b'"api_error"', b'"unknown"')
    )
    _rewrite_event(unknown_case["events_path"], 6, failure_category="unknown")
    unknown_bytes = (
        unknown_case["state_path"].read_bytes(),
        unknown_case["events_path"].read_bytes(),
    )
    phase147_calls: list[object] = []
    _classification(
        lambda: _stop_call(
            unknown_case,
            phase185_function=lambda *args: unknown_stop,
            phase147_function=lambda *args: phase147_calls.append(args),
        ),
        "phase185_contract",
    )
    assert phase147_calls == []
    assert (
        unknown_case["state_path"].read_bytes(),
        unknown_case["events_path"].read_bytes(),
    ) == unknown_bytes


def test_15_phase147_and_phase139_safe_errors_restore_post_phase185_bytes(tmp_path: Path) -> None:
    for error_type in (Phase147Error, Phase139Error):
        for mode in ("state", "events", "both"):
            case, start = _prepared_case(tmp_path / f"{error_type.__name__}-{mode}")
            committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
            safe = error_type("safe")

            def dependency(*args: object, mode=mode, safe=safe) -> object:
                if mode in ("state", "both"):
                    args[3].write_bytes(committed[0] + b"mutated")
                if mode in ("events", "both"):
                    args[4].write_bytes(committed[1] + b"mutated")
                raise safe

            with pytest.raises(type(safe)) as caught:
                _call(case, phase185_function=lambda *args: start, phase147_function=dependency)
            assert caught.value is safe
            assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_16_unexpected_phase147_error_is_sanitized_and_compensated(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "unexpected147")
    committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())

    def dependency(*args: object) -> object:
        args[3].write_bytes(b"bad-state")
        args[4].write_bytes(b"bad-events")
        raise RuntimeError("secret execution payload")

    _classification(lambda: _call(case, phase185_function=lambda *args: start, phase147_function=dependency), "dependency_error")
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_17_malformed_phase147_result_is_contract_error_and_compensated(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "malformed147")
    committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())

    def dependency(*args: object) -> object:
        args[3].write_bytes(b"bad-state")
        return object()

    _classification(lambda: _call(case, phase185_function=lambda *args: start, phase147_function=dependency), "phase147_contract")
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_18_valid_phase147_result_mutation_is_committed_mutation_and_compensated(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "committed-mutation")
    committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())

    def dependency(*args: object) -> object:
        args[3].write_bytes(b"bad-state")
        return RunningStatePersistenceResult(1)

    _classification(lambda: _call(case, phase185_function=lambda *args: start, phase147_function=dependency), "committed_mutation")
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_19_rollback_failure_attempts_both_targets_once_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case, start = _prepared_case(tmp_path / "rollback")
    committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
    original_write = Path.write_bytes
    attempts: list[Path] = []

    def fail_restore(path: Path, data: bytes) -> int:
        attempts.append(path)
        if path == case["events_path"]:
            raise OSError("restore blocked")
        return original_write(path, data)

    def dependency(*args: object) -> object:
        original_write(case["state_path"], b"bad-state")
        original_write(case["events_path"], b"bad-events")
        raise RuntimeError("unexpected phase147")

    monkeypatch.setattr(Path, "write_bytes", fail_restore)
    _classification(lambda: _call(case, phase185_function=lambda *args: start, phase147_function=dependency), "rollback_failure")
    assert attempts.count(case["state_path"]) == 1
    assert attempts.count(case["events_path"]) == 1
    assert case["state_path"].read_bytes() == committed[0]
    assert case["events_path"].read_bytes() == b"bad-events"


def test_20_success_returns_exact_persistence_result_and_has_no_readvance_or_execution(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "no-readvance")
    phase185_calls: list[object] = []
    phase147_calls: list[object] = []

    def p185(*args: object) -> object:
        phase185_calls.append(args)
        return start

    def p147(*args: object) -> object:
        phase147_calls.append(args)
        return _write_expected_running(start, args[3])

    out = _call(case, phase185_function=p185, phase147_function=p147)
    assert type(out) is RunningStatePersistenceResult
    assert len(phase185_calls) == 1 and len(phase147_calls) == 1
    assert phase147_calls[0][0] is start
    assert load_workflow_execution_state(case["state_path"]) == start.running_state
    assert case["events_path"].read_bytes() == case["phase185_event_bytes"]
    assert not any("step-9" in line for line in case["events_path"].read_text().splitlines())
