bl_info = {
    "name":        "Text to Blender",
    "author":      "Sander",
    "version":     (6, 0, 0),
    "blender":     (4, 0, 0),
    "location":    "View3D > Sidebar > LLM",
    "description": "Universeller Text-to-3D Generator: Meshes, Materialien, Animationen, Scripte",
    "category":    "3D View",
}

copilot/fix-llm-tab-visibility-issue
import bpy, traceback, importlib

# ---------------------------------------------------------------------------
# Sub-module reload support (for "Reload Scripts" / re-enable after update)
# ---------------------------------------------------------------------------
_MODULE_NAMES = ("cache", "llm_client", "prompts", "mesh_builder", "pipeline", "operators", "panel")

# Collect any import errors so the fallback panel can display them.
_import_errors: list[str] = []
_modules: dict = {}

def _load_modules() -> None:
    """Import (or reload) every sub-module and populate ``_modules``."""
    _import_errors.clear()
    _modules.clear()

    import sys
    for name in _MODULE_NAMES:
        full_name = f"{__name__}.{name}"
        try:
            if full_name in sys.modules:
                m = importlib.reload(sys.modules[full_name])
            else:
                m = importlib.import_module(f".{name}", package=__name__)
            _modules[name] = m
        except Exception:
            msg = f"[Text to Blender] Import '{name}' failed:\n{traceback.format_exc()}"
            print(msg)
            _import_errors.append(msg)

import bpy
import importlib
import sys
import traceback

# ── Module Management ─────────────────────────────────────────────────────────

_package = __package__
_module_names = ("cache", "llm_client", "prompts", "mesh_builder", "pipeline", "operators", "panel")
_modules = {}
_import_errors = []

def _load_modules():
    """Import (or reload) all sub-modules, collecting errors."""
    global _modules, _import_errors
    _modules = {}
    _import_errors = []

    for name in _module_names:
        full = f"{_package}.{name}"
        try:
            if full in sys.modules:
                _modules[name] = importlib.reload(sys.modules[full])
            else:
                _modules[name] = importlib.import_module(f".{name}", _package)
        except Exception as e:
            err_msg = f"[Text to Blender] Import '{name}' failed: {e}\n{traceback.format_exc()}"
            print(err_msg)
            _import_errors.append(f"Import '{name}': {e}")
main
            _modules[name] = None

_load_modules()

copilot/fix-llm-tab-visibility-issue
# ---------------------------------------------------------------------------
# Fallback error panel – shown when panel/operators could not be loaded
# ---------------------------------------------------------------------------
class TTB_PT_ErrorPanel(bpy.types.Panel):


# ── Fallback Error Panel ─────────────────────────────────────────────────────

class TTB_PT_ErrorPanel(bpy.types.Panel):
    """Fallback panel shown when the addon fails to load properly."""
main
    bl_label       = "Text to Blender"
    bl_idname      = "TTB_PT_error"
    bl_space_type  = "VIEW_3D"
    bl_region_type = "UI"
    bl_category    = "LLM"

    def draw(self, context):
        layout = self.layout
 copilot/fix-llm-tab-visibility-issue
        layout.label(text="Addon failed to load – see details below:", icon="ERROR")
        for err in _import_errors:
            for line in err.splitlines():
                layout.label(text=line)

# ---------------------------------------------------------------------------
# register / unregister
# ---------------------------------------------------------------------------
_fallback_registered = False


def register():
    global _fallback_registered
    _fallback_registered = False

    panel_ok = False
    for name in ("panel", "operators"):
        m = _modules.get(name)
        if m is None:
            print(f"[Text to Blender] Skipping register of '{name}': module not loaded.")
            continue
        try:
            m.register()
            if name == "panel":
                panel_ok = True
            print(f"[Text to Blender] Registered '{name}' successfully.")
        except Exception:
            msg = f"[Text to Blender] register '{name}' failed:\n{traceback.format_exc()}"
            print(msg)
            _import_errors.append(msg)

    # If the real panel did not register, show the fallback error panel instead.
    if not panel_ok:
        try:
            bpy.utils.register_class(TTB_PT_ErrorPanel)
            _fallback_registered = True
            print("[Text to Blender] Fallback error panel registered.")
        except Exception:
            print(f"[Text to Blender] Could not register fallback panel:\n{traceback.format_exc()}")


        layout.label(text="ADDON LOAD ERROR", icon="ERROR")
        layout.separator()

        if _import_errors:
            box = layout.box()
            box.label(text="Failed imports:", icon="CANCEL")
            for err in _import_errors:
                for line in err.splitlines():
                    row = box.row()
                    row.scale_y = 0.6
                    row.label(text=line[:90])
        else:
            layout.label(text="Unknown error during registration.")

        layout.separator()
        layout.label(text="Check Blender System Console for details.")
        layout.label(text="Window > Toggle System Console")

_error_panel_registered = False


# ── Register / Unregister ─────────────────────────────────────────────────────

def register():
    global _error_panel_registered

    panel_ok = False
    operators_ok = False

    # Try registering panel module
    pm = _modules.get("panel")
    if pm:
        try:
            pm.register()
            panel_ok = True
            print("[Text to Blender] panel registered.")
        except Exception as e:
            err_msg = f"Register 'panel': {e}"
            print(f"[Text to Blender] {err_msg}")
            traceback.print_exc()
            _import_errors.append(err_msg)
    else:
        print("[Text to Blender] panel module not available.")

    # Try registering operators module
    om = _modules.get("operators")
    if om:
        try:
            om.register()
            operators_ok = True
            print("[Text to Blender] operators registered.")
        except Exception as e:
            err_msg = f"Register 'operators': {e}"
            print(f"[Text to Blender] {err_msg}")
            traceback.print_exc()
            _import_errors.append(err_msg)
    else:
        print("[Text to Blender] operators module not available.")

    # If panel failed, register the fallback error panel
    if not panel_ok:
        try:
            bpy.utils.register_class(TTB_PT_ErrorPanel)
            _error_panel_registered = True
            print("[Text to Blender] Fallback error panel registered.")
        except Exception as e2:
            print(f"[Text to Blender] Could not register error panel: {e2}")
            traceback.print_exc()

    # Log startup
main
    cm = _modules.get("cache")
    if cm:
        try:
            cm.log_separator("Text to Blender v6.0.0")
            cm.log(cm.LEVEL_INFO, "Bereit." if panel_ok else "FEHLER beim Laden!")
        except Exception:
            pass

 copilot/fix-llm-tab-visibility-issue
    print("[Text to Blender] v6.0.0 registriert.")

    status = "OK" if (panel_ok and operators_ok) else "WITH ERRORS"
    print(f"[Text to Blender] v6.0.0 registriert ({status}).")
    if _import_errors:
        print("[Text to Blender] Errors encountered:")
        for e in _import_errors:
            print(f"  - {e}")

            main


def unregister():
copilot/fix-llm-tab-visibility-issue
    global _fallback_registered

    # Unregister normal modules (reverse order).
    for name in ("operators", "panel"):
        m = _modules.get(name)
        if m:
            try:
                m.unregister()
            except Exception:
                pass

    # Unregister fallback panel if it was registered.
    if _fallback_registered:
        try:
            bpy.utils.unregister_class(TTB_PT_ErrorPanel)
        except Exception:
            pass
        _fallback_registered = False

    # Clean up scene property if present.
    if hasattr(bpy.types.Scene, "ttb_props"):
        try:
            del bpy.types.Scene.ttb_props
        except Exception:
            pass


    global _error_panel_registered

    # Unregister operators
    om = _modules.get("operators")
    if om:
        try:
            om.unregister()
        except Exception:
            pass

    # Unregister panel
    pm = _modules.get("panel")
    if pm:
        try:
            pm.unregister()
        except Exception:
            pass

    # Unregister fallback error panel
    if _error_panel_registered:
        try:
            bpy.utils.unregister_class(TTB_PT_ErrorPanel)
            _error_panel_registered = False
        except Exception:
            pass

    # Clean up scene property
    try:
        del bpy.types.Scene.ttb_props
    except Exception:
        pass

 main
    print("[Text to Blender] v6.0.0 deregistriert.")


if __name__ == "__main__":
    register()