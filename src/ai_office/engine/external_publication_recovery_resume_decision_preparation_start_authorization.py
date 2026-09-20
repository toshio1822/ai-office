# ruff: noqa: E501

"""Durable Phase 303 authorization for one future recovery-resume start.

Phase 303 consumes exactly one durable Phase 302 preparation-intent binding,
loads the exact bound Phase 289 resume intent, constructs the expected Phase
290 resume-start identity in memory, and durably records authorization for one
future Phase 304 handoff.  It does not load, persist, or acquire a Phase 290
start marker, execute publication, reconcile publication, or contact a
provider, transport, network, or credential boundary.
"""

from __future__ import annotations

import errno
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from hashlib import sha256
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
    ExternalPublicationOperationStartError,
    external_publication_operation_start_digest,
)
from .external_publication_recovery_resume_decision_preparation_intent_binding import (
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError,
    external_publication_recovery_resume_decision_preparation_intent_binding_digest,
    load_external_publication_recovery_resume_decision_preparation_intent_binding,
)

_AUTHORIZATION_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation start authorization "
    "is invalid"
)
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation start authorization "
    "persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation start authorization "
    "could not be loaded"
)
_AUTHORIZATION_SCHEMA_VERSION = (
    "external-publication-recovery-resume-decision-preparation-start-authorization.v1"
)
_BINDING_SCHEMA_VERSION = (
    "external-publication-recovery-resume-decision-preparation-intent-binding.v1"
)
_INTENT_SCHEMA_VERSION = "external-publication-operation-intent.v1"
_START_SCHEMA_VERSION = "external-publication-operation-start.v1"
_AUTHORIZATION_KEYS = frozenset(
    {
        "decision_preparation_intent_binding_sha256",
        "decision_preparation_sha256",
        "expected_operation_start_sha256",
        "operation",
        "operation_intent_sha256",
        "previous_recovery_kind",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_kind",
        "recovery_resume_decision_sha256",
        "result_kind",
        "result_sha256",
        "schema_version",
        "source_operation",
        "state",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_AUTHORIZATION_BYTES = 4096
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_RESULT_KINDS = frozenset({"reconciliation", "none"})
_OPERATION = "resume"
_STATE = "authorized"
_NONE_RESULT_KIND = "none"
_RECONCILIATION_RESULT_KIND = "reconciliation"
_ALREADY_ACQUIRED_RECOVERY_KIND = "already_acquired"
_MISMATCH_RECOVERY_KIND = "reconciliation_mismatch"
_AUTHORIZATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-start-authorization-"
)
_INTENT_FILENAME_PREFIX = "external-publication-operation-intent-"
_FILENAME_SUFFIX = ".json"

Classification = Literal[
    "configuration",
    "path_type",
    "binding_contract",
    "binding_digest",
    "intent_contract",
    "intent_digest",
    "intent_lineage",
    "start_contract",
    "start_digest",
    "serialization",
    "encoding",
    "parent",
    "target",
    "create",
    "ambiguous",
    "conflict",
    "load",
    "parse",
    "size",
    "keys",
    "noncanonical",
    "dependency_error",
]

BindingLoader = Callable[[Path], object]
BindingDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding], object
]
IntentLoader = Callable[[Path], object]
IntentDigestFunction = Callable[[ExternalPublicationOperationIntent], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]

_KNOWN_BINDING_ERRORS = (
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError,
)
_KNOWN_INTENT_ERRORS = (ExternalPublicationOperationIntentError,)
_KNOWN_START_ERRORS = (ExternalPublicationOperationStartError,)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationFailureDetail:
    """Detail-safe classification for one Phase 303 failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError(
    ValueError
):
    """Raised when a Phase 303 authorization request or record is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_AUTHORIZATION_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError(
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError
):
    """Raised when a dependency, lineage, or authorization input is incompatible."""


class ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationPersistenceError(
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError
):
    """Raised when Phase 303 authorization durability cannot be proven."""

    def __init__(self, classification: Classification = "target") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationConflictError(
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationPersistenceError
):
    """Raised when an occupied authorization target is not the exact record."""


class ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationLoadError(
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError
):
    """Raised when an authorization sidecar is not an exact canonical record."""

    def __init__(self, classification: Classification = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationFailureDetail(
            classification
        )


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization:
    """Immutable, secret-free authorization for one future Phase 304 handoff.

    ``authorized`` means only that the exact Phase 302 binding and its exact
    Phase 289 resume intent authorize one future attempt to acquire the exact
    expected Phase 290 resume-start identity.  It does not mean that a start
    marker exists or was acquired, and it does not authorize publication.
    """

    schema_version: Literal[
        "external-publication-recovery-resume-decision-preparation-start-authorization.v1"
    ]
    decision_preparation_intent_binding_sha256: str
    decision_preparation_sha256: str
    recovery_resume_decision_sha256: str
    operation_intent_sha256: str
    expected_operation_start_sha256: str
    publication_approval_sha256: str
    publication_plan_sha256: str
    source_operation: Literal["fresh", "resume"]
    previous_recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    result_kind: Literal["reconciliation", "none"]
    result_sha256: str | None
    operation: Literal["resume"]
    state: Literal["authorized"]

    def __post_init__(self) -> None:
        _validate_authorization(self)


def serialize_external_publication_recovery_resume_decision_preparation_start_authorization_canonical(  # noqa: E501
    authorization: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
) -> str:
    """Serialize one exact Phase 303 authorization as canonical JSON."""
    _validate_authorization(authorization)
    try:
        return json.dumps(
            {
                "decision_preparation_intent_binding_sha256": (
                    authorization.decision_preparation_intent_binding_sha256
                ),
                "decision_preparation_sha256": authorization.decision_preparation_sha256,
                "expected_operation_start_sha256": (
                    authorization.expected_operation_start_sha256
                ),
                "operation": authorization.operation,
                "operation_intent_sha256": authorization.operation_intent_sha256,
                "previous_recovery_kind": authorization.previous_recovery_kind,
                "publication_approval_sha256": authorization.publication_approval_sha256,
                "publication_plan_sha256": authorization.publication_plan_sha256,
                "recovery_kind": authorization.recovery_kind,
                "recovery_resume_decision_sha256": (
                    authorization.recovery_resume_decision_sha256
                ),
                "result_kind": authorization.result_kind,
                "result_sha256": authorization.result_sha256,
                "schema_version": authorization.schema_version,
                "source_operation": authorization.source_operation,
                "state": authorization.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("serialization")


def external_publication_recovery_resume_decision_preparation_start_authorization_canonical_bytes(  # noqa: E501
    authorization: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
) -> bytes:
    """Return exact canonical Phase 303 JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_recovery_resume_decision_preparation_start_authorization_canonical(  # noqa: E501
            authorization
        ).encode("utf-8")
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError:
        raise
    except UnicodeError:
        _raise_authorization("encoding")
    except Exception:
        _raise_authorization("encoding")


def external_publication_recovery_resume_decision_preparation_start_authorization_digest(
    authorization: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
) -> str:
    """Return SHA-256 over exact canonical Phase 303 authorization bytes."""
    return sha256(
        external_publication_recovery_resume_decision_preparation_start_authorization_canonical_bytes(
            authorization
        )
    ).hexdigest()


def load_external_publication_recovery_resume_decision_preparation_start_authorization(
    path: Path,
) -> ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization:
    """Strict-load one immutable canonical Phase 303 authorization."""
    _validate_load_path(path)
    try:
        with path.open("rb") as handle:
            contents = handle.read(_MAX_AUTHORIZATION_BYTES + 1)
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")
    if len(contents) > _MAX_AUTHORIZATION_BYTES:
        _raise_load("size")

    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKeyError,
        _NonStandardJSONConstantError,
    ):
        _raise_load("parse")
    except Exception:
        _raise_load("parse")

    authorization = _parse_authorization(value)
    try:
        canonical = external_publication_recovery_resume_decision_preparation_start_authorization_canonical_bytes(
            authorization
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError:
        _raise_load("load")
    except Exception:
        _raise_load("load")
    if canonical != contents:
        _raise_load("noncanonical")
    return authorization


def persist_external_publication_recovery_resume_decision_preparation_start_authorization(
    path: Path,
    authorization: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
) -> None:
    """Durably append-only persist one exact Phase 303 authorization.

    Exclusive creation, complete write, flush, file fsync, safe close, and
    parent-directory fsync are required.  Exact existing bytes are idempotent;
    all other occupied bytes are a fixed conflict.  Any ambiguous post-create
    failure retains the artifact and is never retried, repaired, or removed.
    """
    _validate_persistence_target(path)
    _validate_authorization(authorization)
    try:
        contents = external_publication_recovery_resume_decision_preparation_start_authorization_canonical_bytes(
            authorization
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError:
        raise
    except Exception:
        _raise_persistence("serialization")
    _write_authorization(path, contents)


def authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
    *,
    decision_preparation_intent_binding_path: Path,
    binding_loader: BindingLoader = (
        load_external_publication_recovery_resume_decision_preparation_intent_binding
    ),
    binding_digest_function: BindingDigestFunction = (
        external_publication_recovery_resume_decision_preparation_intent_binding_digest
    ),
    intent_loader: IntentLoader = load_external_publication_operation_intent,
    intent_digest_function: IntentDigestFunction = (
        external_publication_operation_intent_digest
    ),
    start_digest_function: StartDigestFunction = (
        external_publication_operation_start_digest
    ),
) -> ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization:
    """Create or resolve the exact Phase 303 start authorization.

    The caller supplies only the exact Phase 302 binding path.  The binding is
    strict-loaded exactly once and locally reconstructed before its digest is
    computed exactly once from the exact loader-returned object.  The bound
    Phase 289 intent path is derived from the binding and loaded exactly once;
    its exact loader-returned object is locally reconstructed before its digest
    is computed exactly once.  Approval, plan, operation, and intent digest
    lineage are checked before constructing the expected Phase 290 start.

    The expected start is constructed in memory only and its digest is computed
    exactly once from that exact constructed object.  No Phase 290 start loader,
    persistence, or acquisition is used.  The new authorization path is derived
    from the Phase 302 binding digest and the authorization binds every Phase
    302 provenance field.  An existing exact authorization returns the exact
    loader-returned object; otherwise one append-only durable artifact is
    created.  No orchestration, provider, transport, network, credential,
    execution, reconciliation, retry, fallback, or continuation occurs.
    """
    _preflight(
        decision_preparation_intent_binding_path=decision_preparation_intent_binding_path,
        binding_loader=binding_loader,
        binding_digest_function=binding_digest_function,
        intent_loader=intent_loader,
        intent_digest_function=intent_digest_function,
        start_digest_function=start_digest_function,
    )

    binding = _strict_load_binding(
        binding_loader, decision_preparation_intent_binding_path
    )
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
        binding,
        "binding_contract",
    )
    binding_digest = _binding_digest_once(binding_digest_function, binding)

    authorization_path = _derive_authorization_path(
        decision_preparation_intent_binding_path, binding_digest
    )
    _validate_derived_target(authorization_path)

    intent_path = _derive_intent_path(decision_preparation_intent_binding_path, binding)
    intent = _strict_load_intent(intent_loader, intent_path)
    _reconstruct_model(ExternalPublicationOperationIntent, intent, "intent_contract")
    if intent.operation != _OPERATION:  # type: ignore[union-attr]
        _raise_authorization("intent_contract")
    intent_digest = _intent_digest_once(intent_digest_function, intent)
    _validate_intent_lineage(binding, intent, intent_digest)

    expected_start = _construct_expected_start(intent, intent_digest)
    _reconstruct_model(
        ExternalPublicationOperationStart, expected_start, "start_contract"
    )
    start_digest = _start_digest_once(start_digest_function, expected_start)

    constructed = _construct_authorization(
        binding=binding,
        intent=intent,
        binding_digest=binding_digest,
        intent_digest=intent_digest,
        start_digest=start_digest,
    )

    if _authorization_target_present(authorization_path):
        return _resolve_existing_authorization(authorization_path, constructed)

    try:
        persist_external_publication_recovery_resume_decision_preparation_start_authorization(
            authorization_path, constructed
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError:
        raise
    except Exception:
        _raise_persistence("dependency_error")
    return constructed


def _preflight(
    *,
    decision_preparation_intent_binding_path: object,
    binding_loader: object,
    binding_digest_function: object,
    intent_loader: object,
    intent_digest_function: object,
    start_digest_function: object,
) -> None:
    if type(decision_preparation_intent_binding_path) is not _PATH_TYPE:
        _raise_authorization("path_type")
    for dependency in (
        binding_loader,
        binding_digest_function,
        intent_loader,
        intent_digest_function,
        start_digest_function,
    ):
        if not callable(dependency):
            _raise_authorization("configuration")


def _strict_load_binding(loader: BindingLoader, path: Path) -> object:
    try:
        binding = loader(path)
    except _KNOWN_BINDING_ERRORS:
        raise
    except Exception:
        _raise_authorization("dependency_error")
    if (
        type(binding)
        is not ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding
    ):
        _raise_authorization("binding_contract")
    return binding


def _binding_digest_once(
    digest_function: BindingDigestFunction, binding: object
) -> str:
    try:
        digest = digest_function(binding)  # type: ignore[arg-type]
    except _KNOWN_BINDING_ERRORS:
        raise
    except Exception:
        _raise_authorization("dependency_error")
    if not _is_sha256(digest):
        _raise_authorization("binding_digest")
    return digest


def _strict_load_intent(loader: IntentLoader, path: Path) -> object:
    try:
        intent = loader(path)
    except _KNOWN_INTENT_ERRORS:
        raise
    except Exception:
        _raise_authorization("dependency_error")
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_authorization("intent_contract")
    return intent


def _intent_digest_once(digest_function: IntentDigestFunction, intent: object) -> str:
    try:
        digest = digest_function(intent)  # type: ignore[arg-type]
    except _KNOWN_INTENT_ERRORS:
        raise
    except Exception:
        _raise_authorization("dependency_error")
    if not _is_sha256(digest):
        _raise_authorization("intent_digest")
    return digest


def _start_digest_once(digest_function: StartDigestFunction, start: object) -> str:
    try:
        digest = digest_function(start)  # type: ignore[arg-type]
    except _KNOWN_START_ERRORS:
        raise
    except Exception:
        _raise_authorization("dependency_error")
    if not _is_sha256(digest):
        _raise_authorization("start_digest")
    return digest


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
            state="started",
        )
    except ExternalPublicationOperationStartError:
        _raise_authorization("start_contract")
    except Exception:
        _raise_authorization("start_contract")


def _validate_intent_lineage(
    binding: object, intent: object, intent_digest: str
) -> None:
    if (
        intent_digest != binding.operation_intent_sha256  # type: ignore[union-attr]
        or intent.publication_approval_sha256  # type: ignore[union-attr]
        != binding.publication_approval_sha256  # type: ignore[union-attr]
        or intent.publication_plan_sha256  # type: ignore[union-attr]
        != binding.publication_plan_sha256  # type: ignore[union-attr]
        or intent.operation != binding.operation  # type: ignore[union-attr]
        or intent.operation != _OPERATION  # type: ignore[union-attr]
    ):
        _raise_authorization("intent_lineage")


def _construct_authorization(
    *,
    binding: object,
    intent: object,
    binding_digest: str,
    intent_digest: str,
    start_digest: str,
) -> ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization:
    try:
        return ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
            schema_version=_AUTHORIZATION_SCHEMA_VERSION,  # type: ignore[arg-type]
            decision_preparation_intent_binding_sha256=binding_digest,
            decision_preparation_sha256=binding.decision_preparation_sha256,  # type: ignore[union-attr]
            recovery_resume_decision_sha256=binding.recovery_resume_decision_sha256,  # type: ignore[union-attr]
            operation_intent_sha256=intent_digest,
            expected_operation_start_sha256=start_digest,
            publication_approval_sha256=binding.publication_approval_sha256,  # type: ignore[union-attr]
            publication_plan_sha256=binding.publication_plan_sha256,  # type: ignore[union-attr]
            source_operation=binding.source_operation,  # type: ignore[union-attr]
            previous_recovery_kind=binding.previous_recovery_kind,  # type: ignore[union-attr]
            recovery_kind=binding.recovery_kind,  # type: ignore[union-attr]
            result_kind=binding.result_kind,  # type: ignore[union-attr]
            result_sha256=binding.result_sha256,  # type: ignore[union-attr]
            operation=_OPERATION,  # type: ignore[arg-type]
            state=_STATE,  # type: ignore[arg-type]
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("serialization")


def _reconstruct_model(
    model_type: type[object], instance: object, classification: Classification
) -> None:
    if type(instance) is not model_type:
        _raise_authorization(classification)
    try:
        values = {
            field.name: getattr(instance, field.name) for field in fields(model_type)
        }
        model_type(**values)  # type: ignore[operator]
    except Exception:
        _raise_authorization(classification)


def _derive_intent_path(
    binding_path: Path,
    binding: object,
) -> Path:
    return binding_path.parent / (
        f"{_INTENT_FILENAME_PREFIX}{binding.operation_intent_sha256}{_FILENAME_SUFFIX}"
    )  # type: ignore[union-attr]


def _derive_authorization_path(binding_path: Path, binding_digest: str) -> Path:
    return binding_path.parent / (
        f"{_AUTHORIZATION_FILENAME_PREFIX}{binding_digest}{_FILENAME_SUFFIX}"
    )


def _validate_derived_target(path: Path) -> None:
    try:
        if not path.parent.exists() or not path.parent.is_dir():
            _raise_authorization("parent")
        if path.is_symlink() or path.is_dir() or (path.exists() and not path.is_file()):
            _raise_authorization("target")
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("target")


def _authorization_target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_load("target")


def _resolve_existing_authorization(
    path: Path,
    constructed: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
) -> ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization:
    try:
        loaded = load_external_publication_recovery_resume_decision_preparation_start_authorization(
            path
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError:
        raise
    except Exception:
        _raise_load("load")
    if (
        type(loaded)
        is not ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization
    ):
        _raise_load("load")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
        loaded,
        "load",
    )
    if loaded != constructed:
        _raise_conflict()
    return loaded


def _validate_authorization(authorization: object) -> None:
    if (
        type(authorization)
        is not ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization
    ):
        _raise_authorization("configuration")
    try:
        _check_authorization(authorization)
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("configuration")


def _check_authorization(
    authorization: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
) -> None:
    values = {
        field.name: getattr(authorization, field.name)
        for field in fields(
            ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization
        )
    }
    schema_version = values["schema_version"]
    if (
        type(schema_version) is not str
        or schema_version != _AUTHORIZATION_SCHEMA_VERSION
    ):
        _raise_authorization("configuration")

    for field_name in (
        "decision_preparation_intent_binding_sha256",
        "decision_preparation_sha256",
        "recovery_resume_decision_sha256",
        "operation_intent_sha256",
        "expected_operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
    ):
        if not _is_sha256(values[field_name]):
            _raise_authorization("configuration")

    source_operation = values["source_operation"]
    previous_recovery_kind = values["previous_recovery_kind"]
    recovery_kind = values["recovery_kind"]
    result_kind = values["result_kind"]
    result_digest = values["result_sha256"]
    operation = values["operation"]
    state = values["state"]

    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_authorization("configuration")
    if (
        type(previous_recovery_kind) is not str
        or previous_recovery_kind not in _RECOVERY_KINDS
    ):
        _raise_authorization("configuration")
    if (
        previous_recovery_kind == _MISMATCH_RECOVERY_KIND
        and source_operation != _OPERATION
    ):
        _raise_authorization("configuration")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_authorization("configuration")
    if type(result_kind) is not str or result_kind not in _RESULT_KINDS:
        _raise_authorization("configuration")
    if type(operation) is not str or operation != _OPERATION:
        _raise_authorization("configuration")
    if type(state) is not str or state != _STATE:
        _raise_authorization("configuration")

    if result_kind == _NONE_RESULT_KIND:
        if (
            result_digest is not None
            or recovery_kind != _ALREADY_ACQUIRED_RECOVERY_KIND
        ):
            _raise_authorization("configuration")
        return
    if not _is_sha256(result_digest):
        _raise_authorization("configuration")
    if recovery_kind != _MISMATCH_RECOVERY_KIND:
        _raise_authorization("configuration")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _validate_persistence_target(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_persistence("path_type")
    try:
        if not path.parent.exists() or not path.parent.is_dir():  # type: ignore[union-attr]
            _raise_persistence("parent")
        if (
            path.is_symlink()  # type: ignore[union-attr]
            or path.is_dir()  # type: ignore[union-attr]
            or (path.exists() and not path.is_file())  # type: ignore[union-attr]
        ):
            _raise_persistence("target")
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationPersistenceError:
        raise
    except Exception:
        _raise_persistence("target")


def _validate_load_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_load("path_type")
    try:
        if (
            path.is_symlink()  # type: ignore[union-attr]
            or not path.exists()  # type: ignore[union-attr]
            or path.is_dir()  # type: ignore[union-attr]
            or not path.is_file()  # type: ignore[union-attr]
        ):
            _raise_load("target")
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationLoadError
    ):
        raise
    except Exception:
        _raise_load("target")


def _write_authorization(path: Path, contents: bytes) -> None:
    try:
        handle = path.open("xb")
    except FileExistsError:
        _verify_existing_authorization(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _verify_existing_authorization(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_authorization(handle, path.parent, contents)


def _persist_new_authorization(
    handle: object, directory: Path, contents: bytes
) -> None:
    try:
        with _authorization_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short Phase 303 authorization write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_authorization_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _verify_existing_authorization(path: Path, contents: bytes) -> None:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        with path.open("rb") as handle:
            existing = handle.read(_MAX_AUTHORIZATION_BYTES + 1)
        if type(existing) is not bytes:
            _raise_persistence("target")
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationPersistenceError:
        raise
    except Exception:
        _raise_persistence("target")

    if existing != contents:
        _raise_conflict()

    try:
        handle = path.open("rb")
    except Exception:
        _raise_persistence("ambiguous")
    try:
        with _authorization_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")
    try:
        _fsync_authorization_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _authorization_handle_scope(handle: object) -> Iterator[object]:
    enter = getattr(handle, "__enter__", None)
    exit_ = getattr(handle, "__exit__", None)
    if callable(enter) and callable(exit_):
        with handle as active_handle:  # type: ignore[union-attr]
            yield active_handle
        return

    try:
        yield handle
    finally:
        close = getattr(handle, "close", None)
        if not callable(close):
            raise OSError("Phase 303 authorization handle cannot close")
        close()


def _fsync_authorization_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_authorization(
    value: object,
) -> ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization:
    if type(value) is not dict:
        _raise_load("parse")
    if frozenset(value) != _AUTHORIZATION_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
            schema_version=value["schema_version"],
            decision_preparation_intent_binding_sha256=value[
                "decision_preparation_intent_binding_sha256"
            ],
            decision_preparation_sha256=value["decision_preparation_sha256"],
            recovery_resume_decision_sha256=value["recovery_resume_decision_sha256"],
            operation_intent_sha256=value["operation_intent_sha256"],
            expected_operation_start_sha256=value["expected_operation_start_sha256"],
            publication_approval_sha256=value["publication_approval_sha256"],
            publication_plan_sha256=value["publication_plan_sha256"],
            source_operation=value["source_operation"],
            previous_recovery_kind=value["previous_recovery_kind"],
            recovery_kind=value["recovery_kind"],
            result_kind=value["result_kind"],
            result_sha256=value["result_sha256"],
            operation=value["operation"],
            state=value["state"],
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError:
        _raise_load("load")
    except Exception:
        _raise_load("parse")


class _DuplicateKeyError(ValueError):
    """Raised when canonical JSON repeats a key."""


class _NonStandardJSONConstantError(ValueError):
    """Raised when canonical JSON uses NaN or an infinity constant."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    del value
    raise _NonStandardJSONConstantError


def _raise_authorization(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError(
        classification
    ) from None


def _raise_persistence(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationPersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationConflictError(
        "conflict"
    ) from None


def _raise_load(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationLoadError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization",
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError",
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationConflictError",
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError",
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationFailureDetail",
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationLoadError",
    "ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationPersistenceError",
    "authorize_and_persist_external_publication_recovery_resume_decision_preparation_start",
    "external_publication_recovery_resume_decision_preparation_start_authorization_canonical_bytes",
    "external_publication_recovery_resume_decision_preparation_start_authorization_digest",
    "load_external_publication_recovery_resume_decision_preparation_start_authorization",
    "persist_external_publication_recovery_resume_decision_preparation_start_authorization",
    "serialize_external_publication_recovery_resume_decision_preparation_start_authorization_canonical",
]
