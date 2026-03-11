"""
pipeline.py - Text to Blender v6.0.0
══════════════════════════════════════
Universelle hierarchische Pipeline:

Phase 0  : Klassifikation (1 LLM-Call)
             → Objekt-Typ, Maße, 2-8 Baugruppen mit rough_bounds
Phase 1x : Pro Baugruppe Einzelteile (N_assemblies LLM-Calls)
             → Symmetrie-Expansion: mirror_Y → 2x, radial_N → N×
Phase 2  : Pro Teil Bounds (sequenziell, max MAX_BOUNDS_PARTS)
Phase 3  : Pro convex_hull-Teil Pointcloud (max MAX_POINTCLOUD_PARTS)
Phase 4  : Mesh-Bau im Blender-Main-Thread (via bpy.app.timers!)
Phase 5  : Materialien (1 LLM-Call)
"""

import re, json, ast, math, threading
import bpy
from . import llm_client, mesh_builder, prompts, cache

# ── State ────────────────────────────────────────────────────────────────────
_lock  = threading.Lock()
_state = {
    "status":       "idle",
    "phase":        0,
    "phase_label":  "",
    "sub_index":    0,
    "sub_total":    0,
    "sub_queue":    [],

    "user_prompt":    "",
    "classification": {},
    "assemblies":     [],
    "all_parts":      [],
    "placed":         [],
    "final_parts":    [],

    "max_parts_per_assembly": 12,
    "max_bounds_parts":       40,
    "max_pointcloud_parts":   10,
    "detail":    "medium",
    "llm_model": "",
    "llm_host":  "",

    "log":          [],
    "last_error":   None,
    "pending_raw":  None,
    "pending_err":  None,
    "bounds_warnings": [],
}

DETAIL_POINTS = {"einfach": 8, "medium": 24, "hoch": 64}

# ── Logging ──────────────────────────────────────────────────────────────────

def _log(icon, text, phase=0, part=""):
    with _lock:
        _state["log"].append((icon, text))
        if len(_state["log"]) > 200:
            _state["log"] = _state["log"][-200:]
    cache.log(icon, text, phase=int(phase) if phase else 0, part=part)

def get_state():
    with _lock:
        return dict(_state)

def get_log_text():
    try:
        with open(cache.get_log_path(), "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

# ── JSON-Parsing ─────────────────────────────────────────────────────────────

def _parse_json(text):
    """Nutzt llm_client.extract_json für einheitliche JSON-Extraktion."""
    return json.loads(llm_client.extract_json(text))

# ── Pipeline starten / stoppen ───────────────────────────────────────────────

def start(prompt, model, host, detail="medium", project_dir="",
          max_parts_per_assembly=12, max_bounds_parts=40, max_pointcloud_parts=10):

    if project_dir:
        cache.set_project_dir(project_dir)
    cache.log_separator(f"TEXT TO BLENDER v6.0.0 | Prompt: {prompt}")
    _log("INFO",
         f"Limits: max_parts={max_parts_per_assembly}, "
         f"max_bounds={max_bounds_parts}, "
         f"max_pointclouds={max_pointcloud_parts}")

    with _lock:
        _state.update({
            "status": "running", "phase": 0, "phase_label": "Starte ...",
            "sub_index": 0, "sub_total": 0, "sub_queue": [],
            "user_prompt":    prompt,
            "classification": {}, "assemblies": [],
            "all_parts": [], "placed": [], "final_parts": [],
            "max_parts_per_assembly": max(1, int(max_parts_per_assembly)),
            "max_bounds_parts":       max(1, int(max_bounds_parts)),
            "max_pointcloud_parts":   max(0, int(max_pointcloud_parts)),
            "detail":    detail if detail in DETAIL_POINTS else "medium",
            "llm_model": model or "qwen2.5-coder:7b",
            "llm_host":  host  or "http://localhost:11434",
            "log": [], "last_error": None,
            "pending_raw": None, "pending_err": None,
            "bounds_warnings": [],
        })
    _run_phase()

def reset():
    with _lock:
        _state.update({
            "status": "idle", "phase": 0, "phase_label": "",
            "sub_index": 0, "sub_total": 0, "sub_queue": [],
            "user_prompt": "",
            "classification": {}, "assemblies": [],
            "all_parts": [], "placed": [], "final_parts": [],
            "log": [], "last_error": None,
            "pending_raw": None, "pending_err": None,
            "bounds_warnings": [],
        })
    _log("INFO", "Pipeline zurückgesetzt.")

# ── Phase-Dispatch ────────────────────────────────────────────────────────────

def _run_phase():
    with _lock:
        phase  = _state["phase"]
        model  = _state["llm_model"]
        host   = _state["llm_host"]
        prompt = _state["user_prompt"]
        clf    = dict(_state["classification"])
        asm    = list(_state["assemblies"])
        parts  = list(_state["all_parts"])
        placed = list(_state["placed"])
        queue  = list(_state["sub_queue"])
        idx    = _state["sub_index"]
        detail = _state["detail"]
        mpa    = _state["max_parts_per_assembly"]
        mbp    = _state["max_bounds_parts"]
        mpc    = _state["max_pointcloud_parts"]

    # Phase 0: Klassifikation
    if phase == 0:
        _call(prompts.PHASE_0_CLASSIFY,
              f'Erstelle ein 3D-Objekt: "{prompt}"',
              model, host, 0, "Klassifikation", "")

    # Phase 1x: Pro Baugruppe Einzelteile
    elif phase == 1:
        if idx < len(queue):
            asm_item = queue[idx]
            asm_name = asm_item.get("name", "?")
            rb       = asm_item.get("rough_bounds", clf.get("overall_bounds", []))
            user = (
                f'Objekt: "{prompt}"\n'
                f'Typ: {clf.get("object_type","?")} | '
                f'Kategorie: {clf.get("category","?")}\n'
                f'Gesamtmaße: L={clf.get("dimensions_m",{}).get("length","?")}m '
                f'B={clf.get("dimensions_m",{}).get("width","?")}m '
                f'H={clf.get("dimensions_m",{}).get("height","?")}m\n'
                f'Gesamtbounds: {clf.get("overall_bounds","?")}\n\n'
                f'=== Baugruppe: {asm_name} ===\n'
                f'Beschreibung: {asm_item.get("description","")}\n'
                f'Rolle: {asm_item.get("role","")}\n'
                f'Baugruppen-Bounds: {rb}\n\n'
                f'Erstelle die Einzelteile für diese Baugruppe.\n'
                f'LIMIT: maximal {mpa} Teile.\n'
                f'Nutze symmetry "mirror_Y" für links/rechts gespiegelte Teile.\n'
                f'Nutze symmetry "radial_N" für rotationssymmetrische Teile.'
            )
            _call(prompts.PHASE_1_ASSEMBLY_DETAIL, user,
                  model, host, 1,
                  f"Baugruppe: {asm_name} ({idx+1}/{len(queue)})",
                  asm_name)
        else:
            total = len(parts)
            _log("OK", f"Phase 1 fertig: {total} Teile in {len(asm)} Baugruppen.", phase=1)
            cache.log_parts_list(parts, phase=1)
            cache.save_step(1, {"parts": parts, "assemblies": asm})
            if total > mbp:
                _log("WARN", f"Teile-Limit: {total} > {mbp} → kürze.", phase=1)
                with _lock:
                    _state["all_parts"] = _state["all_parts"][:mbp]
            _advance(2)

    # Phase 2: Bounds pro Teil
    elif phase == 2:
        if idx < len(queue):
            part      = queue[idx]
            part_name = part.get("name", "?")
            asm_name  = part.get("_assembly", "")
            asm_item  = next((a for a in asm if a.get("name") == asm_name), {})
            rb        = asm_item.get("rough_bounds", clf.get("overall_bounds", []))
            same_asm  = [p for p in placed if p.get("_assembly") == asm_name]

            user = (
                f'Objekt: "{prompt}"\n'
                f'Gesamtbounds: {clf.get("overall_bounds","?")}\n'
                f'Koordinatensystem: X=Länge(vorne=+X), Y=Breite(rechts=+Y), Z=Höhe(Boden=0)\n\n'
                f'=== Baugruppe: {asm_name} ===\n'
                f'Baugruppen-Bounds: {rb}\n'
                f'Beschreibung: {asm_item.get("description","")}\n\n'
            )
            if same_asm:
                user += f'Bereits platzierte Teile ({len(same_asm)}):\n'
                for p in same_asm:
                    b = p.get("bounds", [])
                    user += f'  {p["name"]}: {[round(v,2) for v in b]}\n'
                user += "\n"
            user += (
                f'=== Platziere jetzt ===\n'
                f'Name:         {part_name}\n'
                f'Beschreibung: {part.get("description","")}\n'
                f'Methode:      {part.get("method","box")}\n'
                f'Symmetrie:    {part.get("symmetry","none")}\n\n'
                f'Bounds MÜSSEN innerhalb der Baugruppen-Bounds liegen: {rb}\n'
                f'Gib [xmin,xmax,ymin,ymax,zmin,zmax] in Metern an.'
            )
            _call(prompts.PHASE_2_BOUNDS, user,
                  model, host, 2,
                  f"Bounds: {part_name} ({idx+1}/{len(queue)})",
                  part_name)
        else:
            _log("OK", f"Phase 2 fertig: {len(placed)} Teile platziert.", phase=2)
            overall  = clf.get("overall_bounds", [])
            warnings = mesh_builder.validate_bounds_list(placed, overall, phase=2)
            with _lock:
                _state["bounds_warnings"] = warnings
            cache.log_parts_list(placed, phase=2)
            cache.save_step(2, {"parts": placed})
            _advance(3)

    # Phase 3: Pointclouds
    elif phase == 3:
        if idx < len(queue):
            part      = queue[idx]
            part_name = part.get("name", "?")
            bounds    = part.get("bounds", [])
            n         = DETAIL_POINTS.get(detail, 24)
            user = (
                f'Objekt: "{prompt}"\n'
                f'Koordinatensystem: X=Länge(vorne=+X), Y=Breite(rechts=+Y), Z=Höhe(Boden=0)\n\n'
                f'Teil: {part_name}\n'
                f'Beschreibung: {part.get("description","")}\n'
                f'Baugruppe: {part.get("_assembly","")}\n'
                f'Bounds: {[round(v,3) for v in bounds]}\n\n'
                f'Erstelle {n} Punkte die die Form von "{part_name}" beschreiben.\n'
                f'ALLE Punkte müssen strikt innerhalb der Bounds liegen.'
            )
            _call(prompts.PHASE_3_POINTCLOUD, user,
                  model, host, 3,
                  f"Pointcloud: {part_name} ({idx+1}/{len(queue)})",
                  part_name)
        else:
            _log("OK", "Phase 3 fertig.", phase=3)
            with _lock:
                fp = list(_state["final_parts"])
            cache.save_step(3, {"parts": fp})
            # Phase 4 MUSS im Main-Thread laufen!
            _schedule_phase_4()

    # Phase 5: Materialien
    elif phase == 5:
        with _lock:
            fp = list(_state["final_parts"])
        teil_namen = ", ".join(p.get("name","?") for p in fp[:30])
        _call(prompts.PHASE_5_MATERIALS,
              f'Objekt: "{prompt}"\n'
              f'Teile ({len(fp)}): {teil_namen}\n'
              f'Weise jedem Teil ein realistisches Material zu.',
              model, host, 5, "Materialien", "")

    else:
        _log("ERR", f"Unbekannte Phase: {phase}")
        with _lock:
            _state["status"] = "error"
            _state["last_error"] = f"Phase {phase} unbekannt"

# ── Phase 4: Mesh-Bau (MUSS im Main-Thread laufen!) ─────────────────────────

def _schedule_phase_4():
    """Registriert Phase 4 als bpy.app.timers → läuft sicher im Main-Thread."""
    _log("INFO", "Phase 4: Warte auf Blender Main-Thread ...")
    try:
        bpy.app.timers.register(_phase_4_main_thread, first_interval=0.05)
    except Exception as e:
        _log("ERR", f"Timer-Registrierung fehlgeschlagen: {e}")
        with _lock:
            _state["status"]     = "error"
            _state["last_error"] = str(e)

def _phase_4_main_thread():
    """Wird von bpy.app.timers im Main-Thread ausgeführt. Baut alle Meshes."""
    try:
        with _lock:
            placed    = list(_state["placed"])
            final_pts = list(_state["final_parts"])

        pts_lookup = {p["name"]: p for p in final_pts}
        merged     = [pts_lookup.get(p["name"], p) for p in placed]

        _log("OK", f"Phase 4: Baue {len(merged)} Meshes ...", phase=4)
        cache.log_parts_list(merged, phase=4)

        created = mesh_builder.build_final(merged)
        with _lock:
            _state["final_parts"] = merged
            _state["phase"]       = 5

        _log("OK", f"Phase 4: {len(created)} Objekte erstellt.", phase=4)
        cache.save_step(4, {"parts": merged})

        _advance(5)

    except Exception as e:
        _log("ERR", f"Phase 4: {e}", phase=4)
        with _lock:
            _state["status"]     = "error"
            _state["last_error"] = str(e)

    return None  # Timer nicht wiederholen

# ── Symmetrie-Expansion ───────────────────────────────────────────────────────

def _expand_symmetry(part: dict, asm_name: str) -> list:
    sym  = part.get("symmetry", "none")
    name = part.get("name", "Teil")
    base = dict(part)
    base["_assembly"]        = asm_name
    base["_symmetry_origin"] = name
    base["_symmetry_index"]  = 0

    if sym == "mirror_Y":
        left  = dict(base); left["name"]  = f"{name}_L"; left["_symmetry_index"] = 0
        right = dict(base); right["name"] = f"{name}_R"; right["_symmetry_index"] = 1
        return [left, right]

    if sym.startswith("radial_"):
        try:
            n = int(sym.split("_")[1])
            n = max(2, min(n, 12))
        except Exception:
            n = 4
        expanded = []
        for i in range(n):
            copy = dict(base)
            copy["name"]              = f"{name}_{i+1}"
            copy["_symmetry_index"]   = i
            copy["_radial_count"]     = n
            copy["_radial_angle_deg"] = round(360.0 / n * i, 1)
            expanded.append(copy)
        return expanded

    return [base]

def _apply_symmetry_to_bounds(part: dict, base_bounds: list) -> list:
    if not base_bounds or len(base_bounds) != 6:
        return base_bounds
    sym = part.get("symmetry", "none")
    idx = part.get("_symmetry_index", 0)
    b   = base_bounds

    if sym == "mirror_Y" and idx == 1:
        return [b[0], b[1], -b[3], -b[2], b[4], b[5]]

    if sym.startswith("radial_") and idx > 0:
        angle_deg = part.get("_radial_angle_deg",
                             360.0 / max(1, part.get("_radial_count", 4)) * idx)
        angle_rad = math.radians(angle_deg)
        cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
        cx = (b[0]+b[1])/2; cy = (b[2]+b[3])/2
        dx = (b[1]-b[0])/2; dy = (b[3]-b[2])/2
        nx = cx * cos_a - cy * sin_a
        ny = cx * sin_a + cy * cos_a
        return [nx-dx, nx+dx, ny-dy, ny+dy, b[4], b[5]]

    return base_bounds

# ── LLM-Aufruf ───────────────────────────────────────────────────────────────

def _call(system, user, model, host, phase, label, part):
    with _lock:
        _state["phase_label"] = label
        _state["status"]      = "running"
    _log("WAIT", f"Phase {phase}: {label}", phase=phase, part=part)
    ok = llm_client.generate_async(
        prompt=user, system_prompt=system,
        model=model, host=host,
        phase=phase, part_name=part,
        on_done=_on_done, timeout=180.0,
    )
    if not ok:
        _log("ERR", "LLM beschäftigt!", phase=phase)
        with _lock:
            _state["status"]     = "error"
            _state["last_error"] = "LLM busy"

def _on_done(raw, error):
    with _lock:
        _state["pending_raw"] = raw
        _state["pending_err"] = error
    try:
        bpy.app.timers.register(_process, first_interval=0.02)
    except Exception as e:
        cache.log(cache.LEVEL_ERROR, f"Timer-Fehler: {e}")
        with _lock:
            _state["status"]     = "error"
            _state["last_error"] = "Timer failed"

def _process():
    with _lock:
        raw  = _state.get("pending_raw")
        err  = _state.get("pending_err")
        ph   = _state["phase"]
        _state["pending_raw"] = None
        _state["pending_err"] = None

    if err:
        _log("ERR", f"LLM: {err}", phase=ph)
        with _lock:
            _state["status"]     = "error"
            _state["last_error"] = err
        return None
    if raw is None:
        _log("ERR", "Leeres Ergebnis.", phase=ph)
        with _lock:
            _state["status"]     = "error"
            _state["last_error"] = "Leere Antwort"
        return None

    fn = {0: _h0, 1: _h1, 2: _h2, 3: _h3, 5: _h5}.get(ph)
    if fn:
        try:
            fn(raw)
        except Exception as exc:
            _log("ERR", f"Ph{ph}: {exc}", phase=ph)
            with _lock:
                _state["status"]     = "error"
                _state["last_error"] = str(exc)
    return None

# ── Phase-Handler ─────────────────────────────────────────────────────────────

def _h0(raw):
    try:
        data = _parse_json(raw)
    except Exception as e:
        _log("WARN", f"Ph0 JSON: {e} → leere Klassifikation", phase=0)
        data = {}

    cache.log_json("Klassifikation", data, phase=0)
    cache.save_step(0, data)

    assemblies = data.get("assemblies", [])
    dim = data.get("dimensions_m", {})
    _log("OK",
         f"Klassifikation: {data.get('object_type','?')} | "
         f"Kategorie: {data.get('category','?')} | "
         f"L={dim.get('length','?')}m B={dim.get('width','?')}m H={dim.get('height','?')}m | "
         f"{len(assemblies)} Baugruppen",
         phase=0)

    # Baugruppen-Bounds visualisieren
    zones = [{"name": a["name"], "bounds": a.get("rough_bounds", [])}
             for a in assemblies if a.get("rough_bounds")]
    if zones:
        mesh_builder.visualize_zones(zones)

    with _lock:
        _state["classification"] = data
        _state["assemblies"]     = assemblies
        _state["all_parts"]      = []

    _advance(1)


def _h1(raw):
    with _lock:
        queue = list(_state["sub_queue"])
        idx   = _state["sub_index"]
        mpa   = _state["max_parts_per_assembly"]

    if idx >= len(queue):
        _advance(2)
        return

    asm_item = queue[idx]
    asm_name = asm_item.get("name", "?")

    try:
        data = _parse_json(raw)
    except Exception as e:
        _log("WARN", f"Ph1 '{asm_name}': {e} → übersprungen", phase=1, part=asm_name)
        with _lock:
            _state["sub_index"] += 1
        _run_phase()
        return

    raw_parts = data.get("parts", [])[:mpa]

    expanded = []
    for p in raw_parts:
        expanded.extend(_expand_symmetry(p, asm_name))

    _log("OK",
         f"Baugruppe '{asm_name}': {len(raw_parts)} Basis-Teile → {len(expanded)} nach Expansion",
         phase=1, part=asm_name)

    with _lock:
        _state["all_parts"].extend(expanded)
        _state["sub_index"] += 1

    _run_phase()


def _h2(raw):
    with _lock:
        queue  = list(_state["sub_queue"])
        idx    = _state["sub_index"]
        placed = list(_state["placed"])

    if idx >= len(queue):
        _advance(3)
        return

    current   = queue[idx]
    part_name = current.get("name", "?")
    sym       = current.get("symmetry", "none")
    sym_idx   = current.get("_symmetry_index", 0)
    origin    = current.get("_symmetry_origin", part_name)

    # Symmetrische Teile: Bounds spiegeln statt LLM befragen
    if sym_idx > 0 and (sym == "mirror_Y" or sym.startswith("radial_")):
        base = next(
            (p for p in placed
             if p.get("_symmetry_origin") == origin and p.get("_symmetry_index") == 0),
            None
        )
        if base and base.get("bounds"):
            mirrored     = _apply_symmetry_to_bounds(current, base["bounds"])
            placed_part  = dict(current)
            placed_part["bounds"] = mesh_builder.repair_bounds(mirrored, part_name)
            _log("OK",
                 f"Symmetrie-Kopie '{part_name}': {[round(v,2) for v in placed_part['bounds']]}",
                 phase=2, part=part_name)
            mesh_builder.build_placeholder(placed_part)
            with _lock:
                _state["placed"].append(placed_part)
                _state["sub_index"] += 1
            _run_phase()
            return

    # Normaler Bounds-Aufruf
    try:
        data   = _parse_json(raw)
        bounds = data.get("bounds")
    except Exception as e:
        _log("WARN", f"Ph2 '{part_name}': {e} → Fallback", phase=2, part=part_name)
        bounds = None

    norm = mesh_builder.normalize_bounds(bounds)
    if norm is None:
        norm = [-0.5, 0.5, -0.5, 0.5, 0.0, 1.0]
    b = mesh_builder.repair_bounds(norm, part_name)

    placed_part         = dict(current)
    placed_part["bounds"] = b
    _log("OK", f"Bounds '{part_name}': {[round(v,2) for v in b]}", phase=2, part=part_name)

    mesh_builder.build_placeholder(placed_part)
    with _lock:
        _state["placed"].append(placed_part)
        _state["sub_index"] += 1
    _run_phase()


def _h3(raw):
    with _lock:
        queue = list(_state["sub_queue"])
        idx   = _state["sub_index"]

    if idx >= len(queue):
        _advance(4)
        return

    current   = queue[idx]
    part_name = current.get("name", "?")
    bounds    = current.get("bounds", [-1,1,-1,1,0,2])

    try:
        data   = _parse_json(raw)
        points = data.get("points", [])
    except Exception as e:
        _log("WARN", f"Ph3 '{part_name}': {e}", phase=3, part=part_name)
        points = []

    valid = []
    for p in points:
        try:
            x = max(bounds[0], min(bounds[1], float(p[0])))
            y = max(bounds[2], min(bounds[3], float(p[1])))
            z = max(bounds[4], min(bounds[5], float(p[2])))
            valid.append([x, y, z])
        except Exception:
            pass

    cache.log_pointcloud(part_name, bounds, valid, phase=3)
    _log("OK", f"{len(valid)} Punkte", phase=3, part=part_name)

    finished         = dict(current)
    finished["points"] = valid
    with _lock:
        _state["final_parts"].append(finished)
        _state["sub_index"] += 1
    _run_phase()


def _h5(raw):
    try:
        data      = _parse_json(raw)
        materials = data.get("materials", [])
    except Exception as e:
        _log("WARN", f"Ph5: {e}", phase=5)
        materials = []

    applied = 0
    for m in materials:
        obj = bpy.data.objects.get(f"LLM_{m.get('name','')}")
        if obj and obj.type == "MESH":
            mesh_builder._apply_material(
                obj,
                m.get("color_rgba", [0.6, 0.6, 0.6, 1.0]),
                float(m.get("metallic",  0.0)),
                float(m.get("roughness", 0.5)),
            )
            applied += 1

    cache.save_step(5, data)
    _log("OK", f"Phase 5: {applied} Materialien zugewiesen.", phase=5)
    with _lock:
        _state["status"] = "done"
    _log("OK", "Pipeline abgeschlossen!")

# ── Phasenwechsel ─────────────────────────────────────────────────────────────

def _advance(next_phase):
    with _lock:
        _state["phase"]     = next_phase
        _state["sub_index"] = 0

        if next_phase == 1:
            q = list(_state["assemblies"])
            _state["sub_queue"] = q
            _state["sub_total"] = len(q)
            msg = f"Phase 1: {len(q)} Baugruppen werden konkretisiert."

        elif next_phase == 2:
            parts = list(_state["all_parts"])
            mbp   = _state["max_bounds_parts"]
            if len(parts) > mbp:
                parts = parts[:mbp]
                _state["all_parts"] = parts
            _state["sub_queue"] = parts
            _state["sub_total"] = len(parts)
            msg = f"Phase 2: {len(parts)} Teile erhalten Bounds (limit={mbp})."

        elif next_phase == 3:
            placed = list(_state["placed"])
            mpc    = _state["max_pointcloud_parts"]
            hull   = [p for p in placed if p.get("method") == "convex_hull"]
            if len(hull) > mpc:
                hull = hull[:mpc]
            _state["sub_queue"]   = hull
            _state["sub_total"]   = len(hull)
            _state["final_parts"] = []
            msg = f"Phase 3: {len(hull)} Pointclouds (limit={mpc})."

        elif next_phase == 5:
            msg = "Phase 5: Materialien werden zugewiesen."

        else:
            msg = f"→ Phase {next_phase}"

    _log("INFO", msg)

    # Phase 4 läuft über Timer, nicht direkt
    if next_phase == 4:
        _schedule_phase_4()
    else:
        _run_phase()
