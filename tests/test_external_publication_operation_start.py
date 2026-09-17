"""Focused provider-free tests for the Phase 290 operation-start fence."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import json
import random
import socket
import subprocess
import time
import uuid
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication_operation_start as start_module
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationIntentLoadError,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartConflictError,
    ExternalPublicationOperationStartError,
    ExternalPublicationOperationStartFailureDetail,
    ExternalPublicationOperationStartLoadError,
    ExternalPublicationOperationStartPersistenceError,
    acquire_external_publication_operation_start,
    approve_external_publication,
    build_external_publication_operation_intent,
    external_publication_operation_intent_digest,
    external_publication_operation_start_canonical_bytes,
    external_publication_operation_start_digest,
    load_external_publication_operation_intent,
    load_external_publication_operation_start,
    persist_external_publication_operation_intent,
    serialize_external_publication_operation_start_canonical,
)
from ai_office.engine.external_publication import ExternalPublicationPlan

_SCHEMA = "external-publication-operation-start.v1"
_INTENT_SCHEMA = "external-publication-operation-intent.v1"
_PLAN_DIGEST = "b" * 64
_APPROVAL_DIGEST = "c" * 64
_INTENT_DIGEST = "d" * 64


class StringChild(str):
    pass


def _intent(operation: str = "fresh") -> ExternalPublicationOperationIntent:
    return ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,
        publication_approval_sha256=_APPROVAL_DIGEST,
        publication_plan_sha256=_PLAN_DIGEST,
        operation=operation,  # type: ignore[arg-type]
    )


def _start(
    operation: str = "fresh",
    *,
    intent_digest: str = _INTENT_DIGEST,
) -> ExternalPublicationOperationStart:
    return ExternalPublicationOperationStart(
        schema_version=_SCHEMA,
        operation_intent_sha256=intent_digest,
        publication_approval_sha256=_APPROVAL_DIGEST,
        publication_plan_sha256=_PLAN_DIGEST,
        operation=operation,  # type: ignore[arg-type]
        state="started",
    )


def _forged_instance(cls: type[object], source: object) -> object:
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    return value


def _assert_start_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationOperationStartError
    assert isinstance(error, ValueError)
    assert str(error) == "external publication operation start is invalid"
    assert type(error.detail) is ExternalPublicationOperationStartFailureDetail
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationOperationStartPersistenceError
    assert str(error) == "external publication operation start persistence failed"
    assert type(error.detail) is ExternalPublicationOperationStartFailureDetail
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_conflict_error(error: ValueError) -> None:
    assert type(error) is ExternalPublicationOperationStartConflictError
    assert str(error) == "external publication operation start persistence failed"
    assert type(error.detail) is ExternalPublicationOperationStartFailureDetail
    assert error.detail.classification == "conflict"
    assert error.__cause__ is None


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationOperationStartLoadError
    assert str(error) == "external publication operation start could not be loaded"
    assert type(error.detail) is ExternalPublicationOperationStartFailureDetail
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _canonical_mapping(start: ExternalPublicationOperationStart) -> dict[str, object]:
    return {
        "operation": start.operation,
        "operation_intent_sha256": start.operation_intent_sha256,
        "publication_approval_sha256": start.publication_approval_sha256,
        "publication_plan_sha256": start.publication_plan_sha256,
        "schema_version": start.schema_version,
        "state": start.state,
    }


def _canonical_bytes(start: ExternalPublicationOperationStart) -> bytes:
    return json.dumps(
        _canonical_mapping(start),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def test_public_models_are_exact_frozen_dataclasses_and_exported() -> None:
    start = _start()
    result = ExternalPublicationOperationStartAcquisition(
        status="acquired", start=start
    )
    assert type(start) is ExternalPublicationOperationStart
    assert type(result) is ExternalPublicationOperationStartAcquisition
    assert dataclasses.is_dataclass(ExternalPublicationOperationStart)
    assert dataclasses.is_dataclass(ExternalPublicationOperationStartAcquisition)
    assert ExternalPublicationOperationStart.__dataclass_params__.frozen
    assert ExternalPublicationOperationStartAcquisition.__dataclass_params__.frozen
    assert tuple(field.name for field in fields(ExternalPublicationOperationStart)) == (
        "schema_version",
        "operation_intent_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "operation",
        "state",
    )
    assert tuple(
        field.name for field in fields(ExternalPublicationOperationStartAcquisition)
    ) == ("status", "start")
    assert (
        start_module.ExternalPublicationOperationStart
        is ExternalPublicationOperationStart
    )
    assert start_module.ExternalPublicationOperationStartAcquisition is (
        ExternalPublicationOperationStartAcquisition
    )
    assert "ExternalPublicationOperationStart" in start_module.__all__
    assert "ExternalPublicationOperationStartAcquisition" in start_module.__all__
    with pytest.raises(FrozenInstanceError):
        start.state = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.status = "already_acquired"  # type: ignore[misc]


@pytest.mark.parametrize("operation", ["fresh", "resume"])
def test_valid_models_preserve_exact_contract(operation: str) -> None:
    start = _start(operation)
    assert start.schema_version == _SCHEMA
    assert start.operation_intent_sha256 == _INTENT_DIGEST
    assert start.publication_approval_sha256 == _APPROVAL_DIGEST
    assert start.publication_plan_sha256 == _PLAN_DIGEST
    assert type(start.operation) is str
    assert start.operation == operation
    assert type(start.state) is str
    assert start.state == "started"
    for status in ("acquired", "already_acquired"):
        result = ExternalPublicationOperationStartAcquisition(
            status=status, start=start
        )
        assert result.status == status
        assert result.start is start


@pytest.mark.parametrize(
    "field_name,value,classification",
    [
        ("schema_version", "external-publication-operation-start.v2", "schema_version"),
        ("operation_intent_sha256", "A" * 64, "intent_digest"),
        ("operation_intent_sha256", "a" * 63, "intent_digest"),
        ("operation_intent_sha256", StringChild("a" * 64), "intent_digest"),
        ("publication_approval_sha256", "A" * 64, "approval_digest"),
        ("publication_plan_sha256", "b" * 63, "plan_digest"),
        ("operation", "FRESH", "operation"),
        ("operation", StringChild("fresh"), "operation"),
        ("state", "STARTED", "state"),
        ("state", StringChild("started"), "state"),
    ],
)
def test_model_rejects_malformed_exact_fields(
    field_name: str, value: object, classification: str
) -> None:
    forged = _forged_instance(ExternalPublicationOperationStart, _start())
    object.__setattr__(forged, field_name, value)
    with pytest.raises(ExternalPublicationOperationStartError) as raised:
        serialize_external_publication_operation_start_canonical(forged)  # type: ignore[arg-type]
    _assert_start_error(raised.value, classification)


@pytest.mark.parametrize("candidate", [None, SimpleNamespace(), {}, "start"])
def test_canonical_boundaries_require_exact_start_type(candidate: object) -> None:
    for helper in (
        serialize_external_publication_operation_start_canonical,
        external_publication_operation_start_canonical_bytes,
        external_publication_operation_start_digest,
    ):
        with pytest.raises(ExternalPublicationOperationStartError) as raised:
            helper(candidate)  # type: ignore[arg-type]
        _assert_start_error(raised.value, "start_type")


def test_start_subclass_and_acquisition_lookalikes_are_rejected() -> None:
    valid = _start()

    class StartSubclass(ExternalPublicationOperationStart):
        pass

    subclass = _forged_instance(StartSubclass, valid)
    lookalike = SimpleNamespace(**dataclasses.asdict(valid))
    for candidate in (subclass, lookalike):
        with pytest.raises(ExternalPublicationOperationStartError) as raised:
            serialize_external_publication_operation_start_canonical(candidate)  # type: ignore[arg-type]
        _assert_start_error(raised.value, "start_type")

    with pytest.raises(ExternalPublicationOperationStartError) as raised:
        ExternalPublicationOperationStartAcquisition(
            status="acquired",
            start=subclass,  # type: ignore[arg-type]
        )
    _assert_start_error(raised.value, "start_type")


@pytest.mark.parametrize("status", ["ACQUIRED", "already", StringChild("acquired"), 1])
def test_acquisition_rejects_malformed_status(status: object) -> None:
    with pytest.raises(ExternalPublicationOperationStartError) as raised:
        ExternalPublicationOperationStartAcquisition(
            status=status,
            start=_start(),  # type: ignore[arg-type]
        )
    _assert_start_error(raised.value, "status")


def test_canonical_json_bytes_and_digest_are_exact() -> None:
    start = _start()
    canonical = serialize_external_publication_operation_start_canonical(start)
    expected = (
        '{"operation":"fresh",'
        '"operation_intent_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",'
        '"publication_approval_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
        '"publication_plan_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        '"schema_version":"external-publication-operation-start.v1",'
        '"state":"started"}'
    )
    assert canonical == expected
    assert canonical.encode("utf-8") == _canonical_bytes(start)
    assert (
        external_publication_operation_start_canonical_bytes(start)
        == canonical.encode()
    )
    assert (
        external_publication_operation_start_digest(start)
        == hashlib.sha256(canonical.encode()).hexdigest()
    )
    assert json.loads(canonical) == _canonical_mapping(start)
    assert " " not in canonical
    assert "\n" not in canonical


def test_loader_round_trip_and_one_bounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "start.json"
    start = _start("resume")
    path.write_bytes(_canonical_bytes(start))
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
        candidate: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        handle = real_open(candidate, mode, *args, **kwargs)
        if candidate == path and mode == "rb":
            return ReadSpy(handle)
        return handle

    monkeypatch.setattr(Path, "open", spy_open)
    loaded = load_external_publication_operation_start(path)
    assert type(loaded) is ExternalPublicationOperationStart
    assert loaded == start
    assert read_sizes == [start_module._MAX_START_BYTES + 1]
    assert external_publication_operation_start_digest(loaded) == (
        external_publication_operation_start_digest(start)
    )


@pytest.mark.parametrize(
    "contents,classification",
    [
        (b"{", "parse"),
        (b"[]", "keys"),
        (b"null", "keys"),
        (
            b'{"operation":"fresh","operation":"resume",'
            b'"operation_intent_sha256":"' + _INTENT_DIGEST.encode() + b'",'
            b'"publication_approval_sha256":"' + _APPROVAL_DIGEST.encode() + b'",'
            b'"publication_plan_sha256":"' + _PLAN_DIGEST.encode() + b'",'
            b'"schema_version":"' + _SCHEMA.encode() + b'","state":"started"}',
            "parse",
        ),
        (
            b'{"operation":NaN,"operation_intent_sha256":"'
            + _INTENT_DIGEST.encode()
            + b'","publication_approval_sha256":"'
            + _APPROVAL_DIGEST.encode()
            + b'","publication_plan_sha256":"'
            + _PLAN_DIGEST.encode()
            + b'","schema_version":"'
            + _SCHEMA.encode()
            + b'","state":"started"}',
            "parse",
        ),
        (b'{"operation":"fresh"}', "keys"),
        (
            b'{"extra":1,"operation":"fresh","operation_intent_sha256":"'
            + _INTENT_DIGEST.encode()
            + b'","publication_approval_sha256":"'
            + _APPROVAL_DIGEST.encode()
            + b'","publication_plan_sha256":"'
            + _PLAN_DIGEST.encode()
            + b'","schema_version":"'
            + _SCHEMA.encode()
            + b'","state":"started"}',
            "keys",
        ),
        (b"\xff", "parse"),
    ],
)
def test_loader_rejects_strict_json_violations(
    tmp_path: Path, contents: bytes, classification: str
) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(contents)
    with pytest.raises(ExternalPublicationOperationStartLoadError) as raised:
        load_external_publication_operation_start(path)
    _assert_load_error(raised.value, classification)


@pytest.mark.parametrize(
    "mapping",
    [
        {"operation": "FRESH"},
        {"state": "running"},
        {"operation_intent_sha256": "A" * 64},
        {"publication_approval_sha256": "c" * 63},
        {"publication_plan_sha256": "B" * 64},
        {"schema_version": "other"},
    ],
)
def test_loader_rejects_malformed_values(
    tmp_path: Path, mapping: dict[str, object]
) -> None:
    value = _canonical_mapping(_start())
    value.update(mapping)
    path = tmp_path / "malformed.json"
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(ExternalPublicationOperationStartLoadError) as raised:
        load_external_publication_operation_start(path)
    _assert_load_error(raised.value, "start")


@pytest.mark.parametrize(
    "contents",
    [
        b' {"operation":"fresh","operation_intent_sha256":"'
        + _INTENT_DIGEST.encode()
        + b'","publication_approval_sha256":"'
        + _APPROVAL_DIGEST.encode()
        + b'","publication_plan_sha256":"'
        + _PLAN_DIGEST.encode()
        + b'","schema_version":"'
        + _SCHEMA.encode()
        + b'","state":"started"}',
        b'{"state":"started","schema_version":"external-publication-operation-start.v1",'
        b'"publication_plan_sha256":"'
        + _PLAN_DIGEST.encode()
        + b'","publication_approval_sha256":"'
        + _APPROVAL_DIGEST.encode()
        + b'","operation_intent_sha256":"'
        + _INTENT_DIGEST.encode()
        + b'","operation":"fresh"}',
    ],
)
def test_loader_rejects_noncanonical_equivalent_bytes(
    tmp_path: Path, contents: bytes
) -> None:
    path = tmp_path / "noncanonical.json"
    path.write_bytes(contents)
    with pytest.raises(ExternalPublicationOperationStartLoadError) as raised:
        load_external_publication_operation_start(path)
    _assert_load_error(raised.value, "noncanonical")


def test_loader_rejects_path_and_target_contract_violations(tmp_path: Path) -> None:
    with pytest.raises(ExternalPublicationOperationStartLoadError) as raised:
        load_external_publication_operation_start(str(tmp_path / "start.json"))  # type: ignore[arg-type]
    _assert_load_error(raised.value, "path_type")

    directory = tmp_path / "directory"
    directory.mkdir()
    target = tmp_path / "target"
    target.write_bytes(b"x")
    symlink = tmp_path / "symlink"
    symlink.symlink_to(target)
    for path in (tmp_path / "missing", directory, symlink):
        with pytest.raises(ExternalPublicationOperationStartLoadError) as raised:
            load_external_publication_operation_start(path)
        _assert_load_error(raised.value, "target")


def test_acquisition_signature_and_authoritative_defaults_are_exact() -> None:
    signature = inspect.signature(acquire_external_publication_operation_start)
    parameters = list(signature.parameters.values())
    assert [parameter.name for parameter in parameters] == [
        "intent_path",
        "start_path",
        "intent_loader",
        "intent_digest_function",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in parameters
    )
    assert parameters[2].default is load_external_publication_operation_intent
    assert parameters[3].default is external_publication_operation_intent_digest


def test_acquisition_requires_exact_paths_and_callable_dependencies_before_loader() -> (
    None
):
    calls = 0

    def loader(_path: Path) -> object:
        nonlocal calls
        calls += 1
        return _intent()

    with pytest.raises(ExternalPublicationOperationStartError) as raised:
        acquire_external_publication_operation_start(
            intent_path="intent.json",  # type: ignore[arg-type]
            start_path=Path("start.json"),
            intent_loader=loader,
        )
    _assert_start_error(raised.value, "path_type")
    assert calls == 0

    with pytest.raises(ExternalPublicationOperationStartError) as raised:
        acquire_external_publication_operation_start(
            intent_path=Path("intent.json"),
            start_path=Path("start.json"),
            intent_loader=object(),  # type: ignore[arg-type]
        )
    _assert_start_error(raised.value, "configuration")


def test_acquisition_calls_loader_and_digest_once_with_exact_identity(
    tmp_path: Path,
) -> None:
    intent_path = tmp_path / "intent.json"
    start_path = tmp_path / "start.json"
    intent = _intent()
    loader_calls: list[object] = []
    digest_calls: list[object] = []

    def loader(path: Path) -> ExternalPublicationOperationIntent:
        loader_calls.append(path)
        return intent

    def digest(value: ExternalPublicationOperationIntent) -> str:
        digest_calls.append(value)
        return _INTENT_DIGEST

    result = acquire_external_publication_operation_start(
        intent_path=intent_path,
        start_path=start_path,
        intent_loader=loader,
        intent_digest_function=digest,
    )
    assert result.status == "acquired"
    assert type(result.start) is ExternalPublicationOperationStart
    assert loader_calls == [intent_path]
    assert loader_calls[0] is intent_path
    assert digest_calls == [intent]
    assert digest_calls[0] is intent
    assert result.start.operation_intent_sha256 == _INTENT_DIGEST
    assert (
        result.start.publication_approval_sha256 == intent.publication_approval_sha256
    )
    assert result.start.publication_plan_sha256 == intent.publication_plan_sha256
    assert result.start.operation == intent.operation
    assert (
        start_path.read_bytes()
        == external_publication_operation_start_canonical_bytes(result.start)
    )


@pytest.mark.parametrize(
    "candidate", [SimpleNamespace(), {}, ExternalPublicationOperationIntent]
)
def test_acquisition_rejects_wrong_or_subclass_intent_before_digest(
    tmp_path: Path, candidate: object
) -> None:
    if candidate is ExternalPublicationOperationIntent:

        class IntentSubclass(ExternalPublicationOperationIntent):
            pass

        candidate = _forged_instance(IntentSubclass, _intent())
    calls = 0

    def digest(_value: object) -> str:
        nonlocal calls
        calls += 1
        return _INTENT_DIGEST

    with pytest.raises(ExternalPublicationOperationStartError) as raised:
        acquire_external_publication_operation_start(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            intent_loader=lambda _path: candidate,
            intent_digest_function=digest,
        )
    _assert_start_error(raised.value, "intent_contract")
    assert calls == 0
    assert not (tmp_path / "start.json").exists()


@pytest.mark.parametrize("digest_value", ["bad", "A" * 64, StringChild("a" * 64), 1])
def test_acquisition_rejects_malformed_digest_before_target_mutation(
    tmp_path: Path, digest_value: object
) -> None:
    calls = 0

    def digest(_value: object) -> object:
        nonlocal calls
        calls += 1
        return digest_value

    with pytest.raises(ExternalPublicationOperationStartError) as raised:
        acquire_external_publication_operation_start(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            intent_loader=lambda _path: _intent(),
            intent_digest_function=digest,
        )
    _assert_start_error(raised.value, "intent_digest")
    assert calls == 1
    assert not (tmp_path / "start.json").exists()


def test_acquisition_propagates_known_phase_289_errors_by_identity(
    tmp_path: Path,
) -> None:
    loader_error = ExternalPublicationOperationIntentLoadError("target")

    def loader(_path: Path) -> object:
        raise loader_error

    with pytest.raises(ExternalPublicationOperationIntentError) as raised:
        acquire_external_publication_operation_start(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            intent_loader=loader,
        )
    assert raised.value is loader_error

    digest_error = ExternalPublicationOperationIntentError("intent")

    def digest(_intent: object) -> object:
        raise digest_error

    with pytest.raises(ExternalPublicationOperationIntentError) as raised:
        acquire_external_publication_operation_start(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            intent_loader=lambda _path: _intent(),
            intent_digest_function=digest,
        )
    assert raised.value is digest_error


def test_acquisition_sanitizes_unexpected_dependency_errors(tmp_path: Path) -> None:
    def loader(_path: Path) -> object:
        raise RuntimeError("private intent path and provider response")

    with pytest.raises(ExternalPublicationOperationStartError) as raised:
        acquire_external_publication_operation_start(
            intent_path=tmp_path / "intent.json",
            start_path=tmp_path / "start.json",
            intent_loader=loader,
        )
    _assert_start_error(raised.value, "dependency_error")
    assert "private" not in str(raised.value)
    assert "provider" not in str(raised.value)


def test_new_acquisition_orders_write_flush_file_fsync_close_and_directory_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent_path = tmp_path / "intent.json"
    start_path = tmp_path / "start.json"
    events: list[str] = []
    real_open = Path.open

    class HandleSpy:
        def __init__(self, inner: object) -> None:
            self.inner = inner

        def write(self, contents: bytes) -> int:
            events.append("write")
            return self.inner.write(contents)  # type: ignore[attr-defined]

        def flush(self) -> None:
            events.append("flush")
            self.inner.flush()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            return self.inner.fileno()  # type: ignore[attr-defined]

        def close(self) -> None:
            events.append("close")
            self.inner.close()  # type: ignore[attr-defined]

    def spy_open(
        candidate: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        handle = real_open(candidate, mode, *args, **kwargs)
        if candidate == start_path and mode == "xb":
            return HandleSpy(handle)
        return handle

    def spy_fsync(_descriptor: int) -> None:
        events.append("file_fsync")

    def spy_directory(_directory: Path) -> None:
        events.append("directory_fsync")

    monkeypatch.setattr(Path, "open", spy_open)
    monkeypatch.setattr(start_module.os, "fsync", spy_fsync)
    monkeypatch.setattr(start_module, "_fsync_operation_start_directory", spy_directory)
    result = acquire_external_publication_operation_start(
        intent_path=intent_path,
        start_path=start_path,
        intent_loader=lambda path: _intent(),
        intent_digest_function=lambda value: _INTENT_DIGEST,
    )
    assert result.status == "acquired"
    assert events == ["write", "flush", "file_fsync", "close", "directory_fsync"]
    assert (
        start_path.read_bytes()
        == external_publication_operation_start_canonical_bytes(result.start)
    )


def test_identical_existing_acquisition_returns_already_acquired_and_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent_path = tmp_path / "intent.json"
    start_path = tmp_path / "start.json"
    first = acquire_external_publication_operation_start(
        intent_path=intent_path,
        start_path=start_path,
        intent_loader=lambda _path: _intent(),
        intent_digest_function=lambda _intent: _INTENT_DIGEST,
    )
    before = start_path.read_bytes()
    fsync_calls: list[int] = []
    directory_calls: list[Path] = []
    monkeypatch.setattr(start_module.os, "fsync", fsync_calls.append)
    monkeypatch.setattr(
        start_module, "_fsync_operation_start_directory", directory_calls.append
    )
    second = acquire_external_publication_operation_start(
        intent_path=intent_path,
        start_path=start_path,
        intent_loader=lambda _path: _intent(),
        intent_digest_function=lambda _intent: _INTENT_DIGEST,
    )
    assert first.status == "acquired"
    assert second.status == "already_acquired"
    assert second.start is not first.start
    assert type(second.start) is ExternalPublicationOperationStart
    assert start_path.read_bytes() == before
    assert len(fsync_calls) == 1
    assert directory_calls == [tmp_path]


@pytest.mark.parametrize("contents", [b"partial", _canonical_bytes(_start("resume"))])
def test_occupied_different_or_partial_target_conflicts_without_mutation(
    tmp_path: Path, contents: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_path = tmp_path / "start.json"
    start_path.write_bytes(contents)
    before = start_path.read_bytes()
    monkeypatch.setattr(
        start_module, "_fsync_operation_start_directory", lambda _path: pytest.fail()
    )
    with pytest.raises(ExternalPublicationOperationStartConflictError) as raised:
        acquire_external_publication_operation_start(
            intent_path=tmp_path / "intent.json",
            start_path=start_path,
            intent_loader=lambda _path: _intent("fresh"),
            intent_digest_function=lambda _intent: _INTENT_DIGEST,
        )
    _assert_conflict_error(raised.value)
    assert start_path.read_bytes() == before


def test_create_failure_before_creation_leaves_target_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_path = tmp_path / "start.json"
    real_open = Path.open

    def fail_create(
        candidate: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        if candidate == start_path and mode == "xb":
            raise OSError("private create detail")
        return real_open(candidate, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_create)
    with pytest.raises(ExternalPublicationOperationStartPersistenceError) as raised:
        acquire_external_publication_operation_start(
            intent_path=tmp_path / "intent.json",
            start_path=start_path,
            intent_loader=lambda _path: _intent(),
            intent_digest_function=lambda _intent: _INTENT_DIGEST,
        )
    _assert_persistence_error(raised.value, "create")
    assert not start_path.exists()
    assert "private create detail" not in str(raised.value)


class _WriteThenFailHandle:
    def __init__(self, inner: object, failure: str) -> None:
        self.inner = inner
        self.failure = failure

    def write(self, contents: bytes) -> int:
        result = self.inner.write(contents)  # type: ignore[attr-defined]
        if self.failure == "write":
            raise OSError("private write detail")
        return result

    def flush(self) -> None:
        if self.failure == "flush":
            raise OSError("private flush detail")
        self.inner.flush()  # type: ignore[attr-defined]

    def fileno(self) -> int:
        return self.inner.fileno()  # type: ignore[attr-defined]

    def close(self) -> None:
        try:
            self.inner.close()  # type: ignore[attr-defined]
        finally:
            if self.failure == "close":
                raise OSError("private close detail")


@pytest.mark.parametrize(
    "failure", ["write", "flush", "file_fsync", "close", "directory_fsync"]
)
def test_post_create_failures_are_ambiguous_retained_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    start_path = tmp_path / f"{failure}.json"
    contents = external_publication_operation_start_canonical_bytes(_start())
    real_open = Path.open
    create_calls = 0

    def spy_open(
        candidate: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        nonlocal create_calls
        handle = real_open(candidate, mode, *args, **kwargs)
        if candidate == start_path and mode == "xb":
            create_calls += 1
            return _WriteThenFailHandle(handle, failure)
        return handle

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("private file fsync detail")

    def fail_directory(_directory: Path) -> None:
        raise OSError("private directory fsync detail")

    monkeypatch.setattr(Path, "open", spy_open)
    if failure == "file_fsync":
        monkeypatch.setattr(start_module.os, "fsync", fail_fsync)
    if failure == "directory_fsync":
        monkeypatch.setattr(
            start_module, "_fsync_operation_start_directory", fail_directory
        )

    with pytest.raises(ExternalPublicationOperationStartPersistenceError) as raised:
        acquire_external_publication_operation_start(
            intent_path=tmp_path / "intent.json",
            start_path=start_path,
            intent_loader=lambda _path: _intent(),
            intent_digest_function=lambda _intent: _INTENT_DIGEST,
        )
    _assert_persistence_error(raised.value, "ambiguous")
    assert create_calls == 1
    assert start_path.exists()
    assert start_path.read_bytes() == contents
    assert all(private not in str(raised.value) for private in ("private", "detail"))


def test_later_exact_ambiguous_artifact_is_only_already_acquired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_path = tmp_path / "start.json"
    real_fsync = start_module.os.fsync
    real_directory = start_module._fsync_operation_start_directory

    def fail_once(_descriptor: int) -> None:
        monkeypatch.setattr(start_module.os, "fsync", real_fsync)
        raise OSError("private fsync detail")

    monkeypatch.setattr(start_module.os, "fsync", fail_once)
    with pytest.raises(ExternalPublicationOperationStartPersistenceError):
        acquire_external_publication_operation_start(
            intent_path=tmp_path / "intent.json",
            start_path=start_path,
            intent_loader=lambda _path: _intent(),
            intent_digest_function=lambda _intent: _INTENT_DIGEST,
        )
    contents = start_path.read_bytes()
    assert contents == external_publication_operation_start_canonical_bytes(_start())
    monkeypatch.setattr(
        start_module, "_fsync_operation_start_directory", real_directory
    )
    result = acquire_external_publication_operation_start(
        intent_path=tmp_path / "intent.json",
        start_path=start_path,
        intent_loader=lambda _path: _intent(),
        intent_digest_function=lambda _intent: _INTENT_DIGEST,
    )
    assert result.status == "already_acquired"
    assert start_path.read_bytes() == contents


def test_later_partial_ambiguous_artifact_fails_closed(tmp_path: Path) -> None:
    start_path = tmp_path / "start.json"
    start_path.write_bytes(b"partial")
    before = start_path.read_bytes()
    with pytest.raises(ExternalPublicationOperationStartConflictError):
        acquire_external_publication_operation_start(
            intent_path=tmp_path / "intent.json",
            start_path=start_path,
            intent_loader=lambda _path: _intent(),
            intent_digest_function=lambda _intent: _INTENT_DIGEST,
        )
    assert start_path.read_bytes() == before


def test_sequential_acquisition_has_one_acquired_then_already_acquired(
    tmp_path: Path,
) -> None:
    kwargs = {
        "intent_path": tmp_path / "intent.json",
        "start_path": tmp_path / "start.json",
        "intent_loader": lambda _path: _intent(),
        "intent_digest_function": lambda _intent: _INTENT_DIGEST,
    }
    first = acquire_external_publication_operation_start(**kwargs)
    second = acquire_external_publication_operation_start(**kwargs)
    assert [first.status, second.status] == ["acquired", "already_acquired"]


def test_resume_has_independent_marker_and_never_converts_fresh(tmp_path: Path) -> None:
    fresh_path = tmp_path / "fresh-start.json"
    resume_path = tmp_path / "resume-start.json"
    fresh = acquire_external_publication_operation_start(
        intent_path=tmp_path / "fresh-intent.json",
        start_path=fresh_path,
        intent_loader=lambda _path: _intent("fresh"),
        intent_digest_function=lambda _intent: _INTENT_DIGEST,
    )
    resume = acquire_external_publication_operation_start(
        intent_path=tmp_path / "resume-intent.json",
        start_path=resume_path,
        intent_loader=lambda _path: _intent("resume"),
        intent_digest_function=lambda _intent: _INTENT_DIGEST,
    )
    assert fresh.status == "acquired"
    assert resume.status == "acquired"
    assert fresh.start.operation == "fresh"
    assert resume.start.operation == "resume"
    assert fresh.start != resume.start
    before = fresh_path.read_bytes()
    with pytest.raises(ExternalPublicationOperationStartConflictError):
        acquire_external_publication_operation_start(
            intent_path=tmp_path / "resume-intent.json",
            start_path=fresh_path,
            intent_loader=lambda _path: _intent("resume"),
            intent_digest_function=lambda _intent: _INTENT_DIGEST,
        )
    assert fresh_path.read_bytes() == before


def test_intent_bytes_remain_unchanged_across_all_start_attempts(
    tmp_path: Path,
) -> None:
    intent = _intent()
    intent_path = tmp_path / "intent.json"
    persist_external_publication_operation_intent(intent_path, intent)
    before = intent_path.read_bytes()
    start_path = tmp_path / "start.json"
    first = acquire_external_publication_operation_start(
        intent_path=intent_path, start_path=start_path
    )
    second = acquire_external_publication_operation_start(
        intent_path=intent_path, start_path=start_path
    )
    assert first.status == "acquired"
    assert second.status == "already_acquired"
    assert intent_path.read_bytes() == before


def test_source_audit_excludes_forbidden_dependencies_and_ambient_access() -> None:
    source = inspect.getsource(start_module)
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
        "replace",
        "rename",
    }
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.alias):
            referenced.add(node.name.split(".")[0])
    assert referenced.isdisjoint(forbidden_symbols)
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module in {"socket", "subprocess", "random", "time", "uuid"}
        for node in ast.walk(tree)
    )


def test_runtime_audit_uses_local_phase_289_and_start_files_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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

    intent = _intent()
    intent_path = tmp_path / "intent.json"
    persist_external_publication_operation_intent(intent_path, intent)
    result = acquire_external_publication_operation_start(
        intent_path=intent_path,
        start_path=tmp_path / "start.json",
    )
    assert result.status == "acquired"


def _real_approval() -> ExternalPublicationApproval:
    plan = ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="regen-290-integration",
        reconciliation_evidence_sha256="d" * 64,
        receipt_sha256="e" * 64,
        business_output_sha256="f" * 64,
        output_byte_length=17,
        provider="future-provider",
        publication_target_sha256="1" * 64,
    )
    return approve_external_publication(
        plan,
        approved_by="human-reviewer-290-integration",
        approval_id="approval-290-integration",
    )


def test_real_phase_289_to_phase_290_local_integration_is_provider_free(
    tmp_path: Path,
) -> None:
    approval = _real_approval()
    fresh_intent = build_external_publication_operation_intent(
        approval, operation="fresh"
    )
    fresh_intent_path = tmp_path / "fresh-intent.json"
    fresh_start_path = tmp_path / "fresh-start.json"
    persist_external_publication_operation_intent(fresh_intent_path, fresh_intent)
    intent_before = fresh_intent_path.read_bytes()
    first = acquire_external_publication_operation_start(
        intent_path=fresh_intent_path, start_path=fresh_start_path
    )
    loaded_start = load_external_publication_operation_start(fresh_start_path)
    assert first.status == "acquired"
    assert loaded_start == first.start
    assert loaded_start.operation_intent_sha256 == (
        external_publication_operation_intent_digest(fresh_intent)
    )
    assert fresh_intent_path.read_bytes() == intent_before
    second = acquire_external_publication_operation_start(
        intent_path=fresh_intent_path, start_path=fresh_start_path
    )
    assert second.status == "already_acquired"
    start_before = fresh_start_path.read_bytes()
    assert fresh_start_path.read_bytes() == start_before

    resume_intent = build_external_publication_operation_intent(
        approval, operation="resume"
    )
    resume_intent_path = tmp_path / "resume-intent.json"
    resume_start_path = tmp_path / "resume-start.json"
    persist_external_publication_operation_intent(resume_intent_path, resume_intent)
    resumed = acquire_external_publication_operation_start(
        intent_path=resume_intent_path, start_path=resume_start_path
    )
    assert resumed.status == "acquired"
    assert resumed.start.operation == "resume"
    assert resumed.start != first.start
    with pytest.raises(ExternalPublicationOperationStartConflictError):
        acquire_external_publication_operation_start(
            intent_path=resume_intent_path, start_path=fresh_start_path
        )
    assert fresh_start_path.read_bytes() == start_before


def test_public_engine_exports_are_available() -> None:
    for exported in (
        ExternalPublicationOperationStart,
        ExternalPublicationOperationStartAcquisition,
        ExternalPublicationOperationStartError,
        ExternalPublicationOperationStartFailureDetail,
        ExternalPublicationOperationStartPersistenceError,
        ExternalPublicationOperationStartConflictError,
        ExternalPublicationOperationStartLoadError,
        serialize_external_publication_operation_start_canonical,
        external_publication_operation_start_canonical_bytes,
        external_publication_operation_start_digest,
        load_external_publication_operation_start,
        acquire_external_publication_operation_start,
    ):
        assert exported is not None
    assert start_module.ExternalPublicationOperationStartError.__name__ == (
        "ExternalPublicationOperationStartError"
    )
