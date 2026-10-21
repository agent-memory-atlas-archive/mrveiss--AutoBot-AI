# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""No secret-named env var carries a non-empty literal default (#16299).

``os.getenv("X_SECRET", "autobot-api-key-v1")`` ships a credential in the
source: when the variable is unset the service runs on a value anyone can read.

Boundary: ``*.py`` under ``autobot-slm-backend``, ``autobot-backend`` and
``autobot_shared`` (test files excluded). Detector: AST ``Call`` to
``os.getenv`` / ``getenv`` / ``os.environ.get`` whose first argument is a string
constant matching SECRET|TOKEN|KEY|PASSWORD and whose second is a non-empty
string constant. Other call shapes (``os.environ[...] or "x"``, defaults built
by f-string or a named constant) are NOT seen.

The baseline is shrink-only: the entries are name-matched false positives
(not credentials). A new hit fails; a baseline entry that stops matching fails.
"""

import ast
import re

from repo_tests._paths import repo_root

_ROOTS = ("autobot-slm-backend", "autobot-backend", "autobot_shared")
_NAME = re.compile(r"SECRET|TOKEN|KEY|PASSWORD")
_FUNCS = {"getenv", "get"}

# (file, env var name) -- not credentials: a Redis key prefix, a temp dir and a date
# cutoff (matched on KEY / SECRETS in the name). Shrink-only (docs/developer/RATCHET_BASELINES.md).
BASELINE = {
    ("autobot-backend/constants/event_stream_constants.py", "AUTOBOT_CHANNEL_SEQ_KEY_PREFIX"),
    ("autobot-backend/constants/event_stream_constants.py", "AUTOBOT_CHANNEL_STREAM_KEY_PREFIX"),
    ("autobot-slm-backend/services/api_key_authority.py", "AUTOBOT_API_KEY_SCOPES_ENFORCED_FROM"),
    ("autobot-slm-backend/services/secrets_store_snapshot.py", "SLM_SECRETS_BACKUP_STAGING_DIR"),
}


def _is_env_read(func: ast.expr) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "getenv"
    if isinstance(func, ast.Attribute) and func.attr in _FUNCS:
        v = func.value
        if func.attr == "getenv":
            return isinstance(v, ast.Name) and v.id == "os"
        return isinstance(v, ast.Attribute) and v.attr == "environ"
    return False


def _str(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def find_hits(source: str) -> list[str]:
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and _is_env_read(node.func) and len(node.args) >= 2):
            continue
        name, default = _str(node.args[0]), _str(node.args[1])
        if name and default and _NAME.search(name):
            hits.append(name)
    return hits


def _scan() -> set[tuple[str, str]]:
    root = repo_root()
    found = set()
    for top in _ROOTS:
        for path in sorted((root / top).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if "/tests/" in rel or path.name.startswith("test_") or path.name.endswith("_test.py"):
                continue
            try:
                src = path.read_text(encoding="utf-8")
                names = find_hits(src)
            except (SyntaxError, UnicodeDecodeError):
                continue
            found.update((rel, n) for n in names)
    return found


_LEGACY_LITERAL = "autobot-api-key-v1"
# The ONE sanctioned home of the old public pepper: a read-only verifier for rows
# hashed before the default was removed (#16299). Sunset: when every install has
# set SLM_HMAC_API_KEY_SECRET.
_LEGACY_LITERAL_FILE = "autobot-slm-backend/user_management/services/api_key_service.py"


def test_legacy_public_pepper_only_in_its_one_declared_place():
    root = repo_root()
    homes = set()
    for top in _ROOTS:
        for path in (root / top).rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if "/tests/" in rel or path.name.startswith("test_") or path.name.endswith("_test.py"):
                continue
            if _LEGACY_LITERAL in path.read_text(encoding="utf-8", errors="ignore"):
                homes.add(rel)
    assert homes == {_LEGACY_LITERAL_FILE}, sorted(homes)


def test_detector_sees_the_pattern():
    """Control: the detector must flag the shapes it claims to."""
    assert find_hits('import os\nos.getenv("A_SECRET", "x")') == ["A_SECRET"]
    assert find_hits('import os\nos.environ.get("A_TOKEN", "x")') == ["A_TOKEN"]
    assert find_hits('import os\nos.getenv("A_KEY", "")') == []
    assert find_hits('import os\nos.getenv("A_KEY")') == []
    assert find_hits('import os\nos.getenv("HOST", "x")') == []


def test_no_new_literal_secret_defaults():
    new = _scan() - BASELINE
    assert not new, f"secret-named env var with a literal default (#16299): {sorted(new)}"


def test_baseline_is_not_stale():
    stale = BASELINE - _scan()
    assert not stale, f"baseline entries no longer match; remove them: {sorted(stale)}"
