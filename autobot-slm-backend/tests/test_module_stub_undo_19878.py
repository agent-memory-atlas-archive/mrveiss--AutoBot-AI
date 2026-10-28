# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Patching an attribute of a conftest module stub must leave that attribute usable (#19878).

pytest 9.2's ``monkeypatch.setattr`` records ``target.__dict__.get(name)`` as the old
value and undoes by deleting the name when that was absent. On a plain ``MagicMock``
stub that delete made the name raise ``AttributeError`` for the rest of the process
(``tests/_module_stub.py``). This guard fails if a stub goes back to being a plain
``MagicMock`` or stops surviving a patch/undo, so the class cannot come back.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from _slm_module_stub import ModuleStub

# Stubs the conftest installs and tests patch attributes of; the parametrization below
# additionally covers every ModuleStub present at collection.
_CANARIES = ("services.playbook_executor", "services.reconciler", "models.database")


def _stubs() -> list[str]:
    return sorted({*_CANARIES, *(n for n, m in sys.modules.items() if isinstance(m, ModuleStub))})


@pytest.mark.parametrize("name", _stubs())
def test_a_stub_survives_monkeypatch_setattr_and_undo(name: str) -> None:
    stub = sys.modules[name]
    assert isinstance(stub, ModuleStub), f"{name} is {type(stub).__mro__[1].__name__}, not a ModuleStub"
    attr = "attribute_patched_by_a_test_19878"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(stub, attr, lambda: "patched")
        assert getattr(stub, attr)() == "patched"
        mp.setattr(stub, attr, lambda: "patched again")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(f"{name}.{attr}", lambda: "by dotted path")  # the form that failed in CI
    with patch(f"{name}.{attr}", return_value=1):
        assert getattr(stub, attr)() == 1

    assert callable(getattr(stub, attr))  # still auto-created on demand, not remembered as deleted
    delattr(stub, attr)
