# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""No hosted provider module writes its own default-model literal (#15501).

Each provider carried one; two went stale for years (#18210). The table in
autobot_shared/ssot_hosted_models.py is the one place a hosted default is written. This
reads every string literal in the provider modules and fails on one that equals a model name
from the table or from autobot_shared/ssot_constants.py.
"""

from __future__ import annotations

import ast

from repo_tests._paths import repo_root

_PROVIDERS = ["openai", "anthropic", "groq", "mistral", "vertexai", "openrouter", "huggingface", "bedrock", "bootstrap"]
_VENDOR_PREFIXES = ("OPENAI_", "ANTHROPIC_", "GOOGLE_", "GROQ_", "MISTRAL_", "DEEPSEEK_")


def _model_names() -> set[str]:
    from autobot_shared.ssot_hosted_models import HOSTED_PROVIDER_DEFAULT_MODELS

    names = {m for models in HOSTED_PROVIDER_DEFAULT_MODELS.values() for m in models.values()}
    tree = ast.parse((repo_root() / "autobot_shared" / "ssot_constants.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            if any(isinstance(t, ast.Name) and t.id.startswith(_VENDOR_PREFIXES) for t in node.targets):
                names.add(node.value.value)
    from autobot_shared.ssot_hosted_models import HUGGINGFACE_KNOWN_CHAT_MODELS

    names.update(HUGGINGFACE_KNOWN_CHAT_MODELS)
    return names


def _literals(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    doc_nodes = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    return [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes
    ]


def _offenders(sources: dict[str, str], names: set[str]) -> list[str]:
    return [
        f"{path}:{line}: {value!r}" for path, src in sources.items() for line, value in _literals(src) if value in names
    ]


def test_no_provider_module_writes_a_default_model_literal():
    names = _model_names()
    assert len(names) > 30, "the model-name set did not load"
    root = repo_root() / "autobot-backend" / "llm_shared" / "providers"
    sources = {f"{p}.py": (root / f"{p}.py").read_text(encoding="utf-8") for p in _PROVIDERS}
    sources["provider_registry.py"] = (root.parent / "provider_registry.py").read_text(encoding="utf-8")
    offenders = _offenders(sources, names)
    assert not offenders, "use autobot_shared.ssot_hosted_models.hosted_default_model (#15501):\n  " + "\n  ".join(
        offenders
    )


def test_the_detector_catches_a_planted_literal_and_skips_docstrings():
    planted = '"""Uses "gpt-4o-mini" in prose."""\nDEFAULT = "gpt-4o-mini"\n'
    assert _offenders({"x.py": planted}, {"gpt-4o-mini"}) == ["x.py:2: 'gpt-4o-mini'"]
