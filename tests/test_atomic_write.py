"""Testes da escrita atômica de JSON (RNF10)."""

import json
import os

import pytest

from app import storage
from app.storage import write_json_atomic


def _leftover_tmp_files(folder) -> list:
    return [p for p in folder.iterdir() if p.name.endswith(".tmp")]


def test_writes_json_with_unicode(tmp_path):
    path = tmp_path / "run.json"
    write_json_atomic(path, {"texto": "Pix não chegou", "n": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"texto": "Pix não chegou", "n": 1}
    assert "não" in path.read_text(encoding="utf-8")  # sem ã
    assert _leftover_tmp_files(tmp_path) == []


def test_overwrites_existing_file(tmp_path):
    path = tmp_path / "run.json"
    write_json_atomic(path, {"v": 1})
    write_json_atomic(path, {"v": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 2}


def test_creates_missing_folders(tmp_path):
    path = tmp_path / "runs" / "C001" / "T001" / "run.json"
    write_json_atomic(path, [])
    assert path.exists()


def test_unserializable_data_keeps_original(tmp_path):
    path = tmp_path / "run.json"
    write_json_atomic(path, {"v": 1})
    with pytest.raises(TypeError):
        write_json_atomic(path, {"v": object()})
    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 1}
    assert _leftover_tmp_files(tmp_path) == []


def test_failure_before_rename_keeps_original(tmp_path, monkeypatch):
    path = tmp_path / "tickets.json"
    write_json_atomic(path, {"v": 1})

    def crash(*_args, **_kwargs):
        raise OSError("queda simulada antes do rename")

    monkeypatch.setattr(storage.os, "replace", crash)
    with pytest.raises(OSError, match="queda simulada"):
        write_json_atomic(path, {"v": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 1}
    assert _leftover_tmp_files(tmp_path) == []


def test_interrupt_during_write_keeps_original(tmp_path, monkeypatch):
    path = tmp_path / "tickets.json"
    write_json_atomic(path, {"v": 1})

    def interrupt(_fd):
        raise KeyboardInterrupt

    monkeypatch.setattr(storage.os, "fsync", interrupt)
    with pytest.raises(KeyboardInterrupt):
        write_json_atomic(path, {"v": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 1}
    assert _leftover_tmp_files(tmp_path) == []
    assert os.listdir(tmp_path) == ["tickets.json"]
