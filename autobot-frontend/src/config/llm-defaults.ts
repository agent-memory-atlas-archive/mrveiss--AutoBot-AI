// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
/**
 * Literal fallback model names, used when no `VITE_LLM_*_MODEL` env var is set (#15502).
 *
 * Mirrors `DEFAULT_LLM_MODEL` (the quality tier, #2553) and `DEFAULT_EMBEDDING_MODEL` in
 * `autobot_shared/ssot_config.py`. Declared once here so `ssot-config.ts` holds no model-name
 * literal; `src/config/__tests__/ssot-parity.spec.ts` and
 * `repo_tests/model_tier_single_definition_15502_test.py` assert it still matches the Python.
 */
export const LLM_DEFAULTS = {
  defaultModel: 'qwen3.5:9b',
  embeddingModel: 'nomic-embed-text:latest',
} as const;
