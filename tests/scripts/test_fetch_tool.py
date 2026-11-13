from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "fetch_tool", Path(__file__).resolve().parents[2] / "scripts" / "fetch_tool.py"
)
fetch_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_tool)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_plain_file_is_verified_and_executable(tmp_path: Path) -> None:
    source = tmp_path / "tool"
    source.write_bytes(b"#!/bin/sh\necho hi\n")
    dest = tmp_path / "bin" / "tool"
    fetch_tool.fetch(source.as_uri(), _sha(source.read_bytes()), dest)
    assert dest.read_bytes() == source.read_bytes()
    assert dest.stat().st_mode & 0o111


def test_checksum_mismatch_aborts(tmp_path: Path) -> None:
    source = tmp_path / "tool"
    source.write_bytes(b"payload")
    with pytest.raises(SystemExit, match="checksum mismatch"):
        fetch_tool.fetch(source.as_uri(), "0" * 64, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_member_is_extracted_from_tarball(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("linux-amd64/helm")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"helm"))
    tarball = tmp_path / "helm.tar.gz"
    tarball.write_bytes(buffer.getvalue())
    dest = tmp_path / "helm"
    assert (
        fetch_tool.main(
            [
                tarball.as_uri(),
                _sha(tarball.read_bytes()),
                str(dest),
                "--member",
                "linux-amd64/helm",
            ]
        )
        == 0
    )
    assert dest.read_bytes() == b"helm"
