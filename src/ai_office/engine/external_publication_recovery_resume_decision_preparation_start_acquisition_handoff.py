# ruff: noqa: E501

"""Strict Phase 304 handoff from Phase 303 authorization to Phase 290.

Phase 304 consumes exactly one durable Phase 303 start authorization, derives
its bound Phase 289 intent path and the Phase 290 target keyed by the exact
Phase 303 authorization digest, and delegates exclusive acquisition to Phase
290 exactly once.  It returns that acquisition result unchanged and stops.

This boundary does not persist a Phase 304 artifact, inspect a Phase 290 start
marker before acquisition, execute publication, reconcile publication, or call
any older recovery handoff.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication_operation_intent import (
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    external_publication_operation_intent_digest,
    load_external_publication_operation_intent,
)
from .external_publication_operation_start import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    acquire_external_publication_operation_start,
    external_publication_operation_start_digest,
)
from .external_publication_recovery_resume_decision_preparation_start_authorization import (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    external_publication_recovery_resume_decision_preparation_start_authorization_digest,
    load_external_publication_recovery_resume_decision_preparation_start_authorization,
)

_HANDOFF_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation start acquisition "
    "handoff is blocked"
)
_AUTHORIZATION_SCHEMA_VERSION = (
    "external-publication-recovery-resume-decision-preparation-start-authorization.v1"
)
_INTENT_SCHEMA_VERSION = "external-publication-operation-intent.v1"
_START_SCHEMA_VERSION = "external-publication-operation-start.v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_AUTHORIZATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-start-authorization-"
)
_INTENT_FILENAME_PREFIX = "external-publication-operation-intent-"
_START_FILENAME_PREFIX = "external-publication-recovery-resume-start-"
_FILENAME_SUFFIX = ".json"
_OPERATION = "resume"
_START_STATE = "started"
_ACQUISITION_STATUSES = frozenset({"acquired", "already_acquired"})

Classification = Literal[
    "configuration",
    "path_type",
    "authorization_contract",
    "authorization_path",
    "authorization_digest",
    "intent_contract",
    "intent_lineage",
    "intent_digest",
    "start_contract",
    "start_digest",
    "dependency_error",
    "result_contract",
]

AuthorizationLoader = Callable[[Path], object]
AuthorizationDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization], object
]
IntentLoader = Callable[[Path], object]
IntentDigestFunction = Callable[[ExternalPublicationOperationIntent], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]
Phase290Function = Callable[..., object]

_KNOWN_AUTHORIZATION_ERRORS = (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
)
_KNOWN_INTENT_ERRORS = (ExternalPublicationOperationIntentError,)
_KNOWN_START_ERRORS = (ExternalPublicationOperationStartError,)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffFailureDetail:
    """Detail-safe classification for one Phase 304 failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError(
    ValueError
):
    """Raised when the Phase 304 acquisition handoff cannot proceed safely."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_HANDOFF_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffCompatibilityError(
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError
):
    """Raised when an input, dependency, lineage, or result is incompatible."""


def run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff(
    *,
    start_authorization_path: Path,
    authorization_loader: AuthorizationLoader = load_external_publication_recovery_resume_decision_preparation_start_authorization,
    authorization_digest_function: AuthorizationDigestFunction = external_publication_recovery_resume_decision_preparation_start_authorization_digest,
    intent_loader: IntentLoader = load_external_publication_operation_intent,
    intent_digest_function: IntentDigestFunction = external_publication_operation_intent_digest,
    start_digest_function: StartDigestFunction = external_publication_operation_start_digest,
    phase290_function: Phase290Function = acquire_external_publication_operation_start,
) -> ExternalPublicationOperationStartAcquisition:
    """Hand one exact Phase 303 authorization to Phase 290 once and stop.

    The caller supplies only the exact Phase 303 authorization path.  The
    authorization is strict-loaded once, independently reconstructed, and
    checked against its canonical filename before its digest is computed once
    from the exact loader-returned object.  The bound Phase 289 intent path is
    then derived and loaded once; its digest is computed once from the exact
    loader-returned object and all approval, plan, operation, and digest
    lineage is checked before any start work.

    The expected Phase 290 start is constructed in memory and digested once
    from that exact constructed object.  The canonical start target is derived
    from the Phase 303 authorization digest.  Phase 290 is called exactly once
    with only ``intent_path`` and ``start_path``.  Its exact acquisition result
    is locally revalidated and returned by identity for both stop statuses.
    """
    _preflight(
        start_authorization_path=start_authorization_path,
        authorization_loader=authorization_loader,
        authorization_digest_function=authorization_digest_function,
        intent_loader=intent_loader,
        intent_digest_function=intent_digest_function,
        start_digest_function=start_digest_function,
        phase290_function=phase290_function,
    )

    authorization = _strict_load_authorization(
        authorization_loader, start_authorization_path
    )
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
        authorization,
        "authorization_contract",
    )
    _validate_authorization_path(start_authorization_path, authorization)
    authorization_digest = _authorization_digest_once(
        authorization_digest_function, authorization
    )

    intent_path = _derive_intent_path(start_authorization_path, authorization)
    intent = _strict_load_intent(intent_loader, intent_path)
    _reconstruct_model(ExternalPublicationOperationIntent, intent, "intent_contract")
    if intent.operation != _OPERATION:  # type: ignore[union-attr]
        _raise_handoff("intent_contract")
    intent_digest = _intent_digest_once(intent_digest_function, intent)
    _validate_intent_lineage(authorization, intent, intent_digest)

    expected_start = _construct_expected_start(intent, intent_digest)
    _reconstruct_model(
        ExternalPublicationOperationStart, expected_start, "start_contract"
    )
    start_digest = _start_digest_once(start_digest_function, expected_start)
    if start_digest != authorization.expected_operation_start_sha256:  # type: ignore[union-attr]
        _raise_handoff("start_digest")

    start_path = _derive_start_path(start_authorization_path, authorization_digest)
    result = _call_phase290(
        phase290_function=phase290_function,
        intent_path=intent_path,
        start_path=start_path,
    )
    _validate_result(result, authorization, expected_start)
    return result  # type: ignore[return-value]


def _preflight(
    *,
    start_authorization_path: object,
    authorization_loader: object,
    authorization_digest_function: object,
    intent_loader: object,
    intent_digest_function: object,
    start_digest_function: object,
    phase290_function: object,
) -> None:
    """Reject invalid public inputs before any loader, digest, or call."""
    if type(start_authorization_path) is not _PATH_TYPE:
        _raise_handoff("path_type")
    if not all(
        callable(dependency)
        for dependency in (
            authorization_loader,
            authorization_digest_function,
            intent_loader,
            intent_digest_function,
            start_digest_function,
            phase290_function,
        )
    ):
        _raise_handoff("configuration")


def _strict_load_authorization(loader: AuthorizationLoader, path: Path) -> object:
    try:
        authorization = loader(path)
    except _KNOWN_AUTHORIZATION_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if (
        type(authorization)
        is not ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization
    ):
        _raise_handoff("authorization_contract")
    return authorization


def _validate_authorization_path(path: Path, authorization: object) -> None:
    expected = path.parent / (
        f"{_AUTHORIZATION_FILENAME_PREFIX}"
        f"{authorization.decision_preparation_intent_binding_sha256}"
        f"{_FILENAME_SUFFIX}"
    )  # type: ignore[union-attr]
    if path != expected:
        _raise_handoff("authorization_path")


def _authorization_digest_once(
    digest_function: AuthorizationDigestFunction, authorization: object
) -> str:
    try:
        digest = digest_function(authorization)  # type: ignore[arg-type]
    except _KNOWN_AUTHORIZATION_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if not _is_sha256(digest):
        _raise_handoff("authorization_digest")
    return digest


def _derive_intent_path(path: Path, authorization: object) -> Path:
    return path.parent / (
        f"{_INTENT_FILENAME_PREFIX}"
        f"{authorization.operation_intent_sha256}"  # type: ignore[union-attr]
        f"{_FILENAME_SUFFIX}"
    )


def _strict_load_intent(loader: IntentLoader, path: Path) -> object:
    try:
        intent = loader(path)
    except _KNOWN_INTENT_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_handoff("intent_contract")
    return intent


def _intent_digest_once(digest_function: IntentDigestFunction, intent: object) -> str:
    try:
        digest = digest_function(intent)  # type: ignore[arg-type]
    except _KNOWN_INTENT_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if not _is_sha256(digest):
        _raise_handoff("intent_digest")
    return digest


def _validate_intent_lineage(
    authorization: object, intent: object, intent_digest: str
) -> None:
    if (
        intent_digest != authorization.operation_intent_sha256  # type: ignore[union-attr]
        or intent.publication_approval_sha256  # type: ignore[union-attr]
        != authorization.publication_approval_sha256  # type: ignore[union-attr]
        or intent.publication_plan_sha256  # type: ignore[union-attr]
        != authorization.publication_plan_sha256  # type: ignore[union-attr]
        or intent.operation != authorization.operation  # type: ignore[union-attr]
        or intent.operation != _OPERATION  # type: ignore[union-attr]
    ):
        _raise_handoff("intent_lineage")


def _construct_expected_start(
    intent: object, intent_digest: str
) -> ExternalPublicationOperationStart:
    try:
        return ExternalPublicationOperationStart(
            schema_version=_START_SCHEMA_VERSION,  # type: ignore[arg-type]
            operation_intent_sha256=intent_digest,
            publication_approval_sha256=intent.publication_approval_sha256,  # type: ignore[union-attr]
            publication_plan_sha256=intent.publication_plan_sha256,  # type: ignore[union-attr]
            operation=_OPERATION,  # type: ignore[arg-type]
            state=_START_STATE,  # type: ignore[arg-type]
        )
    except _KNOWN_START_ERRORS:
        raise
    except Exception:
        _raise_handoff("start_contract")


def _start_digest_once(digest_function: StartDigestFunction, start: object) -> str:
    try:
        digest = digest_function(start)  # type: ignore[arg-type]
    except _KNOWN_START_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if not _is_sha256(digest):
        _raise_handoff("start_digest")
    return digest


def _derive_start_path(path: Path, authorization_digest: str) -> Path:
    return path.parent / (
        f"{_START_FILENAME_PREFIX}{authorization_digest}{_FILENAME_SUFFIX}"
    )


def _call_phase290(
    *, phase290_function: Phase290Function, intent_path: Path, start_path: Path
) -> object:
    try:
        return phase290_function(intent_path=intent_path, start_path=start_path)
    except _KNOWN_INTENT_ERRORS + _KNOWN_START_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")


def _validate_result(
    result: object,
    authorization: object,
    expected_start: ExternalPublicationOperationStart,
) -> None:
    if type(result) is not ExternalPublicationOperationStartAcquisition:
        _raise_handoff("result_contract")
    try:
        values = {
            field.name: getattr(result, field.name)
            for field in fields(ExternalPublicationOperationStartAcquisition)
        }
        ExternalPublicationOperationStartAcquisition(**values)
    except Exception:
        _raise_handoff("result_contract")

    if result.status not in _ACQUISITION_STATUSES:  # type: ignore[union-attr]
        _raise_handoff("result_contract")
    start = result.start  # type: ignore[union-attr]
    if (
        start != expected_start
        or start.operation_intent_sha256  # type: ignore[union-attr]
        != authorization.operation_intent_sha256  # type: ignore[union-attr]
        or start.publication_approval_sha256  # type: ignore[union-attr]
        != authorization.publication_approval_sha256  # type: ignore[union-attr]
        or start.publication_plan_sha256  # type: ignore[union-attr]
        != authorization.publication_plan_sha256  # type: ignore[union-attr]
        or start.operation != _OPERATION  # type: ignore[union-attr]
        or start.state != _START_STATE  # type: ignore[union-attr]
    ):
        _raise_handoff("result_contract")


def _reconstruct_model(
    model_type: type[object], instance: object, classification: Classification
) -> None:
    if type(instance) is not model_type:
        _raise_handoff(classification)
    try:
        values = {
            field.name: getattr(instance, field.name) for field in fields(model_type)
        }
        model_type(**values)  # type: ignore[operator]
    except Exception:
        _raise_handoff(classification)


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _raise_handoff(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffCompatibilityError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffCompatibilityError",
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError",
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffFailureDetail",
    "run_external_publication_recovery_resume_decision_preparation_start_acquisition_handoff",
]
