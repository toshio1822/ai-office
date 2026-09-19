"""Durable recovery-bound resume start authorization evidence (Phase 296).

Phase 296 records one immutable, secret-free authorization proving that an
exact Phase 295 recovery resume-intent binding together with its exact bound
Phase 289 ``resume`` operation intent authorize one *future* attempt to
acquire the exact expected Phase 290 resume start identity through the future
canonical start target.

``authorized`` means only that:

    the exact Phase 295 recovery binding and the exact bound Phase 289 resume
    intent authorize one future attempt to acquire the exact expected Phase 290
    resume start identity through the future canonical start target.

It does not mean that a start marker exists, that acquisition succeeded, that
``acquired`` may be reconstructed or persisted as later execution authority,
that provider execution is authorized outside a future same-invocation
handoff, that resume executed, that reconciliation completed, or that ``fresh``
may be replayed.

Phase 296 calls no Phase 290 acquisition, creates no start marker, loads no
start marker, accepts no caller-supplied start path, start object, or start
digest as authority, calls no Phase 288/291/292/293/294/295 orchestration, and
performs no provider, transport, or network work.
"""

from __future__ import annotations

import errno
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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
from .external_publication_recovery_resume_intent_binding import (
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeIntentBindingError,
    external_publication_recovery_resume_intent_binding_digest,
    load_external_publication_recovery_resume_intent_binding,
)

_AUTHORIZATION_ERROR_MESSAGE = (
    "external publication recovery resume start authorization is invalid"
)
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication recovery resume start authorization persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "external publication recovery resume start authorization could not be loaded"
)
_AUTHORIZATION_SCHEMA_VERSION = (
    "external-publication-recovery-resume-start-authorization.v1"
)
_BINDING_SCHEMA_VERSION = "external-publication-recovery-resume-intent-binding.v1"
_INTENT_SCHEMA_VERSION = "external-publication-operation-intent.v1"
_START_SCHEMA_VERSION = "external-publication-operation-start.v1"
_AUTHORIZATION_KEYS = frozenset(
    {
        "expected_operation_start_sha256",
        "operation",
        "operation_intent_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_decision_sha256",
        "recovery_kind",
        "resume_intent_binding_sha256",
        "resume_preparation_sha256",
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
_OPERATIONS = frozenset({"resume"})
_STATES = frozenset({"authorized"})
_INTENT_OPERATIONS = frozenset({"resume"})
_START_STATES = frozenset({"started"})
_BINDING_OPERATIONS = frozenset({"resume"})
_BINDING_STATES = frozenset({"authorized"})
_FUTURE_START_FILENAME_PREFIX = "external-publication-recovery-resume-start-"
_FUTURE_START_FILENAME_SUFFIX = ".json"
_AUTHORIZATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-start-authorization-"
)
_AUTHORIZATION_FILENAME_SUFFIX = ".json"

Classification = Literal[
    "configuration",
    "path_type",
    "path_conflict",
    "authorization_path",
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
    "keys",
    "noncanonical",
    "dependency_error",
]

BindingLoader = Callable[[Path], object]
BindingDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeIntentBinding], object
]
IntentLoader = Callable[[Path], object]
IntentDigestFunction = Callable[[ExternalPublicationOperationIntent], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]

_KNOWN_PREDECESSOR_ERRORS = (
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationStartError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeStartAuthorizationFailureDetail:
    """Detail-safe classification for one resume start authorization failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeStartAuthorizationError(ValueError):
    """Raised when a resume start authorization or request is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_AUTHORIZATION_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeStartAuthorizationFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError(
    ExternalPublicationRecoveryResumeStartAuthorizationError
):
    """Raised when a path, binding, intent, or expected start is incompatible."""


class ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError(
    ExternalPublicationRecoveryResumeStartAuthorizationError
):
    """Raised when resume-start-authorization durability cannot be proven."""

    def __init__(self, classification: Classification = "target") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeStartAuthorizationFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeStartAuthorizationConflictError(
    ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError
):
    """Raised when an occupied authorization target is not the exact record."""


class ExternalPublicationRecoveryResumeStartAuthorizationLoadError(
    ExternalPublicationRecoveryResumeStartAuthorizationError
):
    """Raised when a resume-start-authorization sidecar is not an exact record."""

    def __init__(self, classification: Classification = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeStartAuthorizationFailureDetail(
            classification
        )


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeStartAuthorization:
    """Immutable, secret-free recovery resume start authorization evidence.

    ``state`` ``"authorized"`` records only that this exact Phase 295 binding
    and this exact bound Phase 289 resume intent authorize one future attempt to
    acquire the exact expected Phase 290 resume start identity through the
    future canonical start target.  It records no start marker, no acquisition,
    no reconstructible ``acquired``, no execution, no reconciliation, and no
    replay permission.
    """

    schema_version: Literal[
        "external-publication-recovery-resume-start-authorization.v1"
    ]
    resume_intent_binding_sha256: str
    resume_preparation_sha256: str
    recovery_decision_sha256: str
    operation_intent_sha256: str
    expected_operation_start_sha256: str
    publication_approval_sha256: str
    publication_plan_sha256: str
    source_operation: Literal["fresh", "resume"]
    recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    operation: Literal["resume"]
    state: Literal["authorized"]

    def __post_init__(self) -> None:
        _validate_authorization(self)


def serialize_external_publication_recovery_resume_start_authorization_canonical(
    authorization: ExternalPublicationRecoveryResumeStartAuthorization,
) -> str:
    """Serialize one exact resume start authorization as compact JSON."""
    _validate_authorization(authorization)
    try:
        return json.dumps(
            {
                "expected_operation_start_sha256": (
                    authorization.expected_operation_start_sha256
                ),
                "operation": authorization.operation,
                "operation_intent_sha256": authorization.operation_intent_sha256,
                "publication_approval_sha256": (
                    authorization.publication_approval_sha256
                ),
                "publication_plan_sha256": authorization.publication_plan_sha256,
                "recovery_decision_sha256": authorization.recovery_decision_sha256,
                "recovery_kind": authorization.recovery_kind,
                "resume_intent_binding_sha256": (
                    authorization.resume_intent_binding_sha256
                ),
                "resume_preparation_sha256": authorization.resume_preparation_sha256,
                "schema_version": authorization.schema_version,
                "source_operation": authorization.source_operation,
                "state": authorization.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("serialization")


def external_publication_recovery_resume_start_authorization_canonical_bytes(
    authorization: ExternalPublicationRecoveryResumeStartAuthorization,
) -> bytes:
    """Return exact canonical resume-start-authorization JSON as UTF-8 bytes."""
    try:
        canonical = serialize_external_publication_recovery_resume_start_authorization_canonical(  # noqa: E501
            authorization
        )
        return canonical.encode("utf-8")
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        raise
    except UnicodeError:
        _raise_authorization("encoding")
    except Exception:
        _raise_authorization("encoding")


def external_publication_recovery_resume_start_authorization_digest(
    authorization: ExternalPublicationRecoveryResumeStartAuthorization,
) -> str:
    """Return SHA-256 over exact canonical authorization UTF-8 bytes."""
    return sha256(
        external_publication_recovery_resume_start_authorization_canonical_bytes(
            authorization
        )
    ).hexdigest()


def _canonical_authorization_target_path(
    resume_intent_binding_path: Path,
    binding_digest: str,
) -> Path:
    """Derive the only allowed Phase 296 authorization target for one binding.

    The rule is canonical for the exact Phase 295 binding so that the same
    binding can never materialize more than one valid Phase 296 authorization:

    ``resume_intent_binding_path.parent /
    "external-publication-recovery-resume-start-authorization-<binding digest>.json"``

    The caller may still supply ``resume_start_authorization_path``, but after
    the binding digest is computed it must be exactly equal to this derived
    target.  No normalization, resolve, or symlink-following equivalence is
    applied; only exact concrete ``Path`` equality is accepted.
    """
    return resume_intent_binding_path.parent / (
        f"{_AUTHORIZATION_FILENAME_PREFIX}{binding_digest}"
        f"{_AUTHORIZATION_FILENAME_SUFFIX}"
    )


def _validate_canonical_authorization_path(
    resume_intent_binding_path: Path,
    resume_start_authorization_path: Path,
    binding_digest: str,
) -> None:
    """Require the caller target to be the exact canonical authorization path.

    Runs after the exact Phase 295 binding has been strict-loaded, locally
    revalidated, and digested, and before the Phase 289 intent loader is called.
    Only exact concrete ``Path`` equality with the derived canonical target is
    accepted; no normalization, resolve, or symlink-following equivalence is
    applied, and no directory is created or artifact relocated.
    """
    try:
        canonical = _canonical_authorization_target_path(
            resume_intent_binding_path, binding_digest
        )
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("authorization_path")
    if resume_start_authorization_path != canonical:
        _raise_authorization("authorization_path")


def _future_start_target_path(
    resume_intent_binding_path: Path,
    authorization_digest: str,
) -> Path:
    """Derive the one canonical future Phase 297 start target for one authorization.

    The authoritative namespace is the exact Phase 295 binding parent, because
    Phase 296 already requires the authorization sidecar itself to live at the
    canonical path inside that same parent:

    ``resume_intent_binding_path.parent /
    "external-publication-recovery-resume-start-<authorization digest>.json"``

    Phase 296 itself neither creates nor checks this future marker, and a future
    Phase 297 must not accept an arbitrary caller-supplied start path.
    """
    return resume_intent_binding_path.parent / (
        f"{_FUTURE_START_FILENAME_PREFIX}{authorization_digest}"
        f"{_FUTURE_START_FILENAME_SUFFIX}"
    )


def load_external_publication_recovery_resume_start_authorization(
    path: Path,
) -> ExternalPublicationRecoveryResumeStartAuthorization:
    """Read and strictly revalidate one immutable canonical authorization record."""
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
        canonical = (
            external_publication_recovery_resume_start_authorization_canonical_bytes(
                authorization
            )
        )
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        _raise_load("load")
    except Exception:
        _raise_load("load")
    if canonical != contents:
        _raise_load("noncanonical")
    return authorization


def persist_external_publication_recovery_resume_start_authorization(
    path: Path,
    authorization: ExternalPublicationRecoveryResumeStartAuthorization,
) -> None:
    """Durably append-only persist one exact resume-start-authorization record.

    The canonical bytes are derived and validated first.  A new target is
    created exclusively, written in full, flushed, file-fsynced, closed, and
    then parent-directory-fsynced.  An identical existing target is an
    idempotent durable success; any other existing content is a fixed conflict
    and is never overwritten, truncated, deleted, or renamed over.  Any
    uncertainty after exclusive creation retains the artifact with no cleanup,
    no retry, and no rewrite.
    """
    _validate_persistence_target(path)
    _validate_authorization(authorization)
    try:
        contents = (
            external_publication_recovery_resume_start_authorization_canonical_bytes(
                authorization
            )
        )
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        raise
    except Exception:
        _raise_persistence("serialization")
    _write_authorization(path, contents)


def authorize_and_persist_external_publication_recovery_resume_start(
    *,
    resume_intent_binding_path: Path,
    resume_intent_path: Path,
    resume_start_authorization_path: Path,
    binding_loader: BindingLoader = (
        load_external_publication_recovery_resume_intent_binding
    ),
    binding_digest_function: BindingDigestFunction = (
        external_publication_recovery_resume_intent_binding_digest
    ),
    intent_loader: IntentLoader = load_external_publication_operation_intent,
    intent_digest_function: IntentDigestFunction = (
        external_publication_operation_intent_digest
    ),
    start_digest_function: StartDigestFunction = (
        external_publication_operation_start_digest
    ),
) -> ExternalPublicationRecoveryResumeStartAuthorization:
    """Authorize one future recovery resume start attempt from exact provenance.

    The exact Phase 295 binding is strict-loaded exactly once through the
    caller's exact ``resume_intent_binding_path`` identity and every Phase 295
    field and cross-field invariant is locally revalidated before its digest is
    computed exactly once from the exact loaded object.  The caller
    ``resume_start_authorization_path`` must then be exactly equal to the
    canonical authorization target derived from that exact binding path parent
    and exact computed binding digest; any other filename or parent fails closed
    with the fixed ``authorization_path`` classification before the Phase 289
    intent loader is called and before any target mutation.  Only then is the
    exact bound Phase 289 ``resume`` intent strict-loaded exactly once through the
    caller's exact ``resume_intent_path`` identity, locally revalidated, and its
    digest bound to the binding digest, approval digest, plan digest, and
    operation.  A Phase 289 intent alone is never recovery authority.

    The expected Phase 290 resume start identity is constructed in memory only,
    from the exact validated lineage, and its digest is computed exactly once
    through the public Phase 290 helper with the exact constructed object
    identity.  The start object is never persisted, never loaded, and never
    acquired; Phase 290 start acquisition is never called.

    An existing authorization target is strict-loaded once and returned by
    identity only when it equals the exact constructed record; any difference is
    a fixed conflict that leaves the existing bytes unchanged.  Otherwise the
    constructed record is persisted exactly once with no retry, and the exact
    constructed object is returned only after durable success.

    No Phase 288/291/292/293/294/295 orchestration is called, no provider,
    transport, or network work happens, and no caller-supplied binding, digest,
    approval, preparation, decision, expected start, source operation, recovery
    kind, or start path is accepted as authority.
    """
    _preflight(
        resume_intent_binding_path=resume_intent_binding_path,
        resume_intent_path=resume_intent_path,
        resume_start_authorization_path=resume_start_authorization_path,
        binding_loader=binding_loader,
        binding_digest_function=binding_digest_function,
        intent_loader=intent_loader,
        intent_digest_function=intent_digest_function,
        start_digest_function=start_digest_function,
    )

    binding = _strict_load_binding(binding_loader, resume_intent_binding_path)
    _validate_binding_contract(binding)
    binding_digest = _binding_digest_once(binding_digest_function, binding)

    _validate_canonical_authorization_path(
        resume_intent_binding_path, resume_start_authorization_path, binding_digest
    )

    intent = _strict_load_intent(intent_loader, resume_intent_path)
    _validate_intent_contract(intent)
    intent_digest = _intent_digest_once(intent_digest_function, intent)
    _validate_intent_lineage(binding, intent, intent_digest)

    expected_start = _construct_expected_start(intent, intent_digest)
    _validate_start_contract(expected_start)
    start_digest = _start_digest_once(start_digest_function, expected_start)

    constructed = _construct_authorization(
        binding=binding,
        intent=intent,
        binding_digest=binding_digest,
        intent_digest=intent_digest,
        start_digest=start_digest,
    )

    if _authorization_target_present(resume_start_authorization_path):
        return _resolve_existing_authorization(
            resume_start_authorization_path, constructed
        )
    persist_external_publication_recovery_resume_start_authorization(
        resume_start_authorization_path, constructed
    )
    return constructed


def _construct_authorization(
    *,
    binding: object,
    intent: object,
    binding_digest: str,
    intent_digest: str,
    start_digest: str,
) -> ExternalPublicationRecoveryResumeStartAuthorization:
    try:
        return ExternalPublicationRecoveryResumeStartAuthorization(
            schema_version=_AUTHORIZATION_SCHEMA_VERSION,  # type: ignore[arg-type]
            resume_intent_binding_sha256=binding_digest,
            resume_preparation_sha256=binding.resume_preparation_sha256,  # type: ignore[union-attr]
            recovery_decision_sha256=binding.recovery_decision_sha256,  # type: ignore[union-attr]
            operation_intent_sha256=intent_digest,
            expected_operation_start_sha256=start_digest,
            publication_approval_sha256=intent.publication_approval_sha256,  # type: ignore[union-attr]
            publication_plan_sha256=intent.publication_plan_sha256,  # type: ignore[union-attr]
            source_operation=binding.source_operation,  # type: ignore[union-attr]
            recovery_kind=binding.recovery_kind,  # type: ignore[union-attr]
            operation="resume",
            state="authorized",
        )
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("serialization")


def _preflight(
    *,
    resume_intent_binding_path: object,
    resume_intent_path: object,
    resume_start_authorization_path: object,
    binding_loader: object,
    binding_digest_function: object,
    intent_loader: object,
    intent_digest_function: object,
    start_digest_function: object,
) -> None:
    if (
        type(resume_intent_binding_path) is not _PATH_TYPE
        or type(resume_intent_path) is not _PATH_TYPE
        or type(resume_start_authorization_path) is not _PATH_TYPE
    ):
        _raise_authorization("path_type")
    if (
        not callable(binding_loader)
        or not callable(binding_digest_function)
        or not callable(intent_loader)
        or not callable(intent_digest_function)
        or not callable(start_digest_function)
    ):
        _raise_authorization("configuration")
    if (
        len(
            {
                resume_intent_binding_path,
                resume_intent_path,
                resume_start_authorization_path,
            }
        )
        != 3
    ):
        _raise_authorization("path_conflict")
    _validate_preflight_target(resume_start_authorization_path)


def _validate_preflight_target(path: object) -> None:
    try:
        if not path.parent.exists() or not path.parent.is_dir():  # type: ignore[union-attr]
            _raise_authorization("parent")
        if (
            path.is_symlink()  # type: ignore[union-attr]
            or path.is_dir()  # type: ignore[union-attr]
            or (path.exists() and not path.is_file())  # type: ignore[union-attr]
        ):
            _raise_authorization("target")
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("target")


def _strict_load_binding(loader: BindingLoader, binding_path: Path) -> object:
    try:
        binding = loader(binding_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_authorization("dependency_error")
    if type(binding) is not ExternalPublicationRecoveryResumeIntentBinding:
        _raise_authorization("binding_contract")
    return binding


def _binding_digest_once(
    digest_function: BindingDigestFunction, binding: object
) -> str:
    try:
        digest = digest_function(binding)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_authorization("dependency_error")
    if not _is_sha256(digest):
        _raise_authorization("binding_digest")
    return digest  # type: ignore[return-value]


def _strict_load_intent(loader: IntentLoader, intent_path: Path) -> object:
    try:
        intent = loader(intent_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_authorization("dependency_error")
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_authorization("intent_contract")
    return intent


def _intent_digest_once(digest_function: IntentDigestFunction, intent: object) -> str:
    try:
        digest = digest_function(intent)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_authorization("dependency_error")
    if not _is_sha256(digest):
        _raise_authorization("intent_digest")
    return digest  # type: ignore[return-value]


def _start_digest_once(digest_function: StartDigestFunction, start: object) -> str:
    try:
        digest = digest_function(start)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_authorization("dependency_error")
    if not _is_sha256(digest):
        _raise_authorization("start_digest")
    return digest  # type: ignore[return-value]


def _validate_binding_contract(binding: object) -> None:
    if type(binding) is not ExternalPublicationRecoveryResumeIntentBinding:
        _raise_authorization("binding_contract")
    try:
        schema_version = binding.schema_version  # type: ignore[union-attr]
        preparation_digest = binding.resume_preparation_sha256  # type: ignore[union-attr]
        decision_digest = binding.recovery_decision_sha256  # type: ignore[union-attr]
        approval_digest = binding.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = binding.publication_plan_sha256  # type: ignore[union-attr]
        intent_digest = binding.operation_intent_sha256  # type: ignore[union-attr]
        source_operation = binding.source_operation  # type: ignore[union-attr]
        recovery_kind = binding.recovery_kind  # type: ignore[union-attr]
        operation = binding.operation  # type: ignore[union-attr]
        state = binding.state  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("binding_contract")

    if type(schema_version) is not str or schema_version != _BINDING_SCHEMA_VERSION:
        _raise_authorization("binding_contract")
    if not _is_sha256(preparation_digest):
        _raise_authorization("binding_contract")
    if not _is_sha256(decision_digest):
        _raise_authorization("binding_contract")
    if not _is_sha256(approval_digest):
        _raise_authorization("binding_contract")
    if not _is_sha256(plan_digest):
        _raise_authorization("binding_contract")
    if not _is_sha256(intent_digest):
        _raise_authorization("binding_contract")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_authorization("binding_contract")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_authorization("binding_contract")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_authorization("binding_contract")
    if type(operation) is not str or operation not in _BINDING_OPERATIONS:
        _raise_authorization("binding_contract")
    if type(state) is not str or state not in _BINDING_STATES:
        _raise_authorization("binding_contract")


def _validate_intent_contract(intent: object) -> None:
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_authorization("intent_contract")
    try:
        schema_version = intent.schema_version  # type: ignore[union-attr]
        approval_digest = intent.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = intent.publication_plan_sha256  # type: ignore[union-attr]
        operation = intent.operation  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("intent_contract")

    if type(schema_version) is not str or schema_version != _INTENT_SCHEMA_VERSION:
        _raise_authorization("intent_contract")
    if not _is_sha256(approval_digest):
        _raise_authorization("intent_contract")
    if not _is_sha256(plan_digest):
        _raise_authorization("intent_contract")
    if type(operation) is not str or operation not in _INTENT_OPERATIONS:
        _raise_authorization("intent_contract")


def _validate_intent_lineage(
    binding: object, intent: object, intent_digest: str
) -> None:
    if binding.operation_intent_sha256 != intent_digest:  # type: ignore[union-attr]
        _raise_authorization("intent_lineage")
    if (
        intent.publication_approval_sha256  # type: ignore[union-attr]
        != binding.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_authorization("intent_lineage")
    if (
        intent.publication_plan_sha256  # type: ignore[union-attr]
        != binding.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_authorization("intent_lineage")
    if intent.operation != binding.operation:  # type: ignore[union-attr]
        _raise_authorization("intent_lineage")


def _construct_expected_start(
    intent: object, intent_digest: str
) -> ExternalPublicationOperationStart:
    try:
        return ExternalPublicationOperationStart(
            schema_version=_START_SCHEMA_VERSION,  # type: ignore[arg-type]
            operation_intent_sha256=intent_digest,
            publication_approval_sha256=intent.publication_approval_sha256,  # type: ignore[union-attr]
            publication_plan_sha256=intent.publication_plan_sha256,  # type: ignore[union-attr]
            operation="resume",
            state="started",
        )
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("start_contract")


def _validate_start_contract(start: object) -> None:
    if type(start) is not ExternalPublicationOperationStart:
        _raise_authorization("start_contract")
    try:
        schema_version = start.schema_version  # type: ignore[union-attr]
        intent_digest = start.operation_intent_sha256  # type: ignore[union-attr]
        approval_digest = start.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = start.publication_plan_sha256  # type: ignore[union-attr]
        operation = start.operation  # type: ignore[union-attr]
        state = start.state  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("start_contract")

    if type(schema_version) is not str or schema_version != _START_SCHEMA_VERSION:
        _raise_authorization("start_contract")
    if not _is_sha256(intent_digest):
        _raise_authorization("start_contract")
    if not _is_sha256(approval_digest):
        _raise_authorization("start_contract")
    if not _is_sha256(plan_digest):
        _raise_authorization("start_contract")
    if type(operation) is not str or operation != "resume":
        _raise_authorization("start_contract")
    if type(state) is not str or state not in _START_STATES:
        _raise_authorization("start_contract")


def _authorization_target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_load("target")


def _resolve_existing_authorization(
    resume_start_authorization_path: Path,
    constructed: ExternalPublicationRecoveryResumeStartAuthorization,
) -> ExternalPublicationRecoveryResumeStartAuthorization:
    loaded = load_external_publication_recovery_resume_start_authorization(
        resume_start_authorization_path
    )
    if type(loaded) is not ExternalPublicationRecoveryResumeStartAuthorization:
        _raise_authorization("load")
    if loaded != constructed:
        _raise_conflict()
    return loaded


def _validate_authorization(authorization: object) -> None:
    if type(authorization) is not ExternalPublicationRecoveryResumeStartAuthorization:
        _raise_authorization("configuration")
    try:
        _check_authorization(authorization)
    except ExternalPublicationRecoveryResumeStartAuthorizationError:
        raise
    except Exception:
        _raise_authorization("configuration")


def _check_authorization(authorization: object) -> None:
    schema_version = authorization.schema_version  # type: ignore[union-attr]
    binding_digest = authorization.resume_intent_binding_sha256  # type: ignore[union-attr]
    preparation_digest = authorization.resume_preparation_sha256  # type: ignore[union-attr]
    decision_digest = authorization.recovery_decision_sha256  # type: ignore[union-attr]
    intent_digest = authorization.operation_intent_sha256  # type: ignore[union-attr]
    start_digest = authorization.expected_operation_start_sha256  # type: ignore[union-attr]
    approval_digest = authorization.publication_approval_sha256  # type: ignore[union-attr]
    plan_digest = authorization.publication_plan_sha256  # type: ignore[union-attr]
    source_operation = authorization.source_operation  # type: ignore[union-attr]
    recovery_kind = authorization.recovery_kind  # type: ignore[union-attr]
    operation = authorization.operation  # type: ignore[union-attr]
    state = authorization.state  # type: ignore[union-attr]

    if (
        type(schema_version) is not str
        or schema_version != _AUTHORIZATION_SCHEMA_VERSION
    ):
        _raise_authorization("configuration")
    if not _is_sha256(binding_digest):
        _raise_authorization("configuration")
    if not _is_sha256(preparation_digest):
        _raise_authorization("configuration")
    if not _is_sha256(decision_digest):
        _raise_authorization("configuration")
    if not _is_sha256(intent_digest):
        _raise_authorization("configuration")
    if not _is_sha256(start_digest):
        _raise_authorization("configuration")
    if not _is_sha256(approval_digest):
        _raise_authorization("configuration")
    if not _is_sha256(plan_digest):
        _raise_authorization("configuration")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_authorization("configuration")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_authorization("configuration")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_authorization("configuration")
    if type(operation) is not str or operation not in _OPERATIONS:
        _raise_authorization("configuration")
    if type(state) is not str or state not in _STATES:
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
    except ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError:
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
    except ExternalPublicationRecoveryResumeStartAuthorizationLoadError:
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
                raise OSError("short resume start authorization write")
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
    except ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError:
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
            raise OSError("resume start authorization handle cannot close")
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
) -> ExternalPublicationRecoveryResumeStartAuthorization:
    if type(value) is not dict or frozenset(value) != _AUTHORIZATION_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationRecoveryResumeStartAuthorization(
            schema_version=value["schema_version"],
            resume_intent_binding_sha256=value["resume_intent_binding_sha256"],
            resume_preparation_sha256=value["resume_preparation_sha256"],
            recovery_decision_sha256=value["recovery_decision_sha256"],
            operation_intent_sha256=value["operation_intent_sha256"],
            expected_operation_start_sha256=value["expected_operation_start_sha256"],
            publication_approval_sha256=value["publication_approval_sha256"],
            publication_plan_sha256=value["publication_plan_sha256"],
            source_operation=value["source_operation"],
            recovery_kind=value["recovery_kind"],
            operation=value["operation"],
            state=value["state"],
        )
    except Exception:
        _raise_load("load")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


class _DuplicateKeyError(ValueError):
    pass


class _NonStandardJSONConstantError(ValueError):
    pass


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    del value
    raise _NonStandardJSONConstantError


def _raise_authorization(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError(
        classification
    ) from None


def _raise_persistence(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationRecoveryResumeStartAuthorizationConflictError(
        "conflict"
    ) from None


def _raise_load(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeStartAuthorizationLoadError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeStartAuthorization",
    "ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError",
    "ExternalPublicationRecoveryResumeStartAuthorizationConflictError",
    "ExternalPublicationRecoveryResumeStartAuthorizationError",
    "ExternalPublicationRecoveryResumeStartAuthorizationFailureDetail",
    "ExternalPublicationRecoveryResumeStartAuthorizationLoadError",
    "ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError",
    "authorize_and_persist_external_publication_recovery_resume_start",
    "external_publication_recovery_resume_start_authorization_canonical_bytes",
    "external_publication_recovery_resume_start_authorization_digest",
    "load_external_publication_recovery_resume_start_authorization",
    "persist_external_publication_recovery_resume_start_authorization",
    "serialize_external_publication_recovery_resume_start_authorization_canonical",
]
