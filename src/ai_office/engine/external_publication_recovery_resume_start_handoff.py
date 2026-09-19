"""Recovery-bound resume start handoff from Phase 296 authorization to Phase 291.

Phase 297 is the first recovery-specific boundary allowed to cross the Phase 290
start fence, and it does so only by delegating to the existing public Phase 291
same-invocation handoff.  The caller supplies exactly three runtime inputs: the
exact Phase 295 binding path, the exact bound Phase 289 resume intent path, and
one exact runtime resume request.

Both durable paths are derived internally.  The Phase 296 authorization path is
derived from the exact binding path parent and the exact computed binding
digest, and the Phase 290 start target is derived from the same exact binding
path parent and the exact computed authorization digest.  A caller-supplied
authorization path, start path, binding object, authorization object, intent
object, digest, operation, recovery kind, acquisition result, Phase 290
function, or Phase 288 function is never accepted as authority.

Phase 297 calls Phase 291 exactly once and lets Phase 291 own the Phase 290
acquisition and the Phase 288 resume execution handoff inside that same
invocation.  Phase 297 never calls Phase 290 or Phase 288 directly, never
reconstructs or persists ``acquired``, and treats ``already_acquired`` as a stop
result rather than as execution authority.  It adds no durable artifact of its
own, never retries, never replays, and never repairs or rewrites an existing
marker.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    external_publication_approval_digest,
)
from .external_publication_execution import (
    ExternalPublicationExecutionError,
)
from .external_publication_execution_evidence import (
    ExternalPublicationExecutionEvidenceError,
)
from .external_publication_execution_orchestration import (
    ExternalPublicationExecutionOrchestrationError,
)
from .external_publication_execution_reconciliation import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
)
from .external_publication_execution_reconciliation_evidence import (
    ExternalPublicationExecutionReconciliationEvidenceError,
)
from .external_publication_execution_reconciliation_orchestration import (
    ExternalPublicationExecutionReconciliationOrchestrationError,
)
from .external_publication_execution_reconciliation_resume import (
    ExternalPublicationExecutionReconciliationResumeError,
)
from .external_publication_operation import (
    ExternalPublicationFreshOperationRequest,
    ExternalPublicationOperationError,
    ExternalPublicationResumeOperationRequest,
)
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
    external_publication_operation_start_digest,
)
from .external_publication_operation_start_handoff import (
    ExternalPublicationOperationStartHandoffError,
    run_external_publication_operation_start_handoff,
)
from .external_publication_recovery_resume_intent_binding import (  # noqa: E501
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeIntentBindingError,
    external_publication_recovery_resume_intent_binding_digest,
    load_external_publication_recovery_resume_intent_binding,
)
from .external_publication_recovery_resume_start_authorization import (  # noqa: E501
    ExternalPublicationRecoveryResumeStartAuthorization,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    external_publication_recovery_resume_start_authorization_digest,
    load_external_publication_recovery_resume_start_authorization,
)

_HANDOFF_ERROR_MESSAGE = "external publication recovery resume start handoff is blocked"
_BINDING_SCHEMA_VERSION = "external-publication-recovery-resume-intent-binding.v1"
_AUTHORIZATION_SCHEMA_VERSION = (
    "external-publication-recovery-resume-start-authorization.v1"
)
_INTENT_SCHEMA_VERSION = "external-publication-operation-intent.v1"
_START_SCHEMA_VERSION = "external-publication-operation-start.v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
# Mirrors the current external publication approval metadata bound (256).
_MAX_APPROVAL_METADATA_LENGTH = 256
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_BINDING_OPERATIONS = frozenset({"resume"})
_BINDING_STATES = frozenset({"authorized"})
_AUTHORIZATION_OPERATIONS = frozenset({"resume"})
_AUTHORIZATION_STATES = frozenset({"authorized"})
_INTENT_OPERATIONS = frozenset({"resume"})
_START_STATES = frozenset({"started"})
_ACQUISITION_STOP_STATUS = "already_acquired"
_AUTHORIZATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-start-authorization-"
)
_AUTHORIZATION_FILENAME_SUFFIX = ".json"
_START_FILENAME_PREFIX = "external-publication-recovery-resume-start-"
_START_FILENAME_SUFFIX = ".json"
_REQUEST_PATH_FIELDS = (
    "ledger_directory",
    "execution_evidence_path",
    "execution_reconciliation_evidence_path",
)

Classification = Literal[
    "configuration",
    "path_type",
    "path_conflict",
    "request_contract",
    "binding_contract",
    "binding_digest",
    "authorization_contract",
    "authorization_digest",
    "authorization_lineage",
    "intent_contract",
    "intent_digest",
    "intent_lineage",
    "start_contract",
    "start_digest",
    "request_lineage",
    "result_contract",
    "dependency_error",
]

BindingLoader = Callable[[Path], object]
BindingDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeIntentBinding], object
]
AuthorizationLoader = Callable[[Path], object]
AuthorizationDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeStartAuthorization], object
]
IntentLoader = Callable[[Path], object]
IntentDigestFunction = Callable[[ExternalPublicationOperationIntent], object]
ApprovalDigestFunction = Callable[[ExternalPublicationApproval], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]
Phase291Function = Callable[..., object]

_KNOWN_PREDECESSOR_ERRORS = (
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationStartError,
    ExternalPublicationOperationStartHandoffError,
    ExternalPublicationOperationError,
    ExternalPublicationError,
    ExternalPublicationExecutionOrchestrationError,
    ExternalPublicationExecutionError,
    ExternalPublicationExecutionEvidenceError,
    ExternalPublicationExecutionReconciliationResumeError,
    ExternalPublicationExecutionReconciliationOrchestrationError,
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationEvidenceError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeStartHandoffFailureDetail:
    """Detail-safe classification for one recovery resume start handoff failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeStartHandoffError(ValueError):
    """Raised when a recovery resume start handoff cannot safely proceed."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_HANDOFF_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeStartHandoffFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeStartHandoffCompatibilityError(
    ExternalPublicationRecoveryResumeStartHandoffError
):
    """Raised when a path, dependency, lineage, or result is incompatible."""


def run_external_publication_recovery_resume_start_handoff(
    *,
    resume_intent_binding_path: Path,
    resume_intent_path: Path,
    request: ExternalPublicationResumeOperationRequest,
    binding_loader: BindingLoader = (
        load_external_publication_recovery_resume_intent_binding
    ),
    binding_digest_function: BindingDigestFunction = (
        external_publication_recovery_resume_intent_binding_digest
    ),
    authorization_loader: AuthorizationLoader = (
        load_external_publication_recovery_resume_start_authorization
    ),
    authorization_digest_function: AuthorizationDigestFunction = (
        external_publication_recovery_resume_start_authorization_digest
    ),
    intent_loader: IntentLoader = load_external_publication_operation_intent,
    intent_digest_function: IntentDigestFunction = (
        external_publication_operation_intent_digest
    ),
    approval_digest_function: ApprovalDigestFunction = (
        external_publication_approval_digest
    ),
    start_digest_function: StartDigestFunction = (
        external_publication_operation_start_digest
    ),
    phase291_function: Phase291Function = (
        run_external_publication_operation_start_handoff
    ),
) -> (
    ExternalPublicationOperationStartAcquisition
    | ExternalPublicationExecutionReconciliation
):
    """Hand one authorized recovery resume attempt to Phase 291 exactly once.

    The exact Phase 295 binding is strict-loaded once and locally revalidated
    before its digest is computed exactly once.  The Phase 296 authorization
    path is then derived internally from the exact binding path parent and the
    exact computed binding digest, strict-loaded once, locally revalidated,
    digested, and checked against the binding lineage.  Only afterwards is the
    exact bound Phase 289 resume intent loaded, validated, digested, and checked
    against both the binding and the authorization.  The expected Phase 290
    start identity is reconstructed in memory only and its digest must equal the
    authorization ``expected_operation_start_sha256``.  Finally the runtime
    request approval and plan lineage are checked before the canonical start
    path is derived and Phase 291 is called exactly once with the exact intent
    path, the exact derived start path, and the exact caller request.

    No marker is created, loaded, inspected, overwritten, deleted, or repaired
    here.  ``acquired`` is never reconstructed and ``already_acquired`` is never
    converted into execution authority.
    """
    _preflight(
        resume_intent_binding_path=resume_intent_binding_path,
        resume_intent_path=resume_intent_path,
        request=request,
        binding_loader=binding_loader,
        binding_digest_function=binding_digest_function,
        authorization_loader=authorization_loader,
        authorization_digest_function=authorization_digest_function,
        intent_loader=intent_loader,
        intent_digest_function=intent_digest_function,
        approval_digest_function=approval_digest_function,
        start_digest_function=start_digest_function,
        phase291_function=phase291_function,
    )

    binding = _strict_load_binding(binding_loader, resume_intent_binding_path)
    _validate_binding_contract(binding)
    binding_digest = _binding_digest_once(binding_digest_function, binding)

    authorization_path = _derive_authorization_path(
        resume_intent_binding_path, binding_digest
    )
    authorization = _strict_load_authorization(authorization_loader, authorization_path)
    _validate_authorization_contract(authorization)
    authorization_digest = _authorization_digest_once(
        authorization_digest_function, authorization
    )
    _validate_authorization_lineage(authorization, binding, binding_digest)

    intent = _strict_load_intent(intent_loader, resume_intent_path)
    _validate_intent_contract(intent)
    intent_digest = _intent_digest_once(intent_digest_function, intent)
    _validate_intent_lineage(intent, binding, authorization, intent_digest)

    expected_start = _construct_expected_start(intent, intent_digest)
    _validate_start_contract(expected_start)
    start_digest = _start_digest_once(start_digest_function, expected_start)
    if start_digest != authorization.expected_operation_start_sha256:  # type: ignore[union-attr]
        _raise_handoff("start_digest")

    _validate_request_lineage(
        request=request,
        binding=binding,
        authorization=authorization,
        intent=intent,
        approval_digest_function=approval_digest_function,
    )

    start_path = _derive_start_path(resume_intent_binding_path, authorization_digest)
    result = _call_phase291(
        phase291_function=phase291_function,
        resume_intent_path=resume_intent_path,
        start_path=start_path,
        request=request,
    )
    _validate_result(
        result,
        authorization=authorization,
        expected_start=expected_start,
        expected_start_digest=start_digest,
    )
    return result  # type: ignore[return-value]


def _preflight(
    *,
    resume_intent_binding_path: object,
    resume_intent_path: object,
    request: object,
    binding_loader: object,
    binding_digest_function: object,
    authorization_loader: object,
    authorization_digest_function: object,
    intent_loader: object,
    intent_digest_function: object,
    approval_digest_function: object,
    start_digest_function: object,
    phase291_function: object,
) -> None:
    """Reject every invalid input before any load, digest, or Phase 291 call."""
    if (
        type(resume_intent_binding_path) is not _PATH_TYPE
        or type(resume_intent_path) is not _PATH_TYPE
    ):
        _raise_handoff("path_type")
    if resume_intent_binding_path == resume_intent_path:
        _raise_handoff("path_conflict")

    if type(request) is ExternalPublicationFreshOperationRequest:
        _raise_handoff("request_contract")
    if type(request) is not ExternalPublicationResumeOperationRequest:
        _raise_handoff("request_contract")

    if not (
        callable(binding_loader)
        and callable(binding_digest_function)
        and callable(authorization_loader)
        and callable(authorization_digest_function)
        and callable(intent_loader)
        and callable(intent_digest_function)
        and callable(approval_digest_function)
        and callable(start_digest_function)
        and callable(phase291_function)
    ):
        _raise_handoff("configuration")

    for field_name in _REQUEST_PATH_FIELDS:
        if type(getattr(request, field_name, None)) is not _PATH_TYPE:
            _raise_handoff("path_type")
    if hasattr(request, "transport") or hasattr(request, "provider"):
        _raise_handoff("request_contract")

    approval = request.approval  # type: ignore[union-attr]
    if type(approval) is not ExternalPublicationApproval:
        _raise_handoff("request_contract")
    try:
        approved = approval.approved
        plan_digest = approval.publication_plan_sha256
        approved_by = approval.approved_by
        approval_id = approval.approval_id
    except Exception:
        _raise_handoff("request_contract")
    if type(approved) is not bool or approved is not True:
        _raise_handoff("request_contract")
    if not _is_sha256(plan_digest):
        _raise_handoff("request_contract")
    if not _is_valid_metadata(approved_by) or not _is_valid_metadata(approval_id):
        _raise_handoff("request_contract")


def _strict_load_binding(loader: BindingLoader, binding_path: Path) -> object:
    try:
        binding = loader(binding_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if type(binding) is not ExternalPublicationRecoveryResumeIntentBinding:
        _raise_handoff("binding_contract")
    return binding


def _binding_digest_once(
    digest_function: BindingDigestFunction, binding: object
) -> str:
    try:
        digest = digest_function(binding)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if not _is_sha256(digest):
        _raise_handoff("binding_digest")
    return digest  # type: ignore[return-value]


def _derive_authorization_path(
    resume_intent_binding_path: Path, binding_digest: str
) -> Path:
    """Derive the only allowed Phase 296 authorization path for one binding."""
    return resume_intent_binding_path.parent / (
        f"{_AUTHORIZATION_FILENAME_PREFIX}{binding_digest}"
        f"{_AUTHORIZATION_FILENAME_SUFFIX}"
    )


def _strict_load_authorization(
    loader: AuthorizationLoader, authorization_path: Path
) -> object:
    try:
        authorization = loader(authorization_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if type(authorization) is not ExternalPublicationRecoveryResumeStartAuthorization:
        _raise_handoff("authorization_contract")
    return authorization


def _authorization_digest_once(
    digest_function: AuthorizationDigestFunction, authorization: object
) -> str:
    try:
        digest = digest_function(authorization)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if not _is_sha256(digest):
        _raise_handoff("authorization_digest")
    return digest  # type: ignore[return-value]


def _validate_binding_contract(binding: object) -> None:
    if type(binding) is not ExternalPublicationRecoveryResumeIntentBinding:
        _raise_handoff("binding_contract")
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
    except ExternalPublicationRecoveryResumeStartHandoffError:
        raise
    except Exception:
        _raise_handoff("binding_contract")

    if type(schema_version) is not str or schema_version != _BINDING_SCHEMA_VERSION:
        _raise_handoff("binding_contract")
    if not _is_sha256(preparation_digest):
        _raise_handoff("binding_contract")
    if not _is_sha256(decision_digest):
        _raise_handoff("binding_contract")
    if not _is_sha256(approval_digest):
        _raise_handoff("binding_contract")
    if not _is_sha256(plan_digest):
        _raise_handoff("binding_contract")
    if not _is_sha256(intent_digest):
        _raise_handoff("binding_contract")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_handoff("binding_contract")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_handoff("binding_contract")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_handoff("binding_contract")
    if type(operation) is not str or operation not in _BINDING_OPERATIONS:
        _raise_handoff("binding_contract")
    if type(state) is not str or state not in _BINDING_STATES:
        _raise_handoff("binding_contract")


def _validate_authorization_contract(authorization: object) -> None:
    if type(authorization) is not ExternalPublicationRecoveryResumeStartAuthorization:
        _raise_handoff("authorization_contract")
    try:
        schema_version = authorization.schema_version  # type: ignore[union-attr]
        binding_digest = authorization.resume_intent_binding_sha256  # type: ignore[union-attr]
        preparation_digest = authorization.resume_preparation_sha256  # type: ignore[union-attr]
        decision_digest = authorization.recovery_decision_sha256  # type: ignore[union-attr]
        intent_digest = authorization.operation_intent_sha256  # type: ignore[union-attr]
        expected_start_digest = (  # type: ignore[union-attr]
            authorization.expected_operation_start_sha256
        )
        approval_digest = authorization.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = authorization.publication_plan_sha256  # type: ignore[union-attr]
        source_operation = authorization.source_operation  # type: ignore[union-attr]
        recovery_kind = authorization.recovery_kind  # type: ignore[union-attr]
        operation = authorization.operation  # type: ignore[union-attr]
        state = authorization.state  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeStartHandoffError:
        raise
    except Exception:
        _raise_handoff("authorization_contract")

    if (
        type(schema_version) is not str
        or schema_version != _AUTHORIZATION_SCHEMA_VERSION
    ):
        _raise_handoff("authorization_contract")
    if not _is_sha256(binding_digest):
        _raise_handoff("authorization_contract")
    if not _is_sha256(preparation_digest):
        _raise_handoff("authorization_contract")
    if not _is_sha256(decision_digest):
        _raise_handoff("authorization_contract")
    if not _is_sha256(intent_digest):
        _raise_handoff("authorization_contract")
    if not _is_sha256(expected_start_digest):
        _raise_handoff("authorization_contract")
    if not _is_sha256(approval_digest):
        _raise_handoff("authorization_contract")
    if not _is_sha256(plan_digest):
        _raise_handoff("authorization_contract")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_handoff("authorization_contract")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_handoff("authorization_contract")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_handoff("authorization_contract")
    if type(operation) is not str or operation not in _AUTHORIZATION_OPERATIONS:
        _raise_handoff("authorization_contract")
    if type(state) is not str or state not in _AUTHORIZATION_STATES:
        _raise_handoff("authorization_contract")


def _validate_authorization_lineage(
    authorization: object, binding: object, binding_digest: str
) -> None:
    if (
        authorization.resume_intent_binding_sha256 != binding_digest  # type: ignore[union-attr]
    ):
        _raise_handoff("authorization_lineage")
    if (
        authorization.resume_preparation_sha256  # type: ignore[union-attr]
        != binding.resume_preparation_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("authorization_lineage")
    if (
        authorization.recovery_decision_sha256  # type: ignore[union-attr]
        != binding.recovery_decision_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("authorization_lineage")
    if (
        authorization.operation_intent_sha256  # type: ignore[union-attr]
        != binding.operation_intent_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("authorization_lineage")
    if (
        authorization.publication_approval_sha256  # type: ignore[union-attr]
        != binding.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("authorization_lineage")
    if (
        authorization.publication_plan_sha256  # type: ignore[union-attr]
        != binding.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("authorization_lineage")
    if (
        authorization.source_operation  # type: ignore[union-attr]
        != binding.source_operation  # type: ignore[union-attr]
    ):
        _raise_handoff("authorization_lineage")
    if (
        authorization.recovery_kind  # type: ignore[union-attr]
        != binding.recovery_kind  # type: ignore[union-attr]
    ):
        _raise_handoff("authorization_lineage")
    if authorization.operation != binding.operation:  # type: ignore[union-attr]
        _raise_handoff("authorization_lineage")
    if authorization.operation != "resume":  # type: ignore[union-attr]
        _raise_handoff("authorization_lineage")
    if authorization.state != "authorized":  # type: ignore[union-attr]
        _raise_handoff("authorization_lineage")


def _strict_load_intent(loader: IntentLoader, intent_path: Path) -> object:
    try:
        intent = loader(intent_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_handoff("intent_contract")
    return intent


def _intent_digest_once(digest_function: IntentDigestFunction, intent: object) -> str:
    try:
        digest = digest_function(intent)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if not _is_sha256(digest):
        _raise_handoff("intent_digest")
    return digest  # type: ignore[return-value]


def _validate_intent_contract(intent: object) -> None:
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_handoff("intent_contract")
    try:
        schema_version = intent.schema_version  # type: ignore[union-attr]
        approval_digest = intent.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = intent.publication_plan_sha256  # type: ignore[union-attr]
        operation = intent.operation  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeStartHandoffError:
        raise
    except Exception:
        _raise_handoff("intent_contract")

    if type(schema_version) is not str or schema_version != _INTENT_SCHEMA_VERSION:
        _raise_handoff("intent_contract")
    if not _is_sha256(approval_digest):
        _raise_handoff("intent_contract")
    if not _is_sha256(plan_digest):
        _raise_handoff("intent_contract")
    if type(operation) is not str or operation not in _INTENT_OPERATIONS:
        _raise_handoff("intent_contract")


def _validate_intent_lineage(
    intent: object,
    binding: object,
    authorization: object,
    intent_digest: str,
) -> None:
    if intent_digest != binding.operation_intent_sha256:  # type: ignore[union-attr]
        _raise_handoff("intent_lineage")
    if intent_digest != authorization.operation_intent_sha256:  # type: ignore[union-attr]
        _raise_handoff("intent_lineage")
    if (
        intent.publication_approval_sha256  # type: ignore[union-attr]
        != binding.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("intent_lineage")
    if (
        intent.publication_approval_sha256  # type: ignore[union-attr]
        != authorization.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("intent_lineage")
    if (
        intent.publication_plan_sha256  # type: ignore[union-attr]
        != binding.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("intent_lineage")
    if (
        intent.publication_plan_sha256  # type: ignore[union-attr]
        != authorization.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("intent_lineage")
    if intent.operation != binding.operation:  # type: ignore[union-attr]
        _raise_handoff("intent_lineage")
    if intent.operation != authorization.operation:  # type: ignore[union-attr]
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
            operation="resume",
            state="started",
        )
    except ExternalPublicationRecoveryResumeStartHandoffError:
        raise
    except Exception:
        _raise_handoff("start_contract")


def _validate_start_contract(
    start: object, classification: Classification = "start_contract"
) -> None:
    if type(start) is not ExternalPublicationOperationStart:
        _raise_handoff(classification)
    try:
        schema_version = start.schema_version  # type: ignore[union-attr]
        intent_digest = start.operation_intent_sha256  # type: ignore[union-attr]
        approval_digest = start.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = start.publication_plan_sha256  # type: ignore[union-attr]
        operation = start.operation  # type: ignore[union-attr]
        state = start.state  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeStartHandoffError:
        raise
    except Exception:
        _raise_handoff(classification)

    if type(schema_version) is not str or schema_version != _START_SCHEMA_VERSION:
        _raise_handoff(classification)
    if not _is_sha256(intent_digest):
        _raise_handoff(classification)
    if not _is_sha256(approval_digest):
        _raise_handoff(classification)
    if not _is_sha256(plan_digest):
        _raise_handoff(classification)
    if type(operation) is not str or operation != "resume":
        _raise_handoff(classification)
    if type(state) is not str or state not in _START_STATES:
        _raise_handoff(classification)


def _start_digest_once(digest_function: StartDigestFunction, start: object) -> str:
    try:
        digest = digest_function(start)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if not _is_sha256(digest):
        _raise_handoff("start_digest")
    return digest  # type: ignore[return-value]


def _validate_request_lineage(
    *,
    request: ExternalPublicationResumeOperationRequest,
    binding: object,
    authorization: object,
    intent: object,
    approval_digest_function: ApprovalDigestFunction,
) -> str:
    try:
        approval_digest = approval_digest_function(request.approval)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if not _is_sha256(approval_digest):
        _raise_handoff("request_lineage")
    if (
        approval_digest != authorization.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("request_lineage")
    if approval_digest != binding.publication_approval_sha256:  # type: ignore[union-attr]
        _raise_handoff("request_lineage")

    plan_digest = request.approval.publication_plan_sha256
    if plan_digest != authorization.publication_plan_sha256:  # type: ignore[union-attr]
        _raise_handoff("request_lineage")
    if plan_digest != binding.publication_plan_sha256:  # type: ignore[union-attr]
        _raise_handoff("request_lineage")
    if plan_digest != intent.publication_plan_sha256:  # type: ignore[union-attr]
        _raise_handoff("request_lineage")
    return approval_digest  # type: ignore[return-value]


def _derive_start_path(
    resume_intent_binding_path: Path, authorization_digest: str
) -> Path:
    """Derive the one canonical Phase 290 start target for one authorization."""
    return resume_intent_binding_path.parent / (
        f"{_START_FILENAME_PREFIX}{authorization_digest}{_START_FILENAME_SUFFIX}"
    )


def _call_phase291(
    *,
    phase291_function: Phase291Function,
    resume_intent_path: Path,
    start_path: Path,
    request: ExternalPublicationResumeOperationRequest,
) -> object:
    """Call the public Phase 291 handoff exactly once with exact identities."""
    try:
        return phase291_function(
            intent_path=resume_intent_path,
            start_path=start_path,
            request=request,
        )
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")


def _validate_result(
    result: object,
    *,
    authorization: object,
    expected_start: ExternalPublicationOperationStart,
    expected_start_digest: str,
) -> None:
    if type(result) is ExternalPublicationOperationStartAcquisition:
        _validate_acquisition_result(
            result,
            authorization=authorization,
            expected_start=expected_start,
            expected_start_digest=expected_start_digest,
        )
        return
    if type(result) is ExternalPublicationExecutionReconciliation:
        _validate_reconciliation_result(result)
        return
    _raise_handoff("result_contract")


def _validate_acquisition_result(
    acquisition: object,
    *,
    authorization: object,
    expected_start: ExternalPublicationOperationStart,
    expected_start_digest: str,
) -> None:
    status = acquisition.status  # type: ignore[union-attr]
    if type(status) is not str or status != _ACQUISITION_STOP_STATUS:
        _raise_handoff("result_contract")
    if expected_start_digest != authorization.expected_operation_start_sha256:  # type: ignore[union-attr]
        _raise_handoff("start_digest")
    start = acquisition.start  # type: ignore[union-attr]
    _validate_start_contract(start, "result_contract")
    if (
        start.operation_intent_sha256  # type: ignore[union-attr]
        != authorization.operation_intent_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("result_contract")
    if (
        start.publication_approval_sha256  # type: ignore[union-attr]
        != authorization.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("result_contract")
    if (
        start.publication_plan_sha256  # type: ignore[union-attr]
        != authorization.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_handoff("result_contract")
    if start.operation != "resume":  # type: ignore[union-attr]
        _raise_handoff("result_contract")
    if start != expected_start:  # type: ignore[operator]
        _raise_handoff("result_contract")


def _validate_reconciliation_result(result: object) -> None:
    try:
        ExternalPublicationExecutionReconciliation(
            schema_version=result.schema_version,  # type: ignore[union-attr]
            claim_sha256=result.claim_sha256,  # type: ignore[union-attr]
            execution_evidence_sha256=(  # type: ignore[union-attr]
                result.execution_evidence_sha256
            ),
            status=result.status,  # type: ignore[union-attr]
            mismatched_fields=result.mismatched_fields,  # type: ignore[union-attr]
        )
    except ExternalPublicationRecoveryResumeStartHandoffError:
        raise
    except Exception:
        _raise_handoff("result_contract")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _is_valid_metadata(value: object) -> bool:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > _MAX_APPROVAL_METADATA_LENGTH
    ):
        return False
    return all(
        unicodedata.category(character) not in {"Cc", "Cs"} for character in value
    )


def _raise_handoff(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeStartHandoffCompatibilityError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeStartHandoffCompatibilityError",
    "ExternalPublicationRecoveryResumeStartHandoffError",
    "ExternalPublicationRecoveryResumeStartHandoffFailureDetail",
    "run_external_publication_recovery_resume_start_handoff",
]
