"""
panel.py - Text to Blender v7.0.0
════════════════════════════════════
Vollständiges UI mit Tabs: Mesh | Material | Animation | Script
Zoom-In Pipeline v7.0.0 — neue Fortschrittsanzeige mit Sub-Phasen.
"""
import bpy, os, traceback
from . import pipeline, llm_client, cache


class TTB_Properties(bpy.types.PropertyGroup):

    prompt: bpy.props.StringProperty(
        name="Prompt",
        default="",
        description="Beschreibe das gewünschte 3D-Objekt"
    )
    model: bpy.props.StringProperty(
        name="Modell",
        default="qwen2.5-coder:7b"
    )
    host: bpy.props.StringProperty(
        name="Host",
        default="http://localhost:11434"
    )
    project_dir: bpy.props.StringProperty(
        name="Projektordner",
        default=os.path.join(os.path.expanduser("~"), "text_to_blender", "default_project"),
        subtype="DIR_PATH"
    )
    detail_level: bpy.props.EnumProperty(
        name="Detailgrad",
        items=[
            ("einfach", "Einfach",  "8 Punkte / Teil  — schnell"),
            ("medium",  "Medium",   "24 Punkte / Teil — empfohlen"),
            ("hoch",    "Hoch",     "64 Punkte / Teil — langsam"),
        ],
        default="medium"
    )
    max_parts_per_assembly: bpy.props.IntProperty(
        name="Max. Teile / Baugruppe",
        description="Maximale Anzahl Einzelteile pro Baugruppe",
        default=12, min=2, max=30, soft_max=20
    )
    max_bounds_parts: bpy.props.IntProperty(
        name="Max. Bounds-Teile",
        description="Maximale Gesamtzahl Teile die Bounds erhalten (= LLM-Calls für Phase 2)",
        default=40, min=5, max=200, soft_max=60
    )
    max_pointcloud_parts: bpy.props.IntProperty(
        name="Max. Pointcloud-Teile",
        description="Maximale Anzahl Teile mit Pointcloud (convex_hull). 0 = keine.",
        default=10, min=0, max=60, soft_max=20
    )
    active_module: bpy.props.EnumProperty(
        name="Modul",
        items=[
            ("mesh",      "Mesh",      "Geometrie-Pipeline"),
            ("material",  "Material",  "Material generieren"),
            ("animation", "Animation", "Animation generieren"),
            ("script",    "Script",    "Script generieren"),
        ],
        default="mesh"
    )
    show_settings: bpy.props.BoolProperty(name="Einstellungen", default=True)
    show_log:      bpy.props.BoolProperty(name="Log",           default=True)
    show_project:  bpy.props.BoolProperty(name="Projektordner", default=True)
    show_limits:   bpy.props.BoolProperty(name="Limits",        default=False)


class TTB_PT_MainPanel(bpy.types.Panel):
    bl_label       = "Text to Blender"
    bl_idname      = "TTB_PT_main"
    bl_space_type  = "VIEW_3D"
    bl_region_type = "UI"
    bl_category    = "LLM"

    def draw(self, context):
        try:
            self._safe_draw(context, self.layout)
        except Exception:
            tb = self.layout
            tb.label(text="ADDON FEHLER — siehe Konsole")
            col = tb.box().column(align=True)
            col.scale_y = 0.6
            for line in traceback.format_exc().splitlines()[-8:]:
                col.label(text=line[:88])

    def _safe_draw(self, context, layout):
        props = getattr(context.scene, "ttb_props", None)
        if props is None:
            layout.label(text="FEHLER: ttb_props fehlt.")
            return

        state, running = {}, False
        try:
            state   = pipeline.get_state()
            running = llm_client.is_busy() or state.get("status") == "running"
        except Exception:
            pass

        # Status-Zeile
        row = layout.row(align=True)
        row.label(text="Ollama LLM")
        row.label(text="[aktiv]" if running else "[bereit]")
        layout.separator(factor=0.3)

        # Tab-Leiste
        layout.row(align=True).prop(props, "active_module", expand=True)
        layout.separator(factor=0.4)

        mod = props.active_module
        if   mod == "mesh":      self._draw_mesh(layout, props, state, running)
        elif mod == "material":  self._draw_code_tab(
            layout, props, running,
            title="Material",
            examples=[
                '"Rostiges Metall"',
                '"Leuchtendes Glas"',
                '"Verwittertes Holz"',
                '"Matte Keramik"',
            ],
            op_id="ttb.generate_material",
            op_label="Material generieren",
            hint="→ wird auf aktives Objekt angewendet"
        )
        elif mod == "animation": self._draw_code_tab(
            layout, props, running,
            title="Animation",
            examples=[
                '"Rotation um Z-Achse"',
                '"Auf- und Abbewegung"',
                '"Pulsierendes Skalieren"',
                '"Pendelnde Drehung"',
            ],
            op_id="ttb.generate_animation",
            op_label="Animation generieren",
            hint="→ wird auf aktives Objekt angewendet"
        )
        elif mod == "script":    self._draw_code_tab(
            layout, props, running,
            title="Script",
            examples=[
                '"Verteile 20 Objekte im Kreis"',
                '"Erstelle ein Array-Modifier"',
                '"Alle Objekte auf Boden setzen"',
                '"Wireframe-Material für alle"',
            ],
            op_id="ttb.generate_script",
            op_label="Script generieren",
            hint="→ wird direkt ausgeführt (Ergebnis im Text-Editor)"
        )

        layout.separator(factor=0.5)
        self._draw_limits(layout, props, running)
        layout.separator(factor=0.3)
        self._draw_project(layout, props)
        layout.separator(factor=0.3)
        self._draw_settings(layout, props)

    # ── Mesh-Tab ──────────────────────────────────────────────────────────────

    def _draw_mesh(self, layout, props, state, running):
        box = layout.box()
        box.label(text="Mesh Pipeline")

        # Beispiele
        sub = box.column(align=True)
        sub.scale_y = 0.7
        sub.label(text="Beispiele:")
        for ex in [
            '"Erstelle ein Holzhaus"',
            '"Erstelle einen Hubschrauber"',
            '"Erstelle einen Eiffelturm"',
            '"Erstelle ein Windrad"',
            '"Erstelle einen Drachen"',
            '"Erstelle ein Raumschiff"',
        ]:
            sub.label(text=ex)
        box.separator(factor=0.3)

        # Prompt-Eingabe
        box.label(text="Dein Prompt:")
        row = box.row(align=True)
        row.prop(props, "prompt", text="")
        row.operator("ttb.reset_prompt", text="", icon="X")

        box.separator(factor=0.2)
        row = box.row(align=True)
        row.label(text="Detailgrad:")
        row.prop(props, "detail_level", text="")
        box.separator(factor=0.3)

        # Status
        status = state.get("status", "idle")
        phase  = state.get("phase", 0) or 0
        si     = int(state.get("sub_index", 0) or 0)
        st     = int(state.get("sub_total",  0) or 0)

        # Start-Button
        btn = box.row(align=True)
        btn.scale_y = 1.4
        if not running and status in ("idle", "done", "error"):
            btn.operator("ttb.start_pipeline", text="Pipeline starten")
            if status in ("done", "error"):
                btn.operator("ttb.reset_pipeline", text="", icon="X")
        else:
            btn.enabled = False
            lbl = state.get("phase_label", "Starte ...")
            if st > 0:
                lbl += f" ({si}/{st})"
            btn.operator("ttb.start_pipeline", text=lbl)

        box.operator("ttb.reset_scene", text="Szene leeren")

        # Fortschritt / Ergebnis
        if status == "running":
            self._draw_progress(box, state, phase, si, st)
        elif status == "done":
            box.label(text="✓ Pipeline abgeschlossen!")
            n = len(state.get("final_parts", []))
            if n:
                box.label(text=f"{n} Objekte erstellt.")
            warnings = state.get("bounds_warnings", [])
            if warnings:
                wb = box.box()
                wb.alert = True
                wb.label(text=f"{len(warnings)} Bounds-Warnungen:")
                wc = wb.column(align=True)
                wc.scale_y = 0.65
                for w in warnings[:4]:
                    wc.label(text=w[:72])
        elif status == "error":
            row = box.row()
            row.alert = True
            row.label(text="✗ Fehler!")
            err = state.get("last_error", "")
            if err:
                eb = box.box()
                eb.scale_y = 0.65
                for line in str(err)[:300].split("\n")[:5]:
                    eb.label(text=line[:80])

        self._draw_log(box, state, props)

    def _draw_progress(self, layout, state, phase, si, st):
        pb = layout.box()
        sub_phase = state.get("sub_phase")
        # Zeige neue Unterphasen im Fortschrittsbalken
        phase_map = [
            ("0a", "Typ"),   ("0b", "Gross"), ("1a", "Teile"),
            ("1b", "Untert"), ("2", "Bounds"),
            ("3", "Punkte"), ("4", "Mesh"),   ("5", "Mat"),
        ]
        # Aktive Phase ermitteln (kombiniert phase + sub_phase)
        if phase == 0:
            active_key = "0b" if sub_phase == "b" else "0a"
        elif phase == 1:
            active_key = "1b" if sub_phase == "b" else "1a"
        else:
            active_key = str(phase)

        row = pb.row()
        row.scale_y = 0.45
        for ph_key, lbl in phase_map:
            row.label(text=f"[{lbl}]" if active_key == ph_key else lbl)
        pb.label(text=state.get("phase_label", "..."))
        if st > 0:
            row2 = pb.row()
            row2.scale_y = 0.4
            shown = min(st, 14)
            for i in range(shown):
                row2.label(text="[X]" if i < si else "[ ]")
            if st > 14:
                row2.label(text=f"+{st-14}")
        sc = pb.column(align=True)
        sc.scale_y = 0.65
        asm   = state.get("assemblies", [])
        parts = state.get("all_parts",  [])
        placed = state.get("placed",    [])
        retry  = state.get("bounds_retry_count", 0)
        if asm:    sc.label(text=f"{len(asm)} Baugruppen erkannt")
        if parts:  sc.label(text=f"{len(parts)} Teile expandiert")
        if placed: sc.label(text=f"{len(placed)} Teile platziert")
        if retry:  sc.label(text=f"Retry {retry}/2 ...")

    # ── Code-Tab (Material / Animation / Script) ──────────────────────────────

    def _draw_code_tab(self, layout, props, running,
                       title, examples, op_id, op_label, hint):
        box = layout.box()
        box.label(text=title)
        sub = box.column(align=True)
        sub.scale_y = 0.7
        sub.label(text="Beispiele:")
        for ex in examples:
            sub.label(text=ex)
        box.separator(factor=0.3)
        box.label(text="Dein Prompt:")
        row = box.row(align=True)
        row.prop(props, "prompt", text="")
        row.operator("ttb.reset_prompt", text="", icon="X")
        r = box.row()
        r.scale_y = 1.4
        r.enabled = not running
        r.operator(op_id, text=op_label)
        box.label(text=hint)

    # ── Limits-Box ────────────────────────────────────────────────────────────

    def _draw_limits(self, layout, props, running):
        box = layout.box()
        row = box.row()
        row.prop(props, "show_limits",
                 icon="TRIA_DOWN" if props.show_limits else "TRIA_RIGHT",
                 icon_only=True, emboss=False)
        row.label(text="Pipeline-Limits")
        if props.show_limits:
            col = box.column(align=True)
            col.enabled = not running
            col.prop(props, "max_parts_per_assembly")
            col.separator(factor=0.3)
            col.prop(props, "max_bounds_parts")
            col.separator(factor=0.3)
            col.prop(props, "max_pointcloud_parts")
            col.separator(factor=0.4)
            hint = box.box()
            hint.scale_y = 0.65
            n = props.max_bounds_parts
            m = props.max_pointcloud_parts
            hint.label(text=f"Phase 0a+0b: 2 Calls (Typ + Groesse)")
            hint.label(text=f"Phase 1a+1b: ~{1 + len([])+1} Calls (Teile)")
            hint.label(text=f"Phase 2: ~{n} Calls (Bounds + Retry)")
            hint.label(text=f"Phase 3: ~{m} Calls (Pointclouds)")
            hint.label(text=f"Phase 5: 1 Call (Materialien)")
            hint.label(text=f"→ gesamt ca. {n + m + 6} LLM-Calls")

    # ── Log-Box ───────────────────────────────────────────────────────────────

    def _draw_log(self, layout, state, props):
        log = state.get("log", [])
        if not log:
            return
        layout.separator(factor=0.2)
        lb = layout.box()
        hdr = lb.row()
        hdr.prop(props, "show_log",
                 icon="TRIA_DOWN" if props.show_log else "TRIA_RIGHT",
                 icon_only=True, emboss=False)
        hdr.label(text="Log:")
        hdr.operator("ttb.copy_log", text="Kopieren")
        if props.show_log:
            col = lb.column(align=True)
            col.scale_y = 0.65
            icon_map = {
                "OK  ": "CHECKMARK", "ERR ": "ERROR",
                "WAIT": "TIME",      "INFO": "INFO",
                "WARN": "ERROR",
            }
            for ik, txt in log[-15:]:
                col.label(
                    text=(txt[:68] + "...") if len(txt) > 68 else txt,
                    icon=icon_map.get(ik, "DOT")
                )
            lb.separator(factor=0.2)
            sc = lb.column(align=True)
            sc.scale_y = 0.65
            sc.label(text=f"Log:    {cache.get_log_path()}")
            sc.label(text=f"Cache:  {cache.get_cache_dir()}")
            for path, label in [
                (cache.get_parts_list_path(),  "Teile:  "),
                (cache.get_joints_list_path(), "Joints: "),
            ]:
                if os.path.exists(path):
                    sc.label(text=f"{label}{path}")

    # ── Projektordner ─────────────────────────────────────────────────────────

    def _draw_project(self, layout, props):
        box = layout.box()
        row = box.row()
        row.prop(props, "show_project",
                 icon="TRIA_DOWN" if props.show_project else "TRIA_RIGHT",
                 icon_only=True, emboss=False)
        row.label(text="Projektordner")
        if props.show_project:
            box.prop(props, "project_dir", text="")
            r = box.row(align=True)
            r.operator("ttb.set_project_dir",  text="Setzen")
            r.operator("ttb.open_project_dir", text="Öffnen")
            box.label(
                text="Vorhanden." if os.path.isdir(props.project_dir)
                else "Wird beim Start erstellt."
            )
            row2 = box.row()
            row2.alert = True
            row2.operator("ttb.clear_project_folder", text="Ordner leeren", icon="TRASH")

    # ── Einstellungen ─────────────────────────────────────────────────────────

    def _draw_settings(self, layout, props):
        box = layout.box()
        row = box.row()
        row.prop(props, "show_settings",
                 icon="TRIA_DOWN" if props.show_settings else "TRIA_RIGHT",
                 icon_only=True, emboss=False)
        row.label(text="Einstellungen")
        if props.show_settings:
            col = box.column(align=True)
            col.label(text="Ollama Verbindung:")
            col.prop(props, "host",  text="Host")
            col.prop(props, "model", text="Modell")
            col.separator(factor=0.3)
            col.operator("ttb.test_connection", text="Verbindung testen")
            box.separator(factor=0.2)
            sub = box.column(align=True)
            sub.scale_y = 0.65
            sub.label(text="ollama serve")
            sub.label(text=f"ollama pull {props.model}")
            sub.label(text="Strg+Z für Undo")


# ── Registrierung ────────────────────────────────────────────────────────────

_classes = (TTB_Properties, TTB_PT_MainPanel)

def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ttb_props = bpy.props.PointerProperty(type=TTB_Properties)

def unregister():
    try:
        del bpy.types.Scene.ttb_props
    except Exception:
        pass
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
