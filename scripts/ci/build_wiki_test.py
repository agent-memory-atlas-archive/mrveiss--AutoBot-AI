# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""scripts/ci/build_wiki.py renders the wiki from docs/ (#19278)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("build_wiki_19278", Path(__file__).with_name("build_wiki.py"))
bw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bw)

URL = "https://forge.example/org/repo"
SHA = "a" * 40  # a commit-shaped id; a real-looking hex literal trips detect-secrets


def test_relative_links_point_at_the_repository_at_the_commit():
    md = "[a](../user-guide/x.md) [b](./y.md#part) [c](https://example.com) [d](#local) [e](/abs)"
    out = bw.rewrite_links(md, "docs/user/getting-started.md", SHA, URL)
    assert f"[a]({URL}/blob/{SHA}/docs/user-guide/x.md)" in out
    assert f"[b]({URL}/blob/{SHA}/docs/user/y.md#part)" in out
    assert "[c](https://example.com)" in out and "[d](#local)" in out and "[e](/abs)" in out


def test_api_paths_are_read_from_the_generated_types():
    types = 'export interface paths {\n    "/api/health": {\n    "/api/knowledge/search": {\n        "/api/nested": {\n'
    assert bw.api_paths(types) == ["/api/health", "/api/knowledge/search"]


def test_the_api_page_names_only_served_paths():
    paths = ["/api/health", "/api/knowledge/search"]
    page = bw.render_api(paths, SHA, URL)
    assert bw.check_api_page(page, paths) == []
    assert bw.check_api_page(page + "\n- `/api/v1/made-up`\n", paths) == ["/api/v1/made-up"]
    assert "## /api/knowledge" in page and "Generated" in page


def test_every_page_is_built_with_its_source_header(tmp_path):
    written = bw.build(tmp_path, SHA, URL)
    assert {"Home", "API", "Configuration", "Deployment"} <= set(written)
    for name in written:
        text = (tmp_path / f"{name}.md").read_text(encoding="utf-8")
        assert text.startswith("> **Generated** from") and SHA[:12] in text, name


def test_every_manifest_source_exists():
    import yaml

    pages = yaml.safe_load(bw.MANIFEST.read_text(encoding="utf-8"))["pages"]
    missing = [s for s in pages.values() if s != "api" and not (bw.ROOT / s).is_file()]
    assert not missing, f"manifest names missing sources: {missing}"


def test_the_repo_url_comes_from_actions_env_or_the_origin_remote(monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://forge.example")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    assert bw.repo_url() == "https://forge.example/org/repo"
    monkeypatch.delenv("GITHUB_SERVER_URL")
    assert bw.repo_url().startswith("https://") and not bw.repo_url().endswith(".git")
