"""Shared pytest fixtures + load the flat-layout plugin as `aap_hermes`.

Because aap-hermes uses Hermes's flat-plugin layout (``__init__.py`` at the
repo root, no nested ``aap_hermes/`` package), tests can't ``import aap_hermes``
the normal way. We replicate the importlib trick Hermes itself uses
(``spec_from_file_location`` with ``submodule_search_locations``) so:
    * ``from aap_hermes import register`` works
    * ``from aap_hermes.adapter import ...`` works (lazy submodule resolution)
    * Relative imports *inside* the plugin (``from .client import ...``) work
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_PKG_NAME = "aap_hermes"

if _PKG_NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        _PKG_NAME,
        _REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(_REPO_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {_PKG_NAME} from {_REPO_ROOT}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = _PKG_NAME
    sys.modules[_PKG_NAME] = module
    spec.loader.exec_module(module)




@pytest.fixture(autouse=True)
def aap_test_trust_root(monkeypatch):
    monkeypatch.setenv(
        "AAP_TRUST_LIST_PUBLIC_KEY_B64",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )


@pytest.fixture
def mock_hermes_module(monkeypatch):
    """Make `_hermes_compat` importable as if it were the real `hermes` package.

    Adapter code does ``from aap_hermes.adapter import AAPPlatformAdapter``;
    the adapter imports its base class lazily via a configurable import path
    so tests can swap in the compat module.
    """
    import _hermes_compat as compat_module
    monkeypatch.setitem(sys.modules, "hermes_adapter_base", compat_module)
    return compat_module
