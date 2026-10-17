# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Tests see an empty HuggingFace cache, whatever the machine has (#16994)."""

import os
from pathlib import Path

from repo_tests import hermetic_model_cache


def test_every_cache_variable_points_inside_the_session_root():
    root = Path(hermetic_model_cache.cache_root())
    for var in hermetic_model_cache.CACHE_ENV_VARS:
        assert Path(os.environ[var]).is_relative_to(root), var


def test_huggingface_hub_resolved_the_session_cache_at_import():
    from huggingface_hub import constants

    assert Path(constants.HF_HUB_CACHE).is_relative_to(Path(hermetic_model_cache.cache_root()))


def test_the_session_cache_holds_no_models():
    hub = Path(os.environ["HF_HUB_CACHE"])
    assert not hub.exists() or not list(hub.glob("models--*"))


def test_the_real_user_cache_is_not_the_one_in_use():
    user_cache = Path.home() / ".cache" / "huggingface"
    assert not Path(os.environ["HF_HOME"]).is_relative_to(user_cache)


def test_model_hubs_are_offline_so_nothing_is_downloaded():
    for var in hermetic_model_cache.OFFLINE_ENV_VARS:
        assert os.environ[var] == "1", var
