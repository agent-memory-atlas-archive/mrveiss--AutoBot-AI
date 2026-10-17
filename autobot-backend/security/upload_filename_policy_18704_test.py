# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Every upload route applies the one strict filename policy (#18704).

Before, ``conversation_files.is_safe_file`` had no path-component check, so a
conversation upload named ``a/b.txt`` passed while the general file API
refused it, and its deny-list lacked ``httpd.conf`` / ``nginx.conf``.
"""

import pytest

from api import conversation_files, files
from security import upload_filename_policy

VALIDATORS = [conversation_files.is_safe_file, files.is_safe_file]
IDS = ["conversation_files", "files"]


def test_both_routes_use_the_one_policy():
    assert conversation_files.is_safe_file is upload_filename_policy.is_safe_file
    assert files.is_safe_file is upload_filename_policy.is_safe_file
    assert conversation_files.ALLOWED_EXTENSIONS is files.ALLOWED_EXTENSIONS


@pytest.mark.parametrize("validator", VALIDATORS, ids=IDS)
@pytest.mark.parametrize("name", ["a/b.txt", "../x.txt", "/etc/passwd.txt", "..\\x.txt", "x\\y.txt", "dir/../notes.md"])
def test_a_name_with_a_path_component_is_refused(validator, name):
    assert validator(name) is False


@pytest.mark.parametrize("validator", VALIDATORS, ids=IDS)
@pytest.mark.parametrize("name", ["httpd.conf", "nginx.conf", ".env", "web.config"])
def test_the_extended_deny_list_applies_to_both(validator, name):
    assert validator(name) is False


@pytest.mark.parametrize("validator", VALIDATORS, ids=IDS)
@pytest.mark.parametrize("name", ["report.xls", "report.xlsx", "notes.odt", "slides.pptx", "app.conf", "a.pdf"])
def test_the_reconciled_allow_list_accepts_both_routes_formats(validator, name):
    assert validator(name) is True
