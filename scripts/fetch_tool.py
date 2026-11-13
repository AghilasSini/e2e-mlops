"""Download a pinned CLI tool, verify its SHA-256, and install it as an executable.

Used by CI jobs that run on a plain Python image (no curl/wget needed):

    python scripts/fetch_tool.py <url> <sha256> <dest> [--member <path inside .tar.gz>]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path


def fetch(url: str, sha256: str, dest: Path, member: str | None = None) -> None:
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - pinned https URLs
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != sha256.lower():
        raise SystemExit(f"checksum mismatch for {url}: expected {sha256}, got {digest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if member:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            source = archive.extractfile(member)
            if source is None:
                raise SystemExit(f"{member} not found in {url}")
            with dest.open("wb") as out:
                shutil.copyfileobj(source, out)
    else:
        dest.write_bytes(payload)
    dest.chmod(0o755)
    print(f"installed {dest} ({digest[:12]})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("sha256")
    parser.add_argument("dest", type=Path)
    parser.add_argument("--member")
    args = parser.parse_args(argv)
    fetch(args.url, args.sha256, args.dest, args.member)
    return 0


if __name__ == "__main__":
    sys.exit(main())
