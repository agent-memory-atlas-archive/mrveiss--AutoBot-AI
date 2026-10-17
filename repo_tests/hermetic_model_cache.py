# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Point every test session at an empty, offline HuggingFace cache (#16994).

CI runners have no model cache, so a test that constructs a model-loading
class (``MultiModalProcessor`` -> ``VisionProcessor``) fails its load fast and
moves on. A developer machine that has run the product has a populated
``~/.cache/huggingface``: the same test then loads the real weights and
``verify_cached_model`` SHA-256s every multi-GB file -- about 43 s per test
(measured 2026-10-17) -- and the online/offline outcome differs from CI's.
The suite's verdict depended on the machine, not the code.

Loaded through ``pytest_plugins`` in the rootdir conftest, so it runs before
any test module can import ``huggingface_hub``, which reads these variables
into module constants at import time. Each process (xdist worker) gets its own
directory, removed at the end of the session.
"""

import os
import shutil
import tempfile

#: The variables huggingface_hub and transformers read for their cache roots.
CACHE_ENV_VARS = ("HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE")
#: Offline as well as empty: an empty cache with network access downloads the
#: weights on first load (16 GB in a measured run), which is worse, not hermetic.
OFFLINE_ENV_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")

_CACHE_ROOT = tempfile.mkdtemp(prefix="autobot-test-hf-")
os.environ["HF_HOME"] = _CACHE_ROOT
os.environ["HF_HUB_CACHE"] = os.path.join(_CACHE_ROOT, "hub")
os.environ["TRANSFORMERS_CACHE"] = os.path.join(_CACHE_ROOT, "hub")
for _var in OFFLINE_ENV_VARS:
    os.environ[_var] = "1"


def cache_root() -> str:
    """The empty cache directory this session's tests see."""
    return _CACHE_ROOT


def pytest_unconfigure(config):
    shutil.rmtree(_CACHE_ROOT, ignore_errors=True)
