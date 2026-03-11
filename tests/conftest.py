"""
conftest.py - Test-Setup für Text to Blender
═════════════════════════════════════════════
Stellt die reinen Python-Module (cache, prompts, llm_client) als importierbares
Paket bereit, ohne dass Blender installiert sein muss.

Strategie:
  1. Stub-Module für bpy / bmesh / mathutils in sys.modules eintragen
  2. Ein virtuelles Paket "text_to_blender" anlegen, das auf das Repo-Verzeichnis zeigt
  3. Die pure-Python-Submodule (cache, prompts, llm_client) vorladen,
     damit relative Imports (from . import cache) funktionieren
"""

import importlib.util
import os
import sys
import types

# ── 1. Stub-Blender-Module ────────────────────────────────────────────────────

_bpy = types.ModuleType("bpy")
_bpy.types   = types.SimpleNamespace()
_bpy.props   = types.SimpleNamespace()
_bpy.utils   = types.SimpleNamespace()
_bpy.data    = types.SimpleNamespace()
_bpy.context = types.SimpleNamespace(scene=types.SimpleNamespace())
_bpy.app     = types.SimpleNamespace(timers=types.SimpleNamespace())
sys.modules.setdefault("bpy",      _bpy)
sys.modules.setdefault("bmesh",    types.ModuleType("bmesh"))

_mathutils = types.ModuleType("mathutils")
_mathutils.Vector = list            # simple stand-in
sys.modules.setdefault("mathutils", _mathutils)

# ── 2. Virtuelles Paket registrieren ─────────────────────────────────────────

PKG_NAME = "text_to_blender"
PKG_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_pkg = types.ModuleType(PKG_NAME)
_pkg.__path__    = [PKG_DIR]
_pkg.__package__ = PKG_NAME
_pkg.__file__    = os.path.join(PKG_DIR, "__init__.py")
sys.modules[PKG_NAME] = _pkg


def _load_submodule(name: str):
    """Lädt ein Submodul aus dem Repo-Verzeichnis in den richtigen Paket-Kontext."""
    full_name = f"{PKG_NAME}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    fpath = os.path.join(PKG_DIR, f"{name}.py")
    spec  = importlib.util.spec_from_file_location(full_name, fpath)
    mod   = importlib.util.module_from_spec(spec)
    mod.__package__ = PKG_NAME
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    setattr(_pkg, name, mod)
    return mod


# Reihenfolge beachten: cache hat keine Abhängigkeiten → zuerst laden
_load_submodule("cache")
_load_submodule("prompts")
_load_submodule("llm_client")
