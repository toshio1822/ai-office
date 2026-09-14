"""Focused provider-free tests for Phase 278 external publication planning."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication as external_module
from ai_office.engine import (
    ExternalPublicationPlan,
    ExternalPublicationPlanError,
    ExternalPublicationTarget,
    ExternalPublicationTargetError,
    PublicationRegenerationExportReconciliation,
    build_external_publication_plan,
    external_publication_plan_canonical_bytes,
    external_publication_plan_digest,
    external_publication_target_canonical_bytes,
    external_publication_target_digest,
    persist_publication_regeneration_export_reconciliation,
    publication_regeneration_export_reconciliation_digest,
    serialize_external_publication_plan_canonical,
    serialize_external_publication_target_canonical,
    validate_external_publication_plan,
)

_TARGET_SCHEMA = "external-publication-target.v1"
_PLAN_SCHEMA = "external-publication-plan.v1"
_RECONCILIATION_SCHEMA = "publication-regeneration-export-reconciliation.v1"
_RECEIPT_DIGEST = "b" * 64
_EXPECTED_DIGEST = "a" * 64
_OBSERVED_DIGEST = "c" * 64


def _matched(
    *,
    regeneration_id: str = "regen-278-unicode",
) -> PublicationRegenerationExportReconciliation:
    return PublicationRegenerationExportReconciliation(
        schema_version=_RECONCILIATION_SCHEMA,
        regeneration_id=regeneration_id,
        receipt_sha256=_RECEIPT_DIGEST,
        status="matched",
        expected_business_output_sha256=_EXPECTED_DIGEST,
        expected_output_byte_length=17,
        observed_business_output_sha256=_EXPECTED_DIGEST,
        observed_output_byte_length=17,
    )


def _reconciliation(status: str) -> PublicationRegenerationExportReconciliation:
    if status == "matched":
        return _matched()
    if status == "missing":
        observed_digest: str | None = None
        observed_length: int | None = None
    else:
        observed_digest = _OBSERVED_DIGEST
        observed_length = 18
    return PublicationRegenerationExportReconciliation(
        schema_version=_RECONCILIATION_SCHEMA,
        regeneration_id=f"regen-278-{status}",
        receipt_sha256=_RECEIPT_DIGEST,
        status=status,  # type: ignore[arg-type]
        expected_business_output_sha256=_EXPECTED_DIGEST,
        expected_output_byte_length=17,
        observed_business_output_sha256=observed_digest,
        observed_output_byte_length=observed_length,
    )


def _target(
    *,
    provider: str = "future-provider",
    destination_id: str = "公開先/primary",
) -> ExternalPublicationTarget:
    return ExternalPublicationTarget(
        schema_version=_TARGET_SCHEMA,
        provider=provider,
        destination_id=destination_id,
    )


def _plan(
    *,
    regeneration_id: str = "regen-278-unicode",
    reconciliation_evidence_sha256: str = "d" * 64,
    receipt_sha256: str = _RECEIPT_DIGEST,
    business_output_sha256: str = _EXPECTED_DIGEST,
    output_byte_length: int = 17,
    provider: str = "future-provider",
    publication_target_sha256: str = "e" * 64,
) -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version=_PLAN_SCHEMA,
        regeneration_id=regeneration_id,
        reconciliation_evidence_sha256=reconciliation_evidence_sha256,
        receipt_sha256=receipt_sha256,
        business_output_sha256=business_output_sha256,
        output_byte_length=output_byte_length,
        provider=provider,
        publication_target_sha256=publication_target_sha256,
    )


def _persisted_evidence(
    tmp_path: Path,
    *,
    status: str = "matched",
) -> Path:
    path = tmp_path / f"{status}.json"
    persist_publication_regeneration_export_reconciliation(
        path, _reconciliation(status)
    )
    return path


def _assert_target_error(error: ValueError, classification: str) -> None:
    assert isinstance(error, ExternalPublicationTargetError)
    assert str(error) == "external publication target is invalid"
    assert error.detail.classification == classification


def _assert_plan_error(error: ValueError, classification: str) -> None:
    assert isinstance(error, ExternalPublicationPlanError)
    assert str(error) == "external publication plan is invalid"
    assert error.detail.classification == classification


def test_valid_target_is_immutable_and_exact() -> None:
    target = _target()

    assert type(target) is ExternalPublicationTarget
    assert target.schema_version == _TARGET_SCHEMA
    assert target.provider == "future-provider"
    assert target.destination_id == "公開先/primary"
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.provider = "other"  # type: ignore[misc]


def test_target_canonical_json_is_exact_unicode_utf8_and_sha256() -> None:
    target = _target()
    expected = (
        '{"destination_id":"公開先/primary","provider":"future-provider",'
        '"schema_version":"external-publication-target.v1"}'
    )

    assert serialize_external_publication_target_canonical(target) == expected
    expected_bytes = expected.encode("utf-8")
    assert external_publication_target_canonical_bytes(target) == expected_bytes
    assert external_publication_target_digest(target) == hashlib.sha256(
        expected_bytes
    ).hexdigest()
    assert b"\\u516c" not in expected_bytes
    assert not expected_bytes.endswith(b"\n")


@pytest.mark.parametrize(
    "provider",
    [
        "",
        "Future-provider",
        "future_provider",
        "future.provider",
        "-future",
        "future-",
        "future--provider",
        "a" * 129,
        1,
        True,
    ],
)
def test_target_provider_is_explicit_lowercase_slug_without_coercion(
    provider: object,
) -> None:
    with pytest.raises(ExternalPublicationTargetError) as raised:
        ExternalPublicationTarget(
            schema_version=_TARGET_SCHEMA,
            provider=provider,  # type: ignore[arg-type]
            destination_id="destination",
        )

    _assert_target_error(raised.value, "target_metadata")


@pytest.mark.parametrize(
    "destination_id",
    [
        "",
        " ",
        " destination",
        "destination ",
        "destination\nsecret",
        "destination\x00secret",
        "destination\x7fsecret",
        "destination\ud800",
        "d" * 257,
        1,
        True,
    ],
)
def test_target_destination_is_trimmed_bounded_and_control_free_without_coercion(
    destination_id: object,
) -> None:
    with pytest.raises(ExternalPublicationTargetError) as raised:
        ExternalPublicationTarget(
            schema_version=_TARGET_SCHEMA,
            provider="future-provider",
            destination_id=destination_id,  # type: ignore[arg-type]
        )

    _assert_target_error(raised.value, "target_metadata")


def test_target_requires_exact_type_at_all_public_validation_boundaries() -> None:
    class TargetSubclass(ExternalPublicationTarget):
        pass

    forged_subclass = object.__new__(TargetSubclass)
    object.__setattr__(forged_subclass, "schema_version", _TARGET_SCHEMA)
    object.__setattr__(forged_subclass, "provider", "future-provider")
    object.__setattr__(forged_subclass, "destination_id", "destination")
    substitute = SimpleNamespace(
        schema_version=_TARGET_SCHEMA,
        provider="future-provider",
        destination_id="destination",
    )

    for value in (forged_subclass, substitute):
        with pytest.raises(ExternalPublicationTargetError) as raised:
            serialize_external_publication_target_canonical(value)  # type: ignore[arg-type]
        _assert_target_error(raised.value, "target_type")

        with pytest.raises(ExternalPublicationTargetError) as raised:
            external_publication_target_digest(value)  # type: ignore[arg-type]
        _assert_target_error(raised.value, "target_type")


def test_target_schema_version_is_exact() -> None:
    with pytest.raises(ExternalPublicationTargetError) as raised:
        ExternalPublicationTarget(
            schema_version="external-publication-target.v2",  # type: ignore[arg-type]
            provider="future-provider",
            destination_id="destination",
        )

    _assert_target_error(raised.value, "target_metadata")


def test_plan_invariants_require_exact_type_schema_lineage_digests_and_length() -> None:
    valid = _plan()
    assert type(valid) is ExternalPublicationPlan

    invalid_values = [
        {"schema_version": "external-publication-plan.v2"},
        {"regeneration_id": ""},
        {"regeneration_id": "Regen-278"},
        {"reconciliation_evidence_sha256": "D" * 64},
        {"receipt_sha256": "not-a-digest"},
        {"business_output_sha256": "g" * 64},
        {"output_byte_length": True},
        {"output_byte_length": -1},
        {"provider": "Future-provider"},
        {"provider": "future_provider"},
        {"publication_target_sha256": "z" * 64},
    ]
    for replacement in invalid_values:
        with pytest.raises(ExternalPublicationPlanError) as raised:
            ExternalPublicationPlan(
                **{**dataclasses.asdict(valid), **replacement}
            )
        _assert_plan_error(raised.value, "plan_metadata")

    class PlanSubclass(ExternalPublicationPlan):
        pass

    forged_subclass = object.__new__(PlanSubclass)
    for field in dataclasses.fields(valid):
        object.__setattr__(forged_subclass, field.name, getattr(valid, field.name))
    substitute = SimpleNamespace(**dataclasses.asdict(valid))
    for value in (forged_subclass, substitute):
        with pytest.raises(ExternalPublicationPlanError) as raised:
            serialize_external_publication_plan_canonical(value)  # type: ignore[arg-type]
        _assert_plan_error(raised.value, "plan_type")


def test_plan_canonical_json_has_exact_eight_keys_and_sha256() -> None:
    plan = _plan()
    expected = json.dumps(
        {
            "business_output_sha256": _EXPECTED_DIGEST,
            "output_byte_length": 17,
            "provider": "future-provider",
            "publication_target_sha256": "e" * 64,
            "receipt_sha256": _RECEIPT_DIGEST,
            "reconciliation_evidence_sha256": "d" * 64,
            "regeneration_id": "regen-278-unicode",
            "schema_version": _PLAN_SCHEMA,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    expected_bytes = expected.encode("utf-8")

    assert serialize_external_publication_plan_canonical(plan) == expected
    assert external_publication_plan_canonical_bytes(plan) == expected_bytes
    assert external_publication_plan_digest(plan) == hashlib.sha256(
        expected_bytes
    ).hexdigest()
    assert set(json.loads(expected)) == {
        "schema_version",
        "regeneration_id",
        "reconciliation_evidence_sha256",
        "receipt_sha256",
        "business_output_sha256",
        "output_byte_length",
        "provider",
        "publication_target_sha256",
    }
    assert [field.name for field in dataclasses.fields(plan)] == [
        "schema_version",
        "regeneration_id",
        "reconciliation_evidence_sha256",
        "receipt_sha256",
        "business_output_sha256",
        "output_byte_length",
        "provider",
        "publication_target_sha256",
    ]
    assert "公開先/primary" not in expected


def test_builder_uses_loader_once_and_digest_once_with_exact_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = tmp_path / "caller" / ".." / "explicit-evidence.json"
    loaded = _matched()
    expected_evidence_digest = publication_regeneration_export_reconciliation_digest(
        loaded
    )
    loaded_paths: list[object] = []
    digested_values: list[object] = []

    def loader(path: object) -> PublicationRegenerationExportReconciliation:
        loaded_paths.append(path)
        return loaded

    def digest(value: object) -> str:
        digested_values.append(value)
        return expected_evidence_digest

    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        loader,
    )
    monkeypatch.setattr(
        external_module,
        "publication_regeneration_export_reconciliation_digest",
        digest,
    )

    target = _target()
    plan = build_external_publication_plan(
        reconciliation_evidence_path=evidence_path,
        target=target,
    )

    assert loaded_paths == [evidence_path]
    assert loaded_paths[0] is evidence_path
    assert digested_values == [loaded]
    assert digested_values[0] is loaded
    assert plan.regeneration_id == loaded.regeneration_id
    assert plan.reconciliation_evidence_sha256 == expected_evidence_digest
    assert plan.receipt_sha256 == loaded.receipt_sha256
    assert plan.business_output_sha256 == loaded.expected_business_output_sha256
    assert plan.output_byte_length == loaded.expected_output_byte_length
    assert plan.provider == target.provider
    assert plan.publication_target_sha256 == external_publication_target_digest(
        target
    )


def test_builder_valid_matched_evidence_produces_only_in_memory_plan(
    tmp_path: Path,
) -> None:
    evidence_path = _persisted_evidence(tmp_path)
    before = sorted(path.name for path in tmp_path.iterdir())

    plan = build_external_publication_plan(
        reconciliation_evidence_path=evidence_path,
        target=_target(),
    )

    after = sorted(path.name for path in tmp_path.iterdir())
    assert before == after
    assert plan == ExternalPublicationPlan(
        schema_version=_PLAN_SCHEMA,
        regeneration_id="regen-278-unicode",
        reconciliation_evidence_sha256=(
            publication_regeneration_export_reconciliation_digest(_matched())
        ),
        receipt_sha256=_RECEIPT_DIGEST,
        business_output_sha256=_EXPECTED_DIGEST,
        output_byte_length=17,
        provider="future-provider",
        publication_target_sha256=external_publication_target_digest(_target()),
    )


def test_builder_does_not_read_output_or_receipt_or_mutate_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = tmp_path / "explicit-evidence.json"
    loaded = _matched()

    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        lambda path: loaded,
    )
    monkeypatch.setattr(
        external_module,
        "publication_regeneration_export_reconciliation_digest",
        lambda value: publication_regeneration_export_reconciliation_digest(loaded),
    )

    def forbidden_read(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("Phase 278 must not read an output or receipt file")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    before = sorted(path.name for path in tmp_path.iterdir())

    build_external_publication_plan(
        reconciliation_evidence_path=evidence_path,
        target=_target(),
    )

    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_module_has_no_external_execution_or_predecessor_observation_boundary() -> None:
    forbidden_names = {
        "export_publication_regeneration_output",
        "project_publication_regeneration_output",
        "reconcile_publication_regeneration_export",
        "load_publication_regeneration_export_receipt",
        "publication_regeneration_export_receipt_digest",
    }

    assert forbidden_names.isdisjoint(external_module.__dict__)


@pytest.mark.parametrize("status", ["missing", "content_mismatch"])
def test_non_matched_evidence_fails_closed_before_digest(
    tmp_path: Path,
    status: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = _persisted_evidence(tmp_path, status=status)
    digest_calls = 0

    def digest(value: object) -> str:
        nonlocal digest_calls
        digest_calls += 1
        return "d" * 64

    monkeypatch.setattr(
        external_module,
        "publication_regeneration_export_reconciliation_digest",
        digest,
    )

    with pytest.raises(ExternalPublicationPlanError) as raised:
        build_external_publication_plan(
            reconciliation_evidence_path=evidence_path,
            target=_target(),
        )

    _assert_plan_error(raised.value, "non_matched_evidence")
    assert digest_calls == 0


def test_loader_return_type_is_exact_and_digest_is_not_called(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = tmp_path / "evidence.json"
    digest_calls = 0

    def digest(value: object) -> str:
        nonlocal digest_calls
        digest_calls += 1
        return "d" * 64

    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        lambda path: SimpleNamespace(status="matched"),
    )
    monkeypatch.setattr(
        external_module,
        "publication_regeneration_export_reconciliation_digest",
        digest,
    )

    with pytest.raises(ExternalPublicationPlanError) as raised:
        build_external_publication_plan(
            reconciliation_evidence_path=evidence_path,
            target=_target(),
        )

    _assert_plan_error(raised.value, "evidence_loading")
    assert digest_calls == 0


def test_loader_errors_are_fixed_and_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = tmp_path / "secret-evidence-path.json"
    calls = 0

    def loader(path: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError(f"{path} private evidence {'a' * 64}")

    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        loader,
    )

    with pytest.raises(ExternalPublicationPlanError) as raised:
        build_external_publication_plan(
            reconciliation_evidence_path=evidence_path,
            target=_target(destination_id="secret-destination"),
        )

    _assert_plan_error(raised.value, "evidence_loading")
    assert calls == 1
    assert str(evidence_path) not in str(raised.value)
    assert "private evidence" not in str(raised.value)
    assert "secret-destination" not in str(raised.value)


def test_digest_is_called_once_and_invalid_digest_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = tmp_path / "explicit-evidence.json"
    loaded = _matched()
    calls: list[object] = []

    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        lambda path: loaded,
    )

    def digest(value: object) -> str:
        calls.append(value)
        return "not-a-digest"

    monkeypatch.setattr(
        external_module,
        "publication_regeneration_export_reconciliation_digest",
        digest,
    )

    with pytest.raises(ExternalPublicationPlanError) as raised:
        build_external_publication_plan(
            reconciliation_evidence_path=evidence_path,
            target=_target(),
        )

    _assert_plan_error(raised.value, "digest")
    assert calls == [loaded]
    assert calls[0] is loaded


def test_invalid_target_is_rejected_after_evidence_digest_without_target_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = _persisted_evidence(tmp_path)
    valid_evidence_digest = publication_regeneration_export_reconciliation_digest(
        _matched()
    )
    digest_calls: list[object] = []
    target_digest_calls = 0

    def evidence_digest(value: object) -> str:
        digest_calls.append(value)
        return valid_evidence_digest

    def target_digest(value: object) -> str:
        nonlocal target_digest_calls
        target_digest_calls += 1
        return "e" * 64

    monkeypatch.setattr(
        external_module,
        "publication_regeneration_export_reconciliation_digest",
        evidence_digest,
    )
    monkeypatch.setattr(
        external_module,
        "external_publication_target_digest",
        target_digest,
    )
    invalid_target = SimpleNamespace(
        schema_version=_TARGET_SCHEMA,
        provider="future-provider",
        destination_id="destination",
    )

    with pytest.raises(ExternalPublicationTargetError) as raised:
        build_external_publication_plan(
            reconciliation_evidence_path=evidence_path,
            target=invalid_target,  # type: ignore[arg-type]
        )

    _assert_target_error(raised.value, "target_type")
    assert len(digest_calls) == 1
    assert target_digest_calls == 0


def test_target_subclass_and_attribute_substitute_are_rejected_by_builder(
    tmp_path: Path,
) -> None:
    evidence_path = _persisted_evidence(tmp_path)

    class TargetSubclass(ExternalPublicationTarget):
        pass

    forged_subclass = object.__new__(TargetSubclass)
    object.__setattr__(forged_subclass, "schema_version", _TARGET_SCHEMA)
    object.__setattr__(forged_subclass, "provider", "future-provider")
    object.__setattr__(forged_subclass, "destination_id", "destination")
    substitute = SimpleNamespace(
        schema_version=_TARGET_SCHEMA,
        provider="future-provider",
        destination_id="destination",
    )

    for target in (forged_subclass, substitute):
        with pytest.raises(ExternalPublicationTargetError) as raised:
            build_external_publication_plan(
                reconciliation_evidence_path=evidence_path,
                target=target,  # type: ignore[arg-type]
            )
        _assert_target_error(raised.value, "target_type")


def test_exact_evidence_path_type_is_required_before_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def loader(path: object) -> object:
        nonlocal calls
        calls += 1
        return _matched()

    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        loader,
    )

    with pytest.raises(ExternalPublicationPlanError) as raised:
        build_external_publication_plan(
            reconciliation_evidence_path=str(tmp_path / "evidence.json"),  # type: ignore[arg-type]
            target=_target(),
        )

    _assert_plan_error(raised.value, "evidence_loading")
    assert calls == 0


def test_validator_rederives_from_fresh_explicit_inputs_and_calls_dependencies_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = _persisted_evidence(tmp_path)
    target = _target()
    plan = build_external_publication_plan(
        reconciliation_evidence_path=evidence_path,
        target=target,
    )
    loaded_paths: list[object] = []
    digested_values: list[object] = []
    loaded = _matched()
    evidence_digest = publication_regeneration_export_reconciliation_digest(loaded)

    def loader(path: object) -> PublicationRegenerationExportReconciliation:
        loaded_paths.append(path)
        return loaded

    def digest(value: object) -> str:
        digested_values.append(value)
        return evidence_digest

    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        loader,
    )
    monkeypatch.setattr(
        external_module,
        "publication_regeneration_export_reconciliation_digest",
        digest,
    )

    assert validate_external_publication_plan(
        plan,
        reconciliation_evidence_path=evidence_path,
        target=target,
    ) is None
    assert loaded_paths == [evidence_path]
    assert loaded_paths[0] is evidence_path
    assert digested_values == [loaded]
    assert digested_values[0] is loaded


def test_validator_rejects_plan_mismatch_without_exposing_identity_values(
    tmp_path: Path,
) -> None:
    evidence_path = _persisted_evidence(tmp_path)
    target = _target(destination_id="private-destination")
    plan = build_external_publication_plan(
        reconciliation_evidence_path=evidence_path,
        target=target,
    )
    tampered = dataclasses.replace(
        plan,
        publication_target_sha256="f" * 64,
    )

    with pytest.raises(ExternalPublicationPlanError) as raised:
        validate_external_publication_plan(
            tampered,
            reconciliation_evidence_path=evidence_path,
            target=target,
        )

    _assert_plan_error(raised.value, "plan_validation")
    assert str(evidence_path) not in str(raised.value)
    assert "private-destination" not in str(raised.value)
    assert "f" * 64 not in str(raised.value)


def test_validator_requires_exact_plan_type_and_valid_invariants(
    tmp_path: Path,
) -> None:
    evidence_path = _persisted_evidence(tmp_path)
    target = _target()
    valid = build_external_publication_plan(
        reconciliation_evidence_path=evidence_path,
        target=target,
    )
    substitute = SimpleNamespace(**dataclasses.asdict(valid))

    with pytest.raises(ExternalPublicationPlanError) as raised:
        validate_external_publication_plan(
            substitute,  # type: ignore[arg-type]
            reconciliation_evidence_path=evidence_path,
            target=target,
        )

    _assert_plan_error(raised.value, "plan_type")


def test_validator_rejects_fresh_input_rebinding() -> None:
    first = _matched(regeneration_id="regen-278-first")
    second = _matched(regeneration_id="regen-278-second")
    first_path = Path("first-evidence.json")
    second_path = Path("second-evidence.json")
    loaded_paths: list[object] = []

    def loader(path: object) -> PublicationRegenerationExportReconciliation:
        loaded_paths.append(path)
        return first if path is first_path else second

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        loader,
    )
    try:
        target = _target()
        plan = build_external_publication_plan(
            reconciliation_evidence_path=first_path,
            target=target,
        )
        with pytest.raises(ExternalPublicationPlanError) as raised:
            validate_external_publication_plan(
                plan,
                reconciliation_evidence_path=second_path,
                target=target,
            )
        _assert_plan_error(raised.value, "plan_validation")
        assert loaded_paths == [first_path, second_path]
        assert loaded_paths[0] is first_path
        assert loaded_paths[1] is second_path
    finally:
        monkeypatch.undo()


def test_error_messages_are_fixed_and_detail_safe() -> None:
    with pytest.raises(ExternalPublicationTargetError) as target_error:
        ExternalPublicationTarget(
            schema_version=_TARGET_SCHEMA,
            provider="provider-secret",
            destination_id="destination-secret\n",
        )
    _assert_target_error(target_error.value, "target_metadata")

    with pytest.raises(ExternalPublicationPlanError) as plan_error:
        ExternalPublicationPlan(
            schema_version=_PLAN_SCHEMA,
            regeneration_id="regen-278",
            reconciliation_evidence_sha256="a" * 64,
            receipt_sha256="b" * 64,
            business_output_sha256="c" * 64,
            output_byte_length=1,
            provider="future-provider",
            publication_target_sha256="not-a-digest",
        )
    _assert_plan_error(plan_error.value, "plan_metadata")
    assert "provider-secret" not in str(target_error.value)
    assert "destination-secret" not in str(target_error.value)
    assert "not-a-digest" not in str(plan_error.value)


def test_public_engine_exports_are_available() -> None:
    assert callable(build_external_publication_plan)
    assert callable(validate_external_publication_plan)
    assert callable(serialize_external_publication_target_canonical)
    assert callable(external_publication_target_canonical_bytes)
    assert callable(external_publication_target_digest)
    assert callable(serialize_external_publication_plan_canonical)
    assert callable(external_publication_plan_canonical_bytes)
    assert callable(external_publication_plan_digest)
