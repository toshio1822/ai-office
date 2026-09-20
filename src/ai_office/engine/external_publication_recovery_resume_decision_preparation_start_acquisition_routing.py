# ruff: noqa: E501

"""Same-invocation Phase 305 routing for one Phase 304 acquisition result.

Phase 305 consumes only the exact Phase 303 authorization path and delegates
one acquisition attempt to Phase 304.  It exposes the exact acquisition object
as an in-memory route and then stops.  It owns no durable artifact and never
reconstructs authority from a durable start marker.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication_operation_intent import (
    ExternalPublicationOperationIntentError,
)
from .external_publication_operation_start import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
)
from .external_publication_recovery_resume_decision_preparation_start_acquisition_handoff import (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError,
    run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff,
)
from .external_publication_recovery_resume_decision_preparation_start_authorization import (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
)

_ROUTE_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation start acquisition "
    "routing is blocked"
)
_ROUTE_SCHEMA_VERSION = "external-publication-recovery-resume-decision-preparation-start-acquisition-route.v1"
_PATH_TYPE = type(Path())
_ACQUISITION_STATUSES = frozenset({"acquired", "already_acquired"})

Classification = Literal[
    "configuration",
    "path_type",
    "acquisition_contract",
    "route_contract",
    "dependency_error",
]

Phase304Function = Callable[..., object]
_KNOWN_PHASE304_ERRORS = (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationStartError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingFailureDetail:
    """Detail-safe classification for one Phase 305 routing failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError(
    ValueError
):
    """Raised when the Phase 305 routing boundary cannot proceed safely."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_ROUTE_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingCompatibilityError(
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError
):
    """Raised when a public input, acquisition, or route is incompatible."""


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute:
    """One non-persisted route from the exact Phase 304 result."""

    schema_version: Literal[
        "external-publication-recovery-resume-decision-preparation-start-acquisition-route.v1"
    ]
    acquisition: ExternalPublicationOperationStartAcquisition
    route: Literal["fresh_start_acquired", "recovery_required"]

    def __post_init__(self) -> None:
        _validate_route(self)


def route_external_publication_recovery_resume_decision_preparation_start_acquisition(
    *,
    start_authorization_path: Path,
    phase304_function: Phase304Function = run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff,
) -> ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute:
    """Route one exact Phase 304 acquisition result and stop.

    The caller supplies only the exact Phase 303 authorization path.  Phase
    304 is called once with that exact path object and no dependency overrides.
    The returned route is in-memory only and retains the exact acquisition
    object returned by Phase 304.
    """
    _preflight(
        start_authorization_path=start_authorization_path,
        phase304_function=phase304_function,
    )

    try:
        acquisition = phase304_function(
            start_authorization_path=start_authorization_path,
        )
    except _KNOWN_PHASE304_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")

    _validate_acquisition(acquisition)
    route = (
        "fresh_start_acquired"
        if acquisition.status == "acquired"  # type: ignore[union-attr]
        else "recovery_required"
    )
    try:
        return (
            ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute(
                schema_version=_ROUTE_SCHEMA_VERSION,
                acquisition=acquisition,  # type: ignore[arg-type]
                route=route,
            )
        )
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError
    ):
        raise
    except Exception:
        _raise_routing("route_contract")


def _preflight(*, start_authorization_path: object, phase304_function: object) -> None:
    """Reject invalid public inputs before calling Phase 304."""
    if type(start_authorization_path) is not _PATH_TYPE:
        _raise_routing("path_type")
    if not callable(phase304_function):
        _raise_routing("configuration")


def _validate_acquisition(acquisition: object) -> None:
    """Locally revalidate both exact public models without reading storage."""
    if type(acquisition) is not ExternalPublicationOperationStartAcquisition:
        _raise_routing("acquisition_contract")
    try:
        start = acquisition.start  # type: ignore[union-attr]
        start_values = {
            field.name: getattr(start, field.name)
            for field in fields(ExternalPublicationOperationStart)
        }
        ExternalPublicationOperationStart(**start_values)

        acquisition_values = {
            field.name: getattr(acquisition, field.name)
            for field in fields(ExternalPublicationOperationStartAcquisition)
        }
        ExternalPublicationOperationStartAcquisition(**acquisition_values)
    except Exception:
        _raise_routing("acquisition_contract")

    if type(acquisition.status) is not str:  # type: ignore[union-attr]
        _raise_routing("acquisition_contract")
    if acquisition.status not in _ACQUISITION_STATUSES:  # type: ignore[union-attr]
        _raise_routing("acquisition_contract")
    if type(acquisition.start) is not ExternalPublicationOperationStart:  # type: ignore[union-attr]
        _raise_routing("acquisition_contract")


def _validate_route(route: object) -> None:
    """Validate the frozen route model independently of its constructor."""
    if (
        type(route)
        is not ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute
    ):
        _raise_routing("route_contract")
    try:
        schema_version = route.schema_version  # type: ignore[union-attr]
        acquisition = route.acquisition  # type: ignore[union-attr]
        route_value = route.route  # type: ignore[union-attr]
    except Exception:
        _raise_routing("route_contract")

    if type(schema_version) is not str or schema_version != _ROUTE_SCHEMA_VERSION:
        _raise_routing("route_contract")
    if type(route_value) is not str or route_value not in {
        "fresh_start_acquired",
        "recovery_required",
    }:
        _raise_routing("route_contract")

    try:
        _validate_acquisition(acquisition)
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError
    ):
        _raise_routing("route_contract")

    if (
        acquisition.status == "acquired"  # type: ignore[union-attr]
        and route_value != "fresh_start_acquired"
    ) or (
        acquisition.status == "already_acquired"  # type: ignore[union-attr]
        and route_value != "recovery_required"
    ):
        _raise_routing("route_contract")


def _raise_routing(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingCompatibilityError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute",
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingCompatibilityError",
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError",
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingFailureDetail",
    "route_external_publication_recovery_resume_decision_preparation_start_acquisition",
]
