# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""scripts/ci/apt_fallback_mirror.sh, on fixture apt directories (#19308).

The first version rewrote only archive.ubuntu.com URIs. The runners resolve the archive through
`mirror+file:/etc/apt/apt-mirrors.txt`, so it matched nothing and the "fallback" retried the
same mirror in silence (#19345 review). These pin both formats and the zero-change signal.
"""

from __future__ import annotations

import subprocess  # nosec B404  # fixed argv, no shell
from pathlib import Path

_SCRIPT = Path(__file__).with_name("apt_fallback_mirror.sh")
_MIRROR = "http://fallback.example/ubuntu/"
_RUNNER_LIST = "http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\nhttp://archive.ubuntu.com/ubuntu/\tpriority:2\n"
_MIRROR_SOURCES = "Types: deb\nURIs: mirror+file:/etc/apt/apt-mirrors.txt\nSuites: noble\n\nTypes: deb\nURIs: http://security.ubuntu.com/ubuntu/\n"


def _run(apt: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603  # fixed argv, no shell
        ["bash", str(_SCRIPT), str(apt), _MIRROR], capture_output=True, text=True, timeout=30, check=False
    )


def _apt(tmp_path: Path, files: dict[str, str]) -> Path:
    apt = tmp_path / "apt"
    for rel, body in files.items():
        (apt / rel).parent.mkdir(parents=True, exist_ok=True)
        (apt / rel).write_text(body, encoding="utf-8")
    return apt


def test_the_runners_mirror_list_gets_the_fallback_first(tmp_path):
    apt = _apt(tmp_path, {"apt-mirrors.txt": _RUNNER_LIST, "sources.list.d/ubuntu.sources": _MIRROR_SOURCES})

    result = _run(apt)

    assert result.returncode == 0 and result.stdout.strip() == "1", result.stdout + result.stderr
    lines = (apt / "apt-mirrors.txt").read_text(encoding="utf-8").splitlines()
    assert lines[0] == _MIRROR and len(lines) == 3, "fallback first, the runner's own entries kept after it"
    assert (apt / "sources.list.d/ubuntu.sources").read_text(encoding="utf-8") == _MIRROR_SOURCES


def test_a_plain_archive_uri_is_rewritten_and_security_is_left_alone(tmp_path):
    body = "Types: deb\nURIs: http://azure.archive.ubuntu.com/ubuntu/\n\nTypes: deb\nURIs: http://security.ubuntu.com/ubuntu/\n"
    apt = _apt(tmp_path, {"sources.list.d/ubuntu.sources": body})

    assert _run(apt).returncode == 0
    text = (apt / "sources.list.d/ubuntu.sources").read_text(encoding="utf-8")
    assert f"URIs: {_MIRROR}" in text and "security.ubuntu.com" in text and "azure.archive" not in text


def test_nothing_to_point_anywhere_exits_3_rather_than_reporting_success(tmp_path):
    apt = _apt(tmp_path, {"sources.list.d/other.sources": "Types: deb\nURIs: https://packages.example/repo/\n"})

    result = _run(apt)

    assert result.returncode == 3 and result.stdout.strip() == "0"


def test_a_second_run_does_not_stack_the_fallback(tmp_path):
    apt = _apt(tmp_path, {"apt-mirrors.txt": _RUNNER_LIST})
    _run(apt)

    assert _run(apt).returncode == 3, "already first: nothing changed, so it says so"
    assert (apt / "apt-mirrors.txt").read_text(encoding="utf-8").splitlines().count(_MIRROR) == 1
