# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Secret-shaped file names, and the near misses that are not (#19339)."""

import pytest

from autobot_shared.secret_file_names import is_secret_file_name


@pytest.mark.parametrize(
    "name",
    [
        "credentials.json", "secrets.toml", "api-token.txt", "db_password.conf", "my.private.key.txt",
        "server.pem", "tls.key", "id_rsa", "id_ed25519.pub", ".env", ".env.local", "prod.env",
        ".netrc", "API_KEY.yaml", "apikeys.json", "service-account-key.json", "key.json", "keys.json",
        "ssh_key", "gcp.service_account.key.json",
    ],
)  # fmt: skip
def test_a_secret_shaped_name_is_matched(name: str) -> None:
    assert is_secret_file_name(name)


@pytest.mark.parametrize(
    "name",
    ["tokenizer.py", "secretary.md", "README.md", "app.conf", "keyboard.md", "guide.md", "key-concepts.md", "key.md"],
)
def test_an_ordinary_name_is_not(name: str) -> None:
    assert not is_secret_file_name(name)
