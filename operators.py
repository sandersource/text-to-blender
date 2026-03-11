"""
operators.py - Text to Blender v7.0.0
"""
import bpy, os, re, subprocess
from . import pipeline, llm_client, mesh_builder, cache


class TTB_OT_StartPipeline(bpy.types.Operator):
    """Startet die universelle Text-to-Blender Pipeline."""
    bl_idname = "ttb.start_pipeline"
    bl_label  = "Pipeline starten"

    def execute(self, context):
        props = getattr(context.scene, "ttb_props", None)
        if not props:
            self.report({"ERROR"}, "ttb_props fehlt.")
            return {"CANCELLED"}
        if not props.prompt.strip():
            self.report({"ERROR"}, "Bitte einen Prompt eingeben!")
            return {"CANCELLED"}
        if llm_client.is_busy() or pipeline.get_state().get("status") == "running":
            self.report({"WARNING"}, "Pipeline läuft bereits.")
            return {"CANCELLED"}

        # Verbindung prüfen
        ok, msg = llm_client.check_connection(props.host)
        if not ok:
            self.report({"ERROR"}, f"Ollama nicht erreichbar: {msg}")
            return {"CANCELLED"}

        pipeline.start(
            prompt=props.prompt,
            model=props.model,
            host=props.host,
            detail=props.detail_level,
            project_dir=props.project_dir,
            max_parts_per_assembly=props.max_parts_per_assembly,
            max_bounds_parts=props.max_bounds_parts,
            max_pointcloud_parts=props.max_pointcloud_parts,
        )
        bpy.app.timers.register(self._poll, first_interval=0.5)
        return {"FINISHED"}

    def _poll(self):
        try:
            state = pipeline.get_state()
        except Exception:
            return None
        for w in bpy.context.window_manager.windows:
            for a in w.screen.areas:
                if a.type == "VIEW_3D":
                    a.tag_redraw()
        return 0.5 if state.get("status") in ("running", "waiting") else None


class TTB_OT_ResetPipeline(bpy.types.Operator):
    bl_idname = "ttb.reset_pipeline"
    bl_label  = "Status zurücksetzen"

    def execute(self, context):
        pipeline.reset()
        return {"FINISHED"}


class TTB_OT_ResetPrompt(bpy.types.Operator):
    bl_idname = "ttb.reset_prompt"
    bl_label  = "Prompt leeren"

    def execute(self, context):
        props = getattr(context.scene, "ttb_props", None)
        if props:
            props.prompt = ""
        return {"FINISHED"}


class TTB_OT_ResetScene(bpy.types.Operator):
    bl_idname = "ttb.reset_scene"
    bl_label  = "Szene leeren"

    def execute(self, context):
        mesh_builder.clear_llm_objects()
        pipeline.reset()
        cache.clear_cache()
        return {"FINISHED"}


class TTB_OT_ClearProjectFolder(bpy.types.Operator):
    """Löscht alle Logs, Cache- und Raw-Dateien im Projektordner."""
    bl_idname    = "ttb.clear_project_folder"
    bl_label     = "Projektordner leeren"
    bl_description = "Löscht alle Logs, Cache- und Raw-Dateien"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        path = cache.get_project_dir()
        if not os.path.isdir(path):
            self.report({"WARNING"}, f"Ordner nicht gefunden: {path}")
            return {"CANCELLED"}
        deleted = 0
        for sub in ("cache", "raw"):
            sp = os.path.join(path, sub)
            if os.path.isdir(sp):
                for fn in os.listdir(sp):
                    try:
                        os.remove(os.path.join(sp, fn))
                        deleted += 1
                    except Exception:
                        pass
        for fn in os.listdir(path):
            fp = os.path.join(path, fn)
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                    deleted += 1
                except Exception:
                    pass
        cache._ensure_dirs()
        cache.log_separator("Projektordner geleert")
        self.report({"INFO"}, f"{deleted} Dateien gelöscht.")
        return {"FINISHED"}


class TTB_OT_SetProjectDir(bpy.types.Operator):
    bl_idname = "ttb.set_project_dir"
    bl_label  = "Ordner setzen"

    def execute(self, context):
        props = getattr(context.scene, "ttb_props", None)
        if not props:
            return {"CANCELLED"}
        path = props.project_dir.strip()
        if not path:
            self.report({"ERROR"}, "Kein Pfad angegeben.")
            return {"CANCELLED"}
        try:
            os.makedirs(path, exist_ok=True)
            cache.set_project_dir(path)
            self.report({"INFO"}, f"Projektordner: {path}")
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class TTB_OT_OpenProjectDir(bpy.types.Operator):
    bl_idname = "ttb.open_project_dir"
    bl_label  = "Ordner öffnen"

    def execute(self, context):
        path = cache.get_project_dir()
        os.makedirs(path, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(path)
            elif hasattr(os, "uname") and os.uname().sysname == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.report({"WARNING"}, str(e))
        return {"FINISHED"}


class TTB_OT_TestConnection(bpy.types.Operator):
    bl_idname = "ttb.test_connection"
    bl_label  = "Verbindung testen"

    def execute(self, context):
        props = getattr(context.scene, "ttb_props", None)
        if not props:
            return {"CANCELLED"}
        ok, msg = llm_client.check_connection(props.host)
        self.report({"INFO"} if ok else {"ERROR"}, msg)
        return {"FINISHED"}


class TTB_OT_CopyLog(bpy.types.Operator):
    bl_idname = "ttb.copy_log"
    bl_label  = "Log kopieren"

    def execute(self, context):
        text = pipeline.get_log_text()
        if not text:
            self.report({"WARNING"}, "Log leer.")
            return {"CANCELLED"}
        context.window_manager.clipboard = text
        self.report({"INFO"}, "Log kopiert.")
        return {"FINISHED"}


# ── Code-Operatoren (Material / Animation / Script) ──────────────────────────

def _make_code_op(idname, label, system):
    class _Op(bpy.types.Operator):
        bl_idname = idname
        bl_label  = label

        def execute(self, context):
            props = getattr(context.scene, "ttb_props", None)
            if not props or not props.prompt.strip():
                self.report({"ERROR"}, "Prompt leer!")
                return {"CANCELLED"}
            if llm_client.is_busy():
                self.report({"WARNING"}, "LLM beschäftigt.")
                return {"CANCELLED"}

            ok, msg = llm_client.check_connection(props.host)
            if not ok:
                self.report({"ERROR"}, f"Ollama nicht erreichbar: {msg}")
                return {"CANCELLED"}

            def on_done(raw, err):
                if err or not raw:
                    cache.log(cache.LEVEL_ERROR, f"{label}: {err}")
                    return
                m    = re.search(r"```python\s*(.*?)\s*```", raw, re.S | re.I)
                code = m.group(1).strip() if m else raw.strip()

                def _run():
                    try:
                        exec(compile(code, f"<{idname}>", "exec"), {"bpy": bpy})
                    except Exception as e:
                        cache.log(cache.LEVEL_ERROR, f"{label}: {e}")
                        t = bpy.data.texts.new("TTB_Error")
                        t.write(f"# Fehler: {e}\n\n{code}")
                    return None

                bpy.app.timers.register(_run, first_interval=0.01)

            llm_client.generate_async(
                prompt=props.prompt,
                system_prompt=system,
                model=props.model,
                host=props.host,
                phase=0,
                part_name=idname.split(".")[1],
                on_done=on_done,
                timeout=120.0,
            )
            return {"FINISHED"}

    _Op.__name__ = idname.replace(".", "_").upper()
    return _Op


TTB_OT_GenerateMaterial = _make_code_op(
    "ttb.generate_material", "Material generieren",
    "Du bist ein Blender-Material-Experte. "
    "Erstelle ein Principled BSDF Material für bpy.context.active_object. "
    "Antworte NUR mit ```python...``` Code-Block."
)

TTB_OT_GenerateAnimation = _make_code_op(
    "ttb.generate_animation", "Animation generieren",
    "Du bist ein Blender-Animations-Experte. "
    "Erstelle Keyframe-Animationen für bpy.context.active_object. "
    "Antworte NUR mit ```python...``` Code-Block."
)

TTB_OT_GenerateScript = _make_code_op(
    "ttb.generate_script", "Script generieren",
    "Du bist ein Blender-Python-Experte. "
    "Erstelle ein vollständiges bpy-Script das die Aufgabe löst. "
    "Antworte NUR mit ```python...``` Code-Block."
)


# ── Registrierung ────────────────────────────────────────────────────────────

_classes = (
    TTB_OT_StartPipeline,
    TTB_OT_ResetPipeline,
    TTB_OT_ResetPrompt,
    TTB_OT_ResetScene,
    TTB_OT_ClearProjectFolder,
    TTB_OT_SetProjectDir,
    TTB_OT_OpenProjectDir,
    TTB_OT_TestConnection,
    TTB_OT_CopyLog,
    TTB_OT_GenerateMaterial,
    TTB_OT_GenerateAnimation,
    TTB_OT_GenerateScript,
)

def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
