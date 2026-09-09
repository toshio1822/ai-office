"""Focused tests for the provider-independent runtime-facts core."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

from ai_office.execution_target import DIRECT_OPENAI_EXECUTION_TARGET
from ai_office.invocation import (
    EMPTY_RUNTIME_FACTS,
    ModelInvocationRequest,
    RuntimeFact,
    RuntimeFactProvenance,
    RuntimeFactsError,
    RuntimeFactsSnapshot,
    UpstreamStepOutput,
    build_model_invocation_execution_fingerprint,
    build_model_invocation_task_input,
    normalize_runtime_fact_timestamp,
    runtime_facts_snapshot_canonical_bytes,
    runtime_facts_snapshot_digest,
    serialize_runtime_facts_snapshot_canonical,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition

_SHA_A = "a" * 64
_SHA_B = "b" * 64


def provenance(
    *,
    origin: str = "persisted_event",
    workflow_id: str = "article-workflow",
    source_ref: str = "event:4",
    source_sha256: str = _SHA_A,
    observed_at: str | None = None,
) -> RuntimeFactProvenance:
    return RuntimeFactProvenance(
        origin=origin,  # type: ignore[arg-type]
        workflow_id=workflow_id,
        source_ref=source_ref,
        source_sha256=source_sha256,
        observed_at=observed_at,
    )


def fact(
    key: str = "workflow.status",
    value_kind: str = "enum",
    value: object = "workflow_complete",
    *,
    source: RuntimeFactProvenance | None = None,
) -> RuntimeFact:
    return RuntimeFact(
        key=key,
        value_kind=value_kind,  # type: ignore[arg-type]
        value=value,  # type: ignore[arg-type]
        provenance=source or provenance(),
    )


def test_empty_snapshot_is_canonical_frozen_and_deterministic() -> None:
    assert EMPTY_RUNTIME_FACTS == RuntimeFactsSnapshot()
    assert EMPTY_RUNTIME_FACTS.facts == ()
    assert serialize_runtime_facts_snapshot_canonical(EMPTY_RUNTIME_FACTS) == (
        '{"facts":[],"schema_version":"runtime-facts.v1"}'
    )
    assert runtime_facts_snapshot_canonical_bytes(EMPTY_RUNTIME_FACTS) == (
        b'{"facts":[],"schema_version":"runtime-facts.v1"}'
    )
    assert EMPTY_RUNTIME_FACTS.digest == (
        "d191f6b2e213a64c9fe51281a8af55c9d9ac2b17b877b2ff42a32a4257c61365"
    )
    assert EMPTY_RUNTIME_FACTS.digest == runtime_facts_snapshot_digest(
        EMPTY_RUNTIME_FACTS
    )
    with pytest.raises(FrozenInstanceError):
        EMPTY_RUNTIME_FACTS.schema_version = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("value_kind", "value", "expected"),
    [
        ("identifier", "review-article", "review-article"),
        ("enum", "workflow_complete", "workflow_complete"),
        ("integer", 4, 4),
        ("boolean", True, True),
        ("timestamp", "2026-09-09T18:00:00+09:00", "2026-09-09T09:00:00Z"),
    ],
)
def test_one_fact_for_each_value_kind_is_valid_and_canonical(
    value_kind: str, value: object, expected: object
) -> None:
    value = fact(key=f"fact.{value_kind}", value_kind=value_kind, value=value)

    assert value.value == expected
    assert RuntimeFactsSnapshot(facts=(value,)).facts == (value,)


@pytest.mark.parametrize(
    "origin", ["persisted_state", "persisted_event", "human_supplied"]
)
def test_all_v1_provenance_origins_are_valid(origin: str) -> None:
    value = fact(source=provenance(origin=origin))

    assert value.provenance.origin == origin


def test_representative_snapshot_has_exact_canonical_json_and_digest() -> None:
    snapshot = RuntimeFactsSnapshot(
        facts=(
            fact(
                key="provider.identity",
                value_kind="enum",
                value="omniroute",
                source=provenance(),
            ),
        )
    )
    expected = (
        '{"facts":[{"key":"provider.identity","provenance":{"observed_at":'
        'null,"origin":"persisted_event","source_ref":"event:4",'
        '"source_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaa","workflow_id":"article-workflow"},"value":"omniroute",'
        '"value_kind":"enum"}],"schema_version":"runtime-facts.v1"}'
    )

    assert serialize_runtime_facts_snapshot_canonical(snapshot) == expected
    assert len(expected.encode("utf-8")) == 315
    assert runtime_facts_snapshot_digest(snapshot) == (
        "6734d8e76d7a5ca208b335ed9b7cd146b49a06fee6db6b95c7886ff6dfc04f5d"
    )


def test_canonical_order_is_independent_of_caller_order() -> None:
    first = fact(
        key="workflow.status",
        value_kind="enum",
        value="succeeded",
        source=provenance(source_ref="state"),
    )
    second = fact(
        key="step.completed_count",
        value_kind="integer",
        value=4,
        source=provenance(source_ref="state"),
    )
    left = RuntimeFactsSnapshot(facts=(first, second))
    right = RuntimeFactsSnapshot(facts=(second, first))

    assert left.facts == right.facts
    assert runtime_facts_snapshot_canonical_bytes(left) == (
        runtime_facts_snapshot_canonical_bytes(right)
    )
    assert left.digest == right.digest


def test_digest_changes_for_value_kind_and_value_changes() -> None:
    base = RuntimeFactsSnapshot(facts=(fact(),))
    changed_value = RuntimeFactsSnapshot(
        facts=(fact(value="persisted_failure"),)
    )
    changed_kind = RuntimeFactsSnapshot(
        facts=(fact(value_kind="identifier", value="workflow-complete"),)
    )

    assert base.digest != changed_value.digest
    assert base.digest != changed_kind.digest
    assert len(base.digest) == 64
    assert base.digest == base.digest.lower()


@pytest.mark.parametrize(
    "changed",
    [
        replace(provenance(), origin="persisted_state"),
        replace(provenance(), source_ref="state"),
        replace(provenance(), source_sha256=_SHA_B),
        replace(provenance(), observed_at="2026-09-09T09:00:00Z"),
    ],
)
def test_digest_changes_for_provenance_only_changes(
    changed: RuntimeFactProvenance,
) -> None:
    base = RuntimeFactsSnapshot(facts=(fact(),))
    modified = RuntimeFactsSnapshot(facts=(fact(source=changed),))

    assert base.digest != modified.digest


def test_timestamp_equivalents_normalize_to_the_same_value() -> None:
    assert normalize_runtime_fact_timestamp("2026-09-09T18:00:00+09:00") == (
        "2026-09-09T09:00:00Z"
    )
    assert normalize_runtime_fact_timestamp("2026-09-09T09:00:00Z") == (
        "2026-09-09T09:00:00Z"
    )
    assert normalize_runtime_fact_timestamp("2026-09-09T09:00:00.1200+00:00") == (
        "2026-09-09T09:00:00.12Z"
    )
    assert normalize_runtime_fact_timestamp(
        "2026-09-09T09:00:00.123456789+00:00"
    ) == "2026-09-09T09:00:00.123456789Z"


def test_timestamp_value_rejects_rfc3339_unknown_local_offset() -> None:
    with pytest.raises(RuntimeFactsError):
        fact(
            key="observed.at",
            value_kind="timestamp",
            value="2026-09-09T09:00:00-00:00",
        )


def test_provenance_observed_at_rejects_rfc3339_unknown_local_offset() -> None:
    with pytest.raises(RuntimeFactsError):
        provenance(observed_at="2026-09-09T09:00:00-00:00")


def test_duplicate_fact_keys_are_rejected_without_selecting_a_winner() -> None:
    with pytest.raises(RuntimeFactsError, match="^runtime facts are invalid$"):
        RuntimeFactsSnapshot(facts=(fact(), fact(value="succeeded")))


@pytest.mark.parametrize(
    "value",
    [
        "Persisted_state",
        "unknown",
        "",
        "persisted-state",
    ],
)
def test_unknown_or_invalid_origins_are_rejected(value: str) -> None:
    with pytest.raises(RuntimeFactsError):
        provenance(origin=value)


@pytest.mark.parametrize("value", ["string", "object", "", "integer-value"])
def test_unknown_or_invalid_value_kinds_are_rejected(value: str) -> None:
    with pytest.raises(RuntimeFactsError):
        fact(value_kind=value, value="value")


@pytest.mark.parametrize(
    "key",
    ["", "Workflow.status", "workflow status", ".status", "workflow..status", "a/../b"],
)
def test_fact_key_grammar_is_narrow_and_does_not_trim(key: str) -> None:
    with pytest.raises(RuntimeFactsError):
        fact(key=key)


@pytest.mark.parametrize(
    "value",
    ["Review-article", "review_article", "review article", "", "../review"],
)
def test_identifier_value_rejects_non_identifier_data(value: str) -> None:
    with pytest.raises(RuntimeFactsError):
        fact(key="step.id", value_kind="identifier", value=value)


@pytest.mark.parametrize(
    "value",
    ["WorkflowComplete", "workflow complete", "", "workflow/complete", "true value"],
)
def test_enum_value_rejects_free_form_or_non_token_data(value: str) -> None:
    with pytest.raises(RuntimeFactsError):
        fact(key="workflow.status", value_kind="enum", value=value)


@pytest.mark.parametrize("value", [True, 1.0, "4", " 4"])
def test_integer_value_requires_exact_signed_integer(value: object) -> None:
    with pytest.raises(RuntimeFactsError):
        fact(key="step.index", value_kind="integer", value=value)


@pytest.mark.parametrize("value", [0, 1, "true", "false", 1.0])
def test_boolean_value_requires_exact_bool(value: object) -> None:
    with pytest.raises(RuntimeFactsError):
        fact(key="step.completed", value_kind="boolean", value=value)


@pytest.mark.parametrize("value", [-(2**63) - 1, 2**63, 1.5, "1"])
def test_integer_value_rejects_out_of_range_and_coercible_values(
    value: object,
) -> None:
    with pytest.raises(RuntimeFactsError):
        fact(key="step.index", value_kind="integer", value=value)


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-09T09:00:00",
        "2026-09-09 09:00:00Z",
        "2026-02-30T09:00:00Z",
        "2026-09-09T09:00:00+0900",
        "2026-09-09T09:00:00z",
    ],
)
def test_timestamp_requires_valid_offset_aware_rfc3339(value: str) -> None:
    with pytest.raises(RuntimeFactsError):
        fact(key="observed.at", value_kind="timestamp", value=value)


@pytest.mark.parametrize(
    "value",
    ["", "A" * 64, "sha256:" + _SHA_A, "a" * 63, "g" * 64, "a" * 65],
)
def test_source_sha256_requires_lowercase_hex_digest(value: str) -> None:
    with pytest.raises(RuntimeFactsError):
        provenance(source_sha256=value)


@pytest.mark.parametrize(
    "value",
    [
        "/tmp/state",
        "../state",
        "event/4",
        "https://example.test/state",
        "event:../4",
        "",
    ],
)
def test_source_ref_is_logical_and_rejects_paths_and_uri_like_values(
    value: str,
) -> None:
    with pytest.raises(RuntimeFactsError):
        provenance(source_ref=value)


@pytest.mark.parametrize("field", ["workflow_id", "source_ref", "source_sha256"])
def test_control_characters_are_rejected_in_provenance(field: str) -> None:
    values = {
        "workflow_id": "workflow\n1",
        "source_ref": "event:\t4",
        "source_sha256": _SHA_A[:-1] + "\x00",
    }
    with pytest.raises(RuntimeFactsError):
        provenance(**{field: values[field]})


@pytest.mark.parametrize("value", [{"secret": "value"}, ["value"], b"value", object()])
def test_container_bytes_and_opaque_values_cannot_enter_typed_facts(
    value: object,
) -> None:
    with pytest.raises(RuntimeFactsError):
        fact(key="unsafe.value", value_kind="enum", value=value)


def test_provenance_and_fact_are_frozen_and_reject_subclasses() -> None:
    source = provenance()
    value = fact(source=source)

    with pytest.raises(FrozenInstanceError):
        source.source_ref = "state"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        value.value = "succeeded"  # type: ignore[misc]
    with pytest.raises(RuntimeFactsError):
        RuntimeFact("workflow.status", "enum", "succeeded", object())  # type: ignore[arg-type]


def test_snapshot_requires_tuple_and_valid_schema_without_coercion() -> None:
    with pytest.raises(RuntimeFactsError):
        RuntimeFactsSnapshot(facts=[fact()])  # type: ignore[arg-type]
    with pytest.raises(RuntimeFactsError):
        RuntimeFactsSnapshot(schema_version="runtime-facts.v2")  # type: ignore[arg-type]


def test_errors_do_not_echo_supplied_secret_like_values() -> None:
    secret_like = "Authorization-secret-token"
    with pytest.raises(RuntimeFactsError) as error:
        fact(key="unsafe.value", value_kind="enum", value=secret_like)

    assert str(error.value) == "runtime facts are invalid"
    assert secret_like not in str(error.value)


def test_existing_request_task_input_and_fingerprint_are_unchanged() -> None:
    request = ModelInvocationRequest("model", "system", "task", ("search",))
    tool = ToolDefinition(
        "search",
        "search description",
        (ToolParameterDefinition("query", "query description", "string", True),),
    )

    assert build_model_invocation_task_input(request) == "task"
    assert build_model_invocation_execution_fingerprint(request, (tool,)) == (
        "2261827de3a42d02c126f02c9d5c92fc4f8170bf67843217ca257154b20d8e96"
    )
    assert build_model_invocation_execution_fingerprint(
        request, (tool,), DIRECT_OPENAI_EXECUTION_TARGET
    ) == "5165c8b6264ec76cc48a609af8c1b77e40db2cae333c2180f0aa9ceff488a5a1"
    assert tuple(request.__dataclass_fields__) == (
        "model",
        "system_instructions",
        "task_instructions",
        "allowed_tools",
        "upstream_inputs",
        "runtime_facts",
    )


def test_existing_upstream_request_task_input_and_fingerprint_are_unchanged() -> None:
    request = ModelInvocationRequest(
        "model",
        "system",
        "task",
        (),
        (UpstreamStepOutput("workflow", "step-1", 1, "employee-1", "approved"),),
    )

    assert build_model_invocation_task_input(request) == (
        '{"task_instructions":"task","upstream_inputs":[{"employee_id":"employee-1",'
        '"output_text":"approved","step_id":"step-1","step_index":1,'
        '"workflow_id":"workflow"}]}'
    )
    assert build_model_invocation_execution_fingerprint(request, ()) == (
        "fddced9418824839b3e13fb0dc1539f1882bda251f1e7428cf4326d97c5666bf"
    )
    assert build_model_invocation_execution_fingerprint(
        request, (), DIRECT_OPENAI_EXECUTION_TARGET
    ) == "907cc04a3ba99e2eb8402a3011bd2bc32c0eeb87f02e53c10f6f39a6b2821fbe"
    assert request.runtime_facts is EMPTY_RUNTIME_FACTS


def test_nonempty_runtime_facts_task_input_is_exact_canonical_json() -> None:
    snapshot = RuntimeFactsSnapshot(
        facts=(
            fact(
                key="provider.identity",
                value_kind="enum",
                value="omniroute",
                source=provenance(
                    workflow_id="article-workflow",
                    source_ref="event:4",
                    observed_at="2026-09-09T18:00:00+09:00",
                ),
            ),
        )
    )
    request = ModelInvocationRequest(
        "model", "system", "task\n", (), runtime_facts=snapshot
    )

    assert build_model_invocation_task_input(request) == (
        '{"runtime_facts":{"facts":[{"key":"provider.identity",'
        '"provenance":{"observed_at":"2026-09-09T09:00:00Z",'
        '"origin":"persisted_event","source_ref":"event:4",'
        '"source_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaa","workflow_id":"article-workflow"},'
        '"value":"omniroute","value_kind":"enum"}],'
        '"schema_version":"runtime-facts.v1",'
        '"snapshot_sha256":"c2c6ec47cfdb6b067eb6fef96e5823baaa8189c1f2c72956d6ee3e012b49969e"},'
        '"task_instructions":"task\\n"}'
    )
    rendered = build_model_invocation_task_input(request)
    assert rendered.count(runtime_facts_snapshot_digest(snapshot)) == 1
    assert request.system_instructions == "system"


def test_nonempty_runtime_facts_and_upstream_render_as_separate_task_members() -> None:
    snapshot = RuntimeFactsSnapshot(facts=(fact(),))
    upstream = UpstreamStepOutput(
        "workflow", "step-1", 1, "employee-1", "authoritative output"
    )
    request = ModelInvocationRequest(
        "model", "system", "task", (), (upstream,), snapshot
    )

    assert build_model_invocation_task_input(request) == (
        '{"runtime_facts":{"facts":[{"key":"workflow.status",'
        '"provenance":{"observed_at":null,"origin":"persisted_event",'
        '"source_ref":"event:4","source_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","workflow_id":"article-workflow"},'
        '"value":"workflow_complete","value_kind":"enum"}],'
        '"schema_version":"runtime-facts.v1",'
        '"snapshot_sha256":"5ad96dfb1b214b11be3f0587715630cb02e1da7692a1802c81506d6419b78c9c"},'
        '"task_instructions":"task","upstream_inputs":[{"employee_id":"employee-1",'
        '"output_text":"authoritative output","step_id":"step-1",'
        '"step_index":1,"workflow_id":"workflow"}]}'
    )


def test_nonempty_runtime_facts_task_rendering_is_caller_order_independent() -> None:
    first = fact(
        key="workflow.status", value="succeeded", source=provenance(source_ref="state")
    )
    second = fact(
        key="step.completed_count",
        value_kind="integer",
        value=4,
        source=provenance(source_ref="state"),
    )
    left = ModelInvocationRequest(
        "model", "system", "task", (), runtime_facts=RuntimeFactsSnapshot(
            facts=(first, second)
        )
    )
    right = ModelInvocationRequest(
        "model", "system", "task", (), runtime_facts=RuntimeFactsSnapshot(
            facts=(second, first)
        )
    )

    assert build_model_invocation_task_input(left) == build_model_invocation_task_input(
        right
    )


def test_core_uses_no_provider_network_environment_or_clock_input() -> None:
    first = runtime_facts_snapshot_digest(EMPTY_RUNTIME_FACTS)
    second = runtime_facts_snapshot_digest(EMPTY_RUNTIME_FACTS)

    assert first == second
    assert (
        sha256(runtime_facts_snapshot_canonical_bytes(EMPTY_RUNTIME_FACTS)).hexdigest()
        == first
    )
