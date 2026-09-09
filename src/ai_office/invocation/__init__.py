"""Provider-independent model invocation requests."""

from ai_office.invocation.model_invocation_execution_approval import (
    ModelInvocationExecutionApproval,
    ModelInvocationExecutionApprovalError,
    approve_model_invocation_execution,
    build_model_invocation_execution_fingerprint,
    validate_model_invocation_execution_approval,
)
from ai_office.invocation.model_invocation_request import (
    ModelInvocationRequest,
    UpstreamStepOutput,
    build_model_invocation_request,
    build_model_invocation_task_input,
)
from ai_office.invocation.model_invocation_result import (
    ModelInvocationFailure,
    ModelInvocationFailureCategory,
    ModelInvocationFailureDiagnostics,
    ModelInvocationResponseBodyKind,
    ModelInvocationResult,
    ModelInvocationSuccess,
)
from ai_office.invocation.runtime_facts import (
    EMPTY_RUNTIME_FACTS,
    RuntimeFact,
    RuntimeFactOrigin,
    RuntimeFactProvenance,
    RuntimeFactsError,
    RuntimeFactsSnapshot,
    RuntimeFactValueKind,
    normalize_runtime_fact_timestamp,
    runtime_facts_snapshot_canonical_bytes,
    runtime_facts_snapshot_digest,
    serialize_runtime_facts_snapshot_canonical,
)

__all__ = [
    "ModelInvocationFailure",
    "ModelInvocationFailureCategory",
    "ModelInvocationFailureDiagnostics",
    "ModelInvocationResponseBodyKind",
    "ModelInvocationExecutionApproval",
    "ModelInvocationExecutionApprovalError",
    "ModelInvocationRequest",
    "UpstreamStepOutput",
    "ModelInvocationResult",
    "ModelInvocationSuccess",
    "EMPTY_RUNTIME_FACTS",
    "RuntimeFact",
    "RuntimeFactOrigin",
    "RuntimeFactProvenance",
    "RuntimeFactsError",
    "RuntimeFactsSnapshot",
    "RuntimeFactValueKind",
    "normalize_runtime_fact_timestamp",
    "runtime_facts_snapshot_canonical_bytes",
    "runtime_facts_snapshot_digest",
    "serialize_runtime_facts_snapshot_canonical",
    "approve_model_invocation_execution",
    "build_model_invocation_task_input",
    "build_model_invocation_execution_fingerprint",
    "build_model_invocation_request",
    "validate_model_invocation_execution_approval",
]
