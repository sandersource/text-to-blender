bl_info = {
    "name":        "Text to Blender",
    "author":      "Sander",
    "version":     (7, 0, 0),
    "blender":     (4, 0, 0),
    "location":    "View3D > Sidebar > LLM",
    "description": "Universeller Text-to-3D Generator: Meshes, Materialien, Animationen, Scripte",
    "category":    "3D View",
}

import bpy, traceback

_modules = {}

def _try_import(name):
    try:
        if   name == "cache":        from . import cache        as m
        elif name == "llm_client":   from . import llm_client   as m
        elif name == "prompts":      from . import prompts      as m
        elif name == "mesh_builder": from . import mesh_builder as m
        elif name == "pipeline":     from . import pipeline     as m
        elif name == "operators":    from . import operators    as m
        elif name == "panel":        from . import panel        as m
        else: return None
        return m
    except Exception as e:
        print(f"[TTB v7] Import '{name}': {e}")
        traceback.print_exc()
        return None

for _n in ("cache", "llm_client", "prompts", "mesh_builder", "pipeline", "operators", "panel"):
    _modules[_n] = _try_import(_n)

def register():
    for name in ("panel", "operators"):
        m = _modules.get(name)
        if m:
            try:
                m.register()
            except Exception as e:
                print(f"[TTB v7] register '{name}': {e}")
                traceback.print_exc()
    cm = _modules.get("cache")
    if cm:
        try:
            cm.log_separator("Text to Blender v7.0.0")
            cm.log(cm.LEVEL_INFO, "Bereit.")
        except Exception:
            pass
    print("[Text to Blender] v7.0.0 registriert.")

def unregister():
    for name in ("operators", "panel"):
        m = _modules.get(name)
        if m:
            try:
                m.unregister()
            except Exception:
                pass
    print("[Text to Blender] v7.0.0 deregistriert.")

if __name__ == "__main__":
    register()
