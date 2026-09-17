"""Focused provider-free tests for the Phase 289 durable operation intent."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import json
import os
import random
import socket
import subprocess
import time
import uuid
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication as publication_module
import ai_office.engine.external_publication_operation as operation_module
import ai_office.engine.external_publication_operation_intent as intent_module
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationApprovalError,
    ExternalPublicationError,
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentConflictError,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationIntentFailureDetail,
    ExternalPublicationOperationIntentLoadError,
    ExternalPublicationOperationIntentPersistenceError,
    ExternalPublicationPlan,
    approve_external_publication,
    build_external_publication_operation_intent,
    external_publication_operation_intent_canonical_bytes,
    external_publication_operation_intent_digest,
    load_external_publication_operation_intent,
    persist_external_publication_operation_intent,
    serialize_external_publication_operation_intent_canonical,
)

_SCHEMA = "external-publication-operation-intent.v1"
_PLAN_DIGEST = "b" * 64
_APPROVAL_DIGEST = "c" * 64


class StringChild(str):
    pass


def _approval() -> ExternalPublicationApproval:
    return ExternalPublicationApproval(
        approved=True,
        publication_plan_sha256=_PLAN_DIGEST,
        approved_by="human-reviewer-289",
        approval_id="approval-289",
    )


def _intent(
    operation: str = "fresh",
    *,
    approval_digest: str = _APPROVAL_DIGEST,
    plan_digest: str = _PLAN_DIGEST,
) -> ExternalPublicationOperationIntent:
    return ExternalPublicationOperationIntent(
        schema_version=_SCHEMA,
        publication_approval_sha256=approval_digest,
        publication_plan_sha256=plan_digest,
        operation=operation,  # type: ignore[arg-type]
    )


def _real_approval() -> ExternalPublicationApproval:
    plan = ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="regen-289-integration",
        reconciliation_evidence_sha256="d" * 64,
        receipt_sha256="e" * 64,
        business_output_sha256="f" * 64,
        output_byte_length=17,
        provider="future-provider",
        publication_target_sha256="1" * 64,
    )
    return approve_external_publication(
        plan,
        approved_by="human-reviewer-289-integration",
        approval_id="approval-289-integration",
    )


def _forged_instance(cls: type[object], source: object) -> object:
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    return value


def _assert_intent_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationOperationIntentError
    assert isinstance(error, ValueError)
    assert str(error) == "external publication operation intent is invalid"
    assert type(error.detail) is ExternalPublicationOperationIntentFailureDetail
    assert error.detail.classification == classification


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationOperationIntentPersistenceError
    assert str(error) == ("external publication operation intent persistence failed")
    assert type(error.detail) is ExternalPublicationOperationIntentFailureDetail
    assert error.detail.classification == classification


def _assert_conflict_error(error: ValueError) -> None:
    assert type(error) is ExternalPublicationOperationIntentConflictError
    assert str(error) == ("external publication operation intent persistence failed")
    assert type(error.detail) is ExternalPublicationOperationIntentFailureDetail
    assert error.detail.classification == "conflict"


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationOperationIntentLoadError
    assert str(error) == ("external publication operation intent could not be loaded")
    assert type(error.detail) is ExternalPublicationOperationIntentFailureDetail
    assert error.detail.classification == classification


def _canonical_mapping(
    intent: ExternalPublicationOperationIntent,
) -> dict[str, object]:
    return {
        "operation": intent.operation,
        "publication_approval_sha256": intent.publication_approval_sha256,
        "publication_plan_sha256": intent.publication_plan_sha256,
        "schema_version": intent.schema_version,
    }


def _canonical_bytes(intent: ExternalPublicationOperationIntent) -> bytes:
    return json.dumps(
        _canonical_mapping(intent),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def test_public_model_is_exact_frozen_dataclass_and_exported() -> None:
    assert type(_intent()) is ExternalPublicationOperationIntent
    assert dataclasses.is_dataclass(ExternalPublicationOperationIntent)
    assert ExternalPublicationOperationIntent.__dataclass_params__.frozen
    assert tuple(
        field.name for field in fields(ExternalPublicationOperationIntent)
    ) == (
        "schema_version",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "operation",
    )
    assert intent_module.ExternalPublicationOperationIntent is (
        ExternalPublicationOperationIntent
    )
    assert "ExternalPublicationOperationIntent" in intent_module.__all__
    with pytest.raises(FrozenInstanceError):
        _intent().operation = "resume"  # type: ignore[misc]


@pytest.mark.parametrize("operation", ["fresh", "resume"])
def test_valid_model_construction_preserves_exact_fields(operation: str) -> None:
    intent = _intent(operation)

    assert intent.schema_version == _SCHEMA
    assert intent.publication_approval_sha256 == _APPROVAL_DIGEST
    assert intent.publication_plan_sha256 == _PLAN_DIGEST
    assert intent.operation == operation
    assert type(intent.operation) is str


@pytest.mark.parametrize(
    "field_name,value,classification",
    [
        (
            "schema_version",
            "external-publication-operation-intent.v2",
            "schema_version",
        ),
        ("publication_approval_sha256", "A" * 64, "approval_digest"),
        ("publication_approval_sha256", "a" * 63, "approval_digest"),
        ("publication_approval_sha256", StringChild("a" * 64), "approval_digest"),
        ("publication_plan_sha256", "A" * 64, "plan_digest"),
        ("publication_plan_sha256", "b" * 63, "plan_digest"),
        ("publication_plan_sha256", StringChild("b" * 64), "plan_digest"),
        ("operation", "FRESH", "operation"),
        ("operation", StringChild("fresh"), "operation"),
        ("operation", 1, "operation"),
    ],
)
def test_forged_malformed_model_is_rejected_by_all_canonical_boundaries(
    field_name: str,
    value: object,
    classification: str,
) -> None:
    forged = _forged_instance(ExternalPublicationOperationIntent, _intent())
    object.__setattr__(forged, field_name, value)

    with pytest.raises(ExternalPublicationOperationIntentError) as raised:
        serialize_external_publication_operation_intent_canonical(forged)  # type: ignore[arg-type]
    _assert_intent_error(raised.value, classification)
    with pytest.raises(ExternalPublicationOperationIntentError) as raised:
        external_publication_operation_intent_canonical_bytes(forged)  # type: ignore[arg-type]
    _assert_intent_error(raised.value, classification)
    with pytest.raises(ExternalPublicationOperationIntentError) as raised:
        external_publication_operation_intent_digest(forged)  # type: ignore[arg-type]
    _assert_intent_error(raised.value, classification)


@pytest.mark.parametrize("candidate", [None, SimpleNamespace(), {}, "intent"])
def test_canonical_boundaries_require_exact_model_type(candidate: object) -> None:
    for helper in (
        serialize_external_publication_operation_intent_canonical,
        external_publication_operation_intent_canonical_bytes,
        external_publication_operation_intent_digest,
    ):
        with pytest.raises(ExternalPublicationOperationIntentError) as raised:
            helper(candidate)  # type: ignore[arg-type]
        _assert_intent_error(raised.value, "intent_type")


def test_model_subclass_and_attribute_lookalike_are_rejected() -> None:
    valid = _intent()

    class IntentSubclass(ExternalPublicationOperationIntent):
        pass

    subclass = _forged_instance(IntentSubclass, valid)
    lookalike = SimpleNamespace(**dataclasses.asdict(valid))
    for candidate in (subclass, lookalike):
        with pytest.raises(ExternalPublicationOperationIntentError) as raised:
            serialize_external_publication_operation_intent_canonical(candidate)  # type: ignore[arg-type]
        _assert_intent_error(raised.value, "intent_type")


def test_builder_signature_and_default_authoritative_helper_are_exact() -> None:
    signature = inspect.signature(build_external_publication_operation_intent)
    parameters = list(signature.parameters.values())
    assert [parameter.name for parameter in parameters] == [
        "approval",
        "operation",
        "approval_digest_function",
    ]
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters[1].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[2].kind is inspect.Parameter.KEYWORD_ONLY
    assert (
        parameters[2].default is publication_module.external_publication_approval_digest
    )
    assert (
        intent_module.build_external_publication_operation_intent
        is build_external_publication_operation_intent
    )


def test_builder_calls_injected_approval_digest_once_with_exact_identity() -> None:
    approval = _approval()
    expected_digest = "a" * 64
    calls: list[object] = []

    def digest(value: object) -> str:
        calls.append(value)
        return expected_digest

    intent = build_external_publication_operation_intent(
        approval,
        operation="fresh",
        approval_digest_function=digest,
    )

    assert type(intent) is ExternalPublicationOperationIntent
    assert calls == [approval]
    assert calls[0] is approval
    assert intent.publication_approval_sha256 == expected_digest
    assert intent.publication_plan_sha256 == approval.publication_plan_sha256
    assert intent.operation == "fresh"


def test_builder_copies_plan_digest_from_exact_approval_without_recomputation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _approval()
    calls: list[object] = []

    def digest(value: object) -> str:
        calls.append(value)
        return _APPROVAL_DIGEST

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Phase 289 must not rebuild or load a publication plan")

    monkeypatch.setattr(
        intent_module, "build_external_publication_plan", forbidden, raising=False
    )
    monkeypatch.setattr(
        intent_module,
        "load_external_publication_plan",
        forbidden,
        raising=False,
    )
    result = build_external_publication_operation_intent(
        approval,
        operation="resume",
        approval_digest_function=digest,
    )

    assert result.publication_plan_sha256 is approval.publication_plan_sha256
    assert calls == [approval]


def test_builder_known_approval_error_propagates_by_exact_identity() -> None:
    approval = _approval()
    sentinel = ExternalPublicationApprovalError("approval_metadata")

    def digest(_value: object) -> str:
        raise sentinel

    with pytest.raises(ExternalPublicationApprovalError) as raised:
        build_external_publication_operation_intent(
            approval,
            operation="fresh",
            approval_digest_function=digest,
        )
    assert raised.value is sentinel


def test_builder_known_external_publication_error_propagates_by_exact_identity() -> (
    None
):
    approval = _approval()
    sentinel = ExternalPublicationError("authoritative")

    def digest(_value: object) -> str:
        raise sentinel

    with pytest.raises(ExternalPublicationError) as raised:
        build_external_publication_operation_intent(
            approval,
            operation="resume",
            approval_digest_function=digest,
        )
    assert raised.value is sentinel


def test_builder_unexpected_dependency_error_is_fixed_and_detail_safe() -> None:
    approval = _approval()

    def digest(_value: object) -> str:
        raise RuntimeError("private provider response")

    with pytest.raises(ExternalPublicationOperationIntentError) as raised:
        build_external_publication_operation_intent(
            approval,
            operation="fresh",
            approval_digest_function=digest,
        )
    _assert_intent_error(raised.value, "dependency_error")
    assert "private provider response" not in str(raised.value)


@pytest.mark.parametrize("operation", ["FRESH", StringChild("fresh"), 1, None])
def test_builder_requires_exact_explicit_operation_before_helper_call(
    operation: object,
) -> None:
    calls = 0

    def digest(_value: object) -> str:
        nonlocal calls
        calls += 1
        return _APPROVAL_DIGEST

    with pytest.raises(ExternalPublicationOperationIntentError) as raised:
        build_external_publication_operation_intent(
            _approval(),
            operation=operation,  # type: ignore[arg-type]
            approval_digest_function=digest,
        )
    _assert_intent_error(raised.value, "operation")
    assert calls == 0


def test_builder_requires_exact_approval_type_before_helper_call() -> None:
    calls = 0

    def digest(_value: object) -> str:
        nonlocal calls
        calls += 1
        return _APPROVAL_DIGEST

    with pytest.raises(ExternalPublicationOperationIntentError) as raised:
        build_external_publication_operation_intent(
            SimpleNamespace(),
            operation="fresh",
            approval_digest_function=digest,
        )  # type: ignore[arg-type]
    _assert_intent_error(raised.value, "approval_type")
    assert calls == 0


def test_builder_requires_callable_helper_before_invocation() -> None:
    with pytest.raises(ExternalPublicationOperationIntentError) as raised:
        build_external_publication_operation_intent(
            _approval(),
            operation="fresh",
            approval_digest_function=object(),  # type: ignore[arg-type]
        )
    _assert_intent_error(raised.value, "configuration")


@pytest.mark.parametrize("value", ["not-a-digest", "A" * 64, StringChild("a" * 64), 1])
def test_builder_rejects_malformed_helper_digest_without_retry(value: object) -> None:
    calls = 0

    def digest(_value: object) -> object:
        nonlocal calls
        calls += 1
        return value

    with pytest.raises(ExternalPublicationOperationIntentError) as raised:
        build_external_publication_operation_intent(
            _approval(),
            operation="fresh",
            approval_digest_function=digest,
        )
    _assert_intent_error(raised.value, "approval_digest")
    assert calls == 1


def test_builder_default_helper_revalidates_exact_approval_contract() -> None:
    valid = _approval()
    forged = _forged_instance(ExternalPublicationApproval, valid)
    object.__setattr__(forged, "approved_by", " invalid")

    with pytest.raises(ExternalPublicationApprovalError) as raised:
        build_external_publication_operation_intent(
            forged,  # type: ignore[arg-type]
            operation="fresh",
        )
    assert raised.value.detail.classification == "approved_by"


def test_canonical_json_is_exact_four_key_compact_sorted_utf8() -> None:
    intent = _intent()
    canonical = serialize_external_publication_operation_intent_canonical(intent)
    expected = (
        '{"operation":"fresh",'
        '"publication_approval_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
        '"publication_plan_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        '"schema_version":"external-publication-operation-intent.v1"}'
    )

    assert canonical == expected
    assert canonical.encode("utf-8") == _canonical_bytes(intent)
    assert json.loads(canonical) == _canonical_mapping(intent)
    assert set(json.loads(canonical)) == {
        "operation",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "schema_version",
    }
    assert " " not in canonical
    assert "\n" not in canonical


@pytest.mark.parametrize("operation", ["fresh", "resume"])
def test_canonical_bytes_and_digest_are_deterministic_and_exact(operation: str) -> None:
    intent = _intent(operation)
    canonical = _canonical_bytes(intent)

    assert external_publication_operation_intent_canonical_bytes(intent) == canonical
    assert (
        external_publication_operation_intent_digest(intent)
        == hashlib.sha256(canonical).hexdigest()
    )
    assert intent.digest == external_publication_operation_intent_digest(intent)


def test_fresh_and_resume_intents_are_distinct_records_and_digests() -> None:
    fresh = _intent("fresh")
    resume = _intent("resume")

    assert fresh != resume
    assert external_publication_operation_intent_canonical_bytes(fresh) != (
        external_publication_operation_intent_canonical_bytes(resume)
    )
    assert external_publication_operation_intent_digest(fresh) != (
        external_publication_operation_intent_digest(resume)
    )


def test_persistence_creates_exact_bytes_and_covers_file_and_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "intent.json"
    intent = _intent()
    fsync_calls: list[int] = []
    directory_calls: list[Path] = []
    monkeypatch.setattr(intent_module.os, "fsync", fsync_calls.append)
    monkeypatch.setattr(
        intent_module,
        "_fsync_intent_directory",
        directory_calls.append,
    )

    assert persist_external_publication_operation_intent(path, intent) is None
    assert path.read_bytes() == external_publication_operation_intent_canonical_bytes(
        intent
    )
    assert len(fsync_calls) == 1
    assert directory_calls == [tmp_path]


def test_persistence_derives_bytes_and_validates_target_before_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "intent.json"
    forged = _forged_instance(ExternalPublicationOperationIntent, _intent())
    object.__setattr__(forged, "operation", "FRESH")

    with pytest.raises(ExternalPublicationOperationIntentError) as raised:
        persist_external_publication_operation_intent(
            path,
            forged,  # type: ignore[arg-type]
        )
    _assert_intent_error(raised.value, "operation")
    assert not path.exists()


@pytest.mark.parametrize("path_kind", ["missing_parent", "parent_file", "str_path"])
def test_persistence_requires_exact_path_and_existing_directory(
    tmp_path: Path,
    path_kind: str,
) -> None:
    if path_kind == "missing_parent":
        path: object = tmp_path / "missing" / "intent.json"
    elif path_kind == "parent_file":
        parent = tmp_path / "parent-file"
        parent.write_bytes(b"file")
        path = parent / "intent.json"
    else:
        path = str(tmp_path / "intent.json")

    with pytest.raises(ExternalPublicationOperationIntentPersistenceError) as raised:
        persist_external_publication_operation_intent(path, _intent())  # type: ignore[arg-type]
    _assert_persistence_error(
        raised.value, "path_type" if path_kind == "str_path" else "parent"
    )


def test_persistence_rejects_symlink_directory_and_nonregular_target(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    symlink_target = tmp_path / "target"
    symlink_target.write_bytes(b"target")
    symlink = tmp_path / "symlink"
    symlink.symlink_to(symlink_target)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    for path in (directory, symlink, fifo):
        with pytest.raises(
            ExternalPublicationOperationIntentPersistenceError
        ) as raised:
            persist_external_publication_operation_intent(path, _intent())
        _assert_persistence_error(raised.value, "target")


def test_existing_identical_bytes_are_idempotent_and_durability_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "intent.json"
    intent = _intent()
    persist_external_publication_operation_intent(path, intent)
    before = path.read_bytes()
    fsync_calls: list[int] = []
    directory_calls: list[Path] = []
    monkeypatch.setattr(intent_module.os, "fsync", fsync_calls.append)
    monkeypatch.setattr(
        intent_module,
        "_fsync_intent_directory",
        directory_calls.append,
    )

    assert persist_external_publication_operation_intent(path, intent) is None
    assert path.read_bytes() == before
    assert len(fsync_calls) == 1
    assert directory_calls == [tmp_path]


def test_existing_different_bytes_conflict_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "intent.json"
    persist_external_publication_operation_intent(path, _intent("fresh"))
    before = path.read_bytes()
    directory_calls: list[Path] = []
    monkeypatch.setattr(
        intent_module,
        "_fsync_intent_directory",
        directory_calls.append,
    )

    with pytest.raises(ExternalPublicationOperationIntentConflictError) as raised:
        persist_external_publication_operation_intent(path, _intent("resume"))
    _assert_conflict_error(raised.value)
    assert path.read_bytes() == before
    assert directory_calls == []


def test_create_failure_is_fixed_and_leaves_target_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "intent.json"
    real_open = Path.open

    def fail_create(
        candidate: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> object:
        if candidate == path and mode == "xb":
            raise OSError("private create detail")
        return real_open(candidate, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_create)
    with pytest.raises(ExternalPublicationOperationIntentPersistenceError) as raised:
        persist_external_publication_operation_intent(path, _intent())
    _assert_persistence_error(raised.value, "create")
    assert not path.exists()
    assert "private create detail" not in str(raised.value)


class _FailingWriteHandle:
    def __init__(self, inner: object) -> None:
        self._inner = inner

    def write(self, contents: bytes) -> int:
        self._inner.write(contents)  # type: ignore[attr-defined]
        raise OSError("private write detail")

    def flush(self) -> None:
        self._inner.flush()  # type: ignore[attr-defined]

    def fileno(self) -> int:
        return self._inner.fileno()  # type: ignore[attr-defined]

    def close(self) -> None:
        self._inner.close()  # type: ignore[attr-defined]


class _FailingFlushHandle:
    def __init__(self, inner: object) -> None:
        self._inner = inner

    def write(self, contents: bytes) -> int:
        return self._inner.write(contents)  # type: ignore[attr-defined]

    def flush(self) -> None:
        raise OSError("private flush detail")

    def fileno(self) -> int:
        return self._inner.fileno()  # type: ignore[attr-defined]

    def close(self) -> None:
        self._inner.close()  # type: ignore[attr-defined]


@pytest.mark.parametrize("failure", ["write", "file_fsync", "directory_fsync"])
def test_post_create_failures_are_ambiguous_and_retain_exact_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    path = tmp_path / f"{failure}.json"
    intent = _intent()
    contents = external_publication_operation_intent_canonical_bytes(intent)
    real_open = Path.open

    if failure == "write":

        def failing_open(
            candidate: Path,
            mode: str = "r",
            *args: object,
            **kwargs: object,
        ) -> object:
            handle = real_open(candidate, mode, *args, **kwargs)
            if candidate == path and mode == "xb":
                return _FailingWriteHandle(handle)
            return handle

        monkeypatch.setattr(Path, "open", failing_open)
    elif failure == "file_fsync":

        def fail_file_fsync(_descriptor: int) -> None:
            raise OSError("private file fsync detail")

        monkeypatch.setattr(intent_module.os, "fsync", fail_file_fsync)
    else:

        def fail_directory_fsync(_directory: Path) -> None:
            raise OSError("private directory fsync detail")

        monkeypatch.setattr(
            intent_module,
            "_fsync_intent_directory",
            fail_directory_fsync,
        )

    with pytest.raises(ExternalPublicationOperationIntentPersistenceError) as raised:
        persist_external_publication_operation_intent(path, intent)
    _assert_persistence_error(raised.value, "ambiguous")
    assert path.exists()
    assert path.read_bytes() == contents
    assert all(detail not in str(raised.value) for detail in ("private", "fsync"))


def test_flush_failure_after_exclusive_create_is_ambiguous_retained_and_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "flush.json"
    intent = _intent()
    contents = external_publication_operation_intent_canonical_bytes(intent)
    real_open = Path.open
    create_calls = 0

    def failing_open(
        candidate: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal create_calls
        handle = real_open(candidate, mode, *args, **kwargs)
        if candidate == path and mode == "xb":
            create_calls += 1
            return _FailingFlushHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", failing_open)

    with pytest.raises(ExternalPublicationOperationIntentPersistenceError) as raised:
        persist_external_publication_operation_intent(path, intent)

    _assert_persistence_error(raised.value, "ambiguous")
    assert str(raised.value) == (
        "external publication operation intent persistence failed"
    )
    assert "private flush detail" not in str(raised.value)
    assert create_calls == 1
    assert path.exists()
    assert path.read_bytes() == contents


def test_persistence_does_not_retry_after_ambiguous_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "intent.json"
    open_calls: list[str] = []
    real_open = Path.open

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("private fsync detail")

    def spy_open(
        candidate: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> object:
        if candidate == path:
            open_calls.append(mode)
        return real_open(candidate, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy_open)
    monkeypatch.setattr(intent_module.os, "fsync", fail_fsync)
    with pytest.raises(ExternalPublicationOperationIntentPersistenceError) as raised:
        persist_external_publication_operation_intent(path, _intent())
    _assert_persistence_error(raised.value, "ambiguous")
    assert open_calls == ["xb"]


def test_loader_round_trip_returns_exact_model_and_stable_digest(
    tmp_path: Path,
) -> None:
    path = tmp_path / "intent.json"
    intent = _intent("resume")
    persist_external_publication_operation_intent(path, intent)

    loaded = load_external_publication_operation_intent(path)
    assert type(loaded) is ExternalPublicationOperationIntent
    assert loaded == intent
    assert external_publication_operation_intent_digest(loaded) == (
        external_publication_operation_intent_digest(intent)
    )


def test_loader_uses_one_bounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "intent.json"
    intent = _intent()
    path.write_bytes(external_publication_operation_intent_canonical_bytes(intent))
    real_open = Path.open
    read_sizes: list[int] = []

    class ReadSpy:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def __enter__(self) -> ReadSpy:
            return self

        def __exit__(self, *args: object) -> None:
            self._inner.close()  # type: ignore[attr-defined]

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return self._inner.read(size)  # type: ignore[attr-defined]

    def spy_open(
        candidate: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> object:
        handle = real_open(candidate, mode, *args, **kwargs)
        if candidate == path and mode == "rb":
            return ReadSpy(handle)
        return handle

    monkeypatch.setattr(Path, "open", spy_open)
    assert load_external_publication_operation_intent(path) == intent
    assert read_sizes == [intent_module._MAX_INTENT_BYTES + 1]


@pytest.mark.parametrize("path_kind", ["missing", "symlink", "directory", "fifo"])
def test_loader_rejects_missing_symlink_directory_and_nonfile_targets(
    tmp_path: Path,
    path_kind: str,
) -> None:
    path = tmp_path / path_kind
    if path_kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"target")
        path.symlink_to(target)
    elif path_kind == "directory":
        path.mkdir()
    elif path_kind == "fifo":
        os.mkfifo(path)

    with pytest.raises(ExternalPublicationOperationIntentLoadError) as raised:
        load_external_publication_operation_intent(path)
    _assert_load_error(raised.value, "target")


@pytest.mark.parametrize(
    "contents,classification",
    [
        (
            b'{"operation":"fresh","operation":"resume","publication_approval_sha256":"'
            + _APPROVAL_DIGEST.encode()
            + b'","publication_plan_sha256":"'
            + _PLAN_DIGEST.encode()
            + b'","schema_version":"'
            + _SCHEMA.encode()
            + b'"}',
            "parse",
        ),
        (
            b'{"operation":NaN,"publication_approval_sha256":"'
            + _APPROVAL_DIGEST.encode()
            + b'","publication_plan_sha256":"'
            + _PLAN_DIGEST.encode()
            + b'","schema_version":"'
            + _SCHEMA.encode()
            + b'"}',
            "parse",
        ),
        (b'{"operation":"fresh"}', "keys"),
        (
            b'{"extra":1,"operation":"fresh","publication_approval_sha256":"'
            + _APPROVAL_DIGEST.encode()
            + b'","publication_plan_sha256":"'
            + _PLAN_DIGEST.encode()
            + b'","schema_version":"'
            + _SCHEMA.encode()
            + b'"}',
            "keys",
        ),
        (b"\xff", "parse"),
    ],
)
def test_loader_rejects_duplicate_constants_keys_and_malformed_json(
    tmp_path: Path,
    contents: bytes,
    classification: str,
) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(contents)

    with pytest.raises(ExternalPublicationOperationIntentLoadError) as raised:
        load_external_publication_operation_intent(path)
    _assert_load_error(raised.value, classification)


@pytest.mark.parametrize(
    "mapping,classification",
    [
        ({"operation": "FRESH"}, "intent"),
        ({"operation": "fresh", "publication_approval_sha256": "A" * 64}, "intent"),
        ({"operation": "fresh", "publication_plan_sha256": "b" * 63}, "intent"),
        ({"operation": "fresh", "schema_version": "other"}, "intent"),
    ],
)
def test_loader_rejects_malformed_exact_values(
    tmp_path: Path,
    mapping: dict[str, object],
    classification: str,
) -> None:
    value = {
        "operation": "fresh",
        "publication_approval_sha256": _APPROVAL_DIGEST,
        "publication_plan_sha256": _PLAN_DIGEST,
        "schema_version": _SCHEMA,
    }
    value.update(mapping)
    path = tmp_path / "malformed.json"
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(ExternalPublicationOperationIntentLoadError) as raised:
        load_external_publication_operation_intent(path)
    _assert_load_error(raised.value, classification)


@pytest.mark.parametrize(
    "contents",
    [
        b' {"operation":"fresh","publication_approval_sha256":"'
        + _APPROVAL_DIGEST.encode()
        + b'","publication_plan_sha256":"'
        + _PLAN_DIGEST.encode()
        + b'","schema_version":"'
        + _SCHEMA.encode()
        + b'"}',
        b'{"schema_version":"external-publication-operation-intent.v1","publication_plan_sha256":"'
        + _PLAN_DIGEST.encode()
        + b'","publication_approval_sha256":"'
        + _APPROVAL_DIGEST.encode()
        + b'","operation":"fresh"}',
    ],
)
def test_loader_rejects_noncanonical_but_semantically_equivalent_json(
    tmp_path: Path,
    contents: bytes,
) -> None:
    path = tmp_path / "noncanonical.json"
    path.write_bytes(contents)

    with pytest.raises(ExternalPublicationOperationIntentLoadError) as raised:
        load_external_publication_operation_intent(path)
    _assert_load_error(raised.value, "noncanonical")


def test_loader_rejects_payload_larger_than_bounded_limit(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * (intent_module._MAX_INTENT_BYTES + 1))

    with pytest.raises(ExternalPublicationOperationIntentLoadError) as raised:
        load_external_publication_operation_intent(path)
    _assert_load_error(raised.value, "size")


def test_persistence_and_loader_errors_never_leak_path_or_details(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-intent-path.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ExternalPublicationOperationIntentLoadError) as raised:
        load_external_publication_operation_intent(path)
    _assert_load_error(raised.value, "parse")
    assert str(path) not in str(raised.value)
    assert "not-json" not in str(raised.value)


def test_source_audit_excludes_execution_routing_and_sensitive_or_ambient_access() -> (
    None
):
    source = inspect.getsource(intent_module)
    tree = ast.parse(source)
    forbidden_symbols = {
        "run_external_publication_operation",
        "execute_and_persist_approved_external_publication",
        "execute_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "claim_external_publication_attempt",
        "external_publication_consumption_key",
        "external_publication_attempt_claim_path",
        "load_external_publication_attempt_claim",
        "load_external_publication_execution_result",
        "persist_external_publication_execution_result",
        "load_external_publication_execution_reconciliation",
        "persist_external_publication_execution_reconciliation",
        "environ",
        "getenv",
        "datetime",
        "monotonic",
        "unlink",
        "mkdir",
    }
    referenced_symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced_symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced_symbols.add(node.attr)
        elif isinstance(node, ast.alias):
            referenced_symbols.add(node.name.split(".")[0])
    assert referenced_symbols.isdisjoint(forbidden_symbols)
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module in {"socket", "subprocess", "random", "time", "uuid"}
        for node in ast.walk(tree)
    )


def test_runtime_audit_uses_only_local_approval_and_sidecar_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("ambient operation must not be called")

    for module, name in (
        (socket, "socket"),
        (socket, "create_connection"),
        (subprocess, "run"),
        (subprocess, "Popen"),
        (random, "random"),
        (time, "time"),
        (uuid, "uuid4"),
    ):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, forbidden)

    intent = build_external_publication_operation_intent(
        _approval(),
        operation="fresh",
    )
    path = tmp_path / "intent.json"
    persist_external_publication_operation_intent(path, intent)
    assert load_external_publication_operation_intent(path) == intent


def test_real_approval_builder_persistence_loader_integration_is_persistence_only(
    tmp_path: Path,
) -> None:
    approval = _real_approval()
    fresh = build_external_publication_operation_intent(
        approval,
        operation="fresh",
    )
    fresh_path = tmp_path / "fresh-intent.json"
    persist_external_publication_operation_intent(fresh_path, fresh)
    loaded = load_external_publication_operation_intent(fresh_path)
    assert loaded == fresh
    assert loaded.publication_approval_sha256 == approval.digest
    assert loaded.publication_plan_sha256 == approval.publication_plan_sha256
    assert external_publication_operation_intent_digest(loaded) == (
        external_publication_operation_intent_digest(fresh)
    )
    persist_external_publication_operation_intent(fresh_path, fresh)

    resume = build_external_publication_operation_intent(
        approval,
        operation="resume",
    )
    assert resume.publication_approval_sha256 == fresh.publication_approval_sha256
    assert resume.publication_plan_sha256 == fresh.publication_plan_sha256
    assert external_publication_operation_intent_digest(resume) != (
        external_publication_operation_intent_digest(fresh)
    )
    before = fresh_path.read_bytes()
    with pytest.raises(ExternalPublicationOperationIntentConflictError):
        persist_external_publication_operation_intent(fresh_path, resume)
    assert fresh_path.read_bytes() == before


def test_public_engine_exports_are_available() -> None:
    assert callable(build_external_publication_operation_intent)
    assert callable(serialize_external_publication_operation_intent_canonical)
    assert callable(external_publication_operation_intent_canonical_bytes)
    assert callable(external_publication_operation_intent_digest)
    assert callable(persist_external_publication_operation_intent)
    assert callable(load_external_publication_operation_intent)
    assert intent_module.ExternalPublicationOperationIntentError.__name__ == (
        "ExternalPublicationOperationIntentError"
    )
    assert operation_module.run_external_publication_operation is not None
