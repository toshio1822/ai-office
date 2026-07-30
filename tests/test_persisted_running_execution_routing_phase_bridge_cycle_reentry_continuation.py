"""Focused Phase 84 boundary tests."""

import inspect

import pytest

from ai_office.engine import (
    PersistedRunningExecutionRoutingPhaseBridgeCycleContinuationCompatibilityError,
    PersistedRunningExecutionRoutingPhaseBridgeCycleReentryContinuationCompatibilityError,
    route_persisted_running_execution_routing_phase_bridge_cycle_continuation,
    route_persisted_running_execution_routing_phase_bridge_cycle_reentry_continuation,
)


def test_phase77_dependency_signature_is_canonical() -> None:
    parameters = tuple(
        inspect.signature(
            route_persisted_running_execution_routing_phase_bridge_cycle_reentry_continuation
        ).parameters
    )
    assert parameters[:10] == (
        "result",
        "start",
        "workflow",
        "employee",
        "state_path",
        "events_path",
        "resolved_tools",
        "api_key",
        "approval",
        "transport",
    )
    assert tuple(
        inspect.signature(
            route_persisted_running_execution_routing_phase_bridge_cycle_continuation
        ).parameters
    )[:10] == parameters[:10]


def test_routes_all_ten_objects_once_and_returns_exact_dependency_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_office.engine.persisted_running_execution_routing_phase_bridge_cycle_reentry_continuation as module  # noqa: E501

    supplied = tuple(object() for _ in range(10))
    returned = object()
    calls: list[tuple[object, ...]] = []
    phase77 = (  # noqa: E501
        module.route_persisted_running_execution_routing_phase_bridge_cycle_continuation
    )

    def dependency(*args: object, **kwargs: object) -> object:
        calls.append(args)
        assert kwargs == {"phase70_function": phase77}
        return returned

    monkeypatch.setattr(
        module,
        "route_persisted_running_execution_routing_phase_bridge_cycle_continuation",
        dependency,
    )
    assert module.route_persisted_running_execution_routing_phase_bridge_cycle_reentry_continuation(  # noqa: E501
        *supplied
    ) is returned
    assert calls == [supplied]


def test_phase77_compatibility_error_is_reclassified_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_office.engine.persisted_running_execution_routing_phase_bridge_cycle_reentry_continuation as module  # noqa: E501

    error = PersistedRunningExecutionRoutingPhaseBridgeCycleContinuationCompatibilityError(  # noqa: E501
        "dependency_rollback"
    )
    monkeypatch.setattr(
        module,
        "route_persisted_running_execution_routing_phase_bridge_cycle_continuation",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(
        PersistedRunningExecutionRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        module.route_persisted_running_execution_routing_phase_bridge_cycle_reentry_continuation(
            *(object() for _ in range(10))
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert "dependency_rollback" not in str(caught.value)
