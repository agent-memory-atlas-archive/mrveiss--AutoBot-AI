# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""File names that hold secrets: the one list a crawler or indexer must never ingest (#19339).

A file is matched by its NAME, not its content. A stem word naming a secret (``credentials.json``,
``api-token.txt``, ``db_password.conf``) or a key or env file shape (``*.pem``, ``id_rsa``,
``.env.local``) marks it. Stem words are split on ``-``, ``_``, ``.`` and spaces, so ``tokenizer.py``
or ``secretary.md`` is not a match.
"""

from __future__ import annotations

import fnmatch
import re

#: Whole words in a file stem that name a secret.
SECRET_NAME_WORDS: frozenset[str] = frozenset(
    {"secret", "secrets", "credential", "credentials", "token", "tokens", "password", "passwords", "passwd"}
    | {"apikey", "apikeys", "privatekey"}
)
#: File-name shapes of keys, keystores and env files, matched case-insensitively.
SECRET_FILE_GLOBS: tuple[str, ...] = (
    "*.key", "*.pem", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    ".env", ".env.*", "*.env", ".netrc", ".pgpass", ".htpasswd",
)  # fmt: skip
_WORD_SPLIT = re.compile(r"[-_.\s]+")
#: ``key``/``keys`` names a secret only beside one of these words (``service-account-key.json``,
#: ``ssh_key``), or as the whole stem of a data file (``key.json``); alone it is an ordinary word
#: (``key-concepts.md``). #19696 review, #19796.
_KEY_QUALIFIERS: frozenset[str] = frozenset(
    {"service", "account", "api", "private", "secret", "ssh", "signing", "master", "access", "encryption"}
)
_KEY_DATA_SUFFIXES: frozenset[str] = frozenset({"json", "yaml", "yml", "txt", "toml", "ini"})


def is_secret_file_name(name: str) -> bool:
    """Whether the file called *name* (a base name, not a path) looks like it holds a secret."""
    lowered = name.lower()
    if any(fnmatch.fnmatch(lowered, glob) for glob in SECRET_FILE_GLOBS):
        return True
    stem = lowered.rsplit(".", 1)[0] if "." in lowered.lstrip(".") else lowered
    words = [w for w in _WORD_SPLIT.split(stem) if w]
    joined_pairs = {a + b for a, b in zip(words, words[1:])}  # "api key" / "private_key" -> one word
    if SECRET_NAME_WORDS.intersection(words) or SECRET_NAME_WORDS.intersection(joined_pairs):
        return True
    return _names_a_key(words, lowered.rsplit(".", 1)[-1] if "." in lowered else "")


def _names_a_key(words: list[str], suffix: str) -> bool:
    """``key``/``keys`` after a qualifier word, or as the whole stem of a data file."""
    if words in (["key"], ["keys"]):
        return suffix in _KEY_DATA_SUFFIXES
    return any(w in ("key", "keys") and i > 0 and words[i - 1] in _KEY_QUALIFIERS for i, w in enumerate(words))
