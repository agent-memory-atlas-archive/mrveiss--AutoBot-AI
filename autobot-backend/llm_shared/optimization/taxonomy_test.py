# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Equivalence of the shared taxonomies to the tables they replaced (#13049)."""

import pytest

from .kv_cache import KVCacheConfig
from .pipeline import PipelineConfig
from .taxonomy import Compression, Dtype

#: The kv_cache._DTYPE_BYTES literal as it stood before #13049, frozen here.
_OLD_DTYPE_BYTES = {"fp16": 2, "bf16": 2, "fp32": 4, "float16": 2, "bfloat16": 2, "float32": 4}
_OLD_COMPRESSIONS = ("4bit", "8bit", "none")
_BAD_DTYPES = ("int8", "", "FP16", "fp64", "float64", " fp16", None)


def _kv(dtype):
    return KVCacheConfig(num_layers=1, num_heads=1, head_dim=1, max_seq_len=1, dtype=dtype)


@pytest.mark.parametrize(("spelling", "nbytes"), sorted(_OLD_DTYPE_BYTES.items()))
def test_every_old_spelling_keeps_its_width_and_stays_as_given(spelling, nbytes):
    cfg = _kv(spelling)
    assert cfg.dtype == spelling and cfg.dtype_bytes == nbytes
    assert Dtype.parse(spelling).nbytes == nbytes


@pytest.mark.parametrize("bad", _BAD_DTYPES)
def test_every_old_rejection_is_still_rejected_with_the_same_message(bad):
    expected = f"Unsupported dtype '{bad}'. Choose from: {sorted(_OLD_DTYPE_BYTES)}"
    with pytest.raises(ValueError) as exc:
        _kv(bad)
    assert str(exc.value) == expected


def test_the_stored_values_are_unchanged():
    assert [d.value for d in Dtype] == ["fp16", "bf16", "fp32"]
    assert [d.torch_name for d in Dtype] == ["float16", "bfloat16", "float32"]
    assert [c.value for c in Compression] == ["4bit", "8bit", "none"]


@pytest.mark.parametrize("mode", _OLD_COMPRESSIONS)
def test_pipeline_accepts_every_mode_layer_inference_accepts(mode):
    assert PipelineConfig(model_name="m", compression=mode).compression == mode


def test_contrast_pair_pipeline_rejects_at_the_field_the_caller_set():
    PipelineConfig(model_name="m", compression="8bit")
    with pytest.raises(ValueError, match=r"compression must be one of \['4bit', '8bit', 'none'\], got 'fp4'"):
        PipelineConfig(model_name="m", compression="fp4")
