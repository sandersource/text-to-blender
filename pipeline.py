"""
pipeline.py - Text to Blender v7.0.0
══════════════════════════════════════
Universelle hierarchische Pipeline v7.0.0:

Phase 0a : WAS ist es? (Typ, Kategorie, Komplexität) — 1 Call
Phase 0b : Wie GROSS? (L×B×H + overall_bounds) — 1 Call
Phase 1a : Welche HAUPTBAUGRUPPEN? (Namen + rough_bounds) — 1 Call
Phase 1b : Pro Baugruppe Einzelteile (N_assemblies LLM-Calls)
              → Symmetrie-Expansion nur für mirror_Y/radial_N Teile
Phase 2  : Pro Teil Bounds (sequenziell, max max_bounds_parts)
              → Mit globalem Spatial-Context (ASCII-Sketch aller platzierten Teile)
              → Kompakter Kontext-Summary statt roher JSON-Dumps
              → Bounds-Validierung mit bis zu 2 Retry-Versuchen
              → Auto-Platzierung wenn alle Retries fehlschlagen
Phase 3  : Pro convex_hull-Teil Pointcloud (max max_pointcloud_parts)
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

    "max_parts_per_assembly": 8,
    "max_bounds_parts":       25,
    "max_pointcloud_parts":   10,
    "detail":    "medium",
    "llm_model": "",
    "llm_host":  "",

    "log":          [],
    "last_error":   None,
    "pending_raw":  None,
    "pending_err":  None,
    "bounds_warnings": [],
    "_ph2_retry_count": 0,
    "_ph0_step":        0,   # 0=0a(WAS), 1=0b(GROESSE), 2=1a(BAUGRUPPEN)
}

DETAIL_POINTS = {"einfach": 8, "medium": 24, "hoch": 64}

# ── Bounds-Validierung: Schwellwerte ─────────────────────────────────────────
MAX_BOUNDS_RETRIES       = 2    # Maximale Anzahl Retry-Versuche für Phase 2
MAX_PART_VOLUME_RATIO    = 0.90 # Teil darf max. 90% des Baugruppen-Volumens belegen
MAX_OVERLAP_THRESHOLD    = 0.50 # Teile mit >50% Überlappung gelten als ungültig
DEFAULT_PART_SIZE_RATIO  = 0.25 # Standard-Teilgröße: 25% der Baugruppe (X/Y)
DEFAULT_PART_SIZE_RATIO_Z = 0.30 # Standard-Teilgröße: 30% der Baugruppe (Z)
AUTO_PLACE_MIN_SIZE      = 0.05 # Minimale Teilgröße bei Auto-Platzierung (m)
AUTO_PLACE_Y_MARGIN      = 0.10 # Y-Achsen-Rand bei Auto-Platzierung (10% pro Seite)
MAX_ASSEMBLIES           = 6    # Maximale Anzahl Baugruppen (Phase 1a)
MAX_OTHER_PARTS_DISPLAY  = 8    # Maximale Anzahl anderer Teile im Phase-2-Kontext

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
          max_parts_per_assembly=8, max_bounds_parts=25, max_pointcloud_parts=10):

    if project_dir:
        cache.set_project_dir(project_dir)
    cache.log_separator(f"TEXT TO BLENDER v7.0.0 | Prompt: {prompt}")
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
            "_ph2_retry_count": 0,
            "_ph0_step":        0,
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
            "_ph2_retry_count": 0,
            "_ph0_step":        0,
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
        ph0_step = _state.get("_ph0_step", 0)

    # ── Phase 0: Klassifikation (3 Sub-Schritte: 0a → 0b → 1a) ───────────────
    if phase == 0:
        if ph0_step == 0:
            # Phase 0a: WAS ist das Objekt?
            _call(prompts.PHASE_0A_WHAT,
                  f'Beschreibe: "{prompt}"',
                  model, host, 0, "0a: Typ + Kategorie", "")

        elif ph0_step == 1:
            # Phase 0b: Wie GROSS ist es? (kompakter Kontext aus 0a)
            user = (
                f'Objekt: "{prompt}"\n'
                f'Typ: {clf.get("object_type","?")} | '
                f'Kategorie: {clf.get("category","?")}\n'
                f'Bestimme jetzt die realistischen Abmessungen und Bounds.'
            )
            _call(prompts.PHASE_0B_SIZE, user,
                  model, host, 0, "0b: Größe + Bounds", "")

        elif ph0_step == 2:
            # Phase 1a: Welche Haupt-Baugruppen?
            dim = clf.get("dimensions_m", {})
            user = (
                f'Objekt: "{prompt}"\n'
                f'Typ: {clf.get("object_type","?")} | '
                f'Kategorie: {clf.get("category","?")}\n'
                f'Größe: L={dim.get("length","?")}m '
                f'B={dim.get("width","?")}m '
                f'H={dim.get("height","?")}m\n'
                f'Overall Bounds: {clf.get("overall_bounds","?")}\n\n'
                f'Zerlege dieses Objekt in 2-{MAX_ASSEMBLIES} logische Hauptbaugruppen.\n'
                f'LIMIT: maximal {MAX_ASSEMBLIES} Baugruppen.'
            )
            _call(prompts.PHASE_1A_MAIN_PARTS, user,
                  model, host, 0, "1a: Hauptbaugruppen", "")

        else:
            _log("ERR", f"Unbekannter ph0_step: {ph0_step}")
            with _lock:
                _state["status"] = "error"
                _state["last_error"] = f"ph0_step {ph0_step} unbekannt"

    # ── Phase 1b: Pro Baugruppe Einzelteile ──────────────────────────────────
    elif phase == 1:
        if idx < len(queue):
            asm_item = queue[idx]
            asm_name = asm_item.get("name", "?")
            rb       = asm_item.get("rough_bounds", clf.get("overall_bounds", []))
            dim      = clf.get("dimensions_m", {})
            user = (
                f'Objekt: "{prompt}"\n'
                f'Typ: {clf.get("object_type","?")} | '
                f'Kategorie: {clf.get("category","?")}\n'
                f'Größe: L={dim.get("length","?")}m '
                f'B={dim.get("width","?")}m '
                f'H={dim.get("height","?")}m\n\n'
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
                  f"1b: Teile von {asm_name} ({idx+1}/{len(queue)})",
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

    # ── Phase 2: Bounds pro Teil ──────────────────────────────────────────────
    elif phase == 2:
        if idx < len(queue):
            part      = queue[idx]
            part_name = part.get("name", "?")
            asm_name  = part.get("_assembly", "")
            asm_item  = next((a for a in asm if a.get("name") == asm_name), {})

            user = _build_ph2_user_prompt(part, asm_item, clf, placed, prompt)
            _call(prompts.PHASE_2_BOUNDS, user,
                  model, host, 2,
                  f"Bounds: {part_name} ({idx+1}/{len(queue)})",
                  part_name)
        else:
            _log("OK", f"Phase 2 fertig: {len(placed)} Teile platziert.", phase=2)
            overall  = clf.get("overall_bounds", [])
            warnings = mesh_builder.validate_bounds_list(placed, overall, phase=2)
            # Spatial distribution check
            mesh_builder.check_spatial_distribution(placed, overall, phase=2)
            with _lock:
                _state["bounds_warnings"] = warnings
            cache.log_parts_list(placed, phase=2)
            cache.save_step(2, {"parts": placed})
            _advance(3)

    # ── Phase 3: Pointclouds ──────────────────────────────────────────────────
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

    # ── Phase 5: Materialien ──────────────────────────────────────────────────
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

# ── Phase-2-Hilfsfunktionen ───────────────────────────────────────────────────

def _bounds_overlap_pct(ba: list, bb: list):
    """Gibt (pct_a, pct_b) zurück — den Überlappungsanteil relativ zu Volumen a und b."""
    if len(ba) != 6 or len(bb) != 6:
        return 0.0, 0.0
    ox = max(0.0, min(ba[1], bb[1]) - max(ba[0], bb[0]))
    oy = max(0.0, min(ba[3], bb[3]) - max(ba[2], bb[2]))
    oz = max(0.0, min(ba[5], bb[5]) - max(ba[4], bb[4]))
    ov = ox * oy * oz
    if ov <= 0:
        return 0.0, 0.0
    va = max(1e-9, (ba[1]-ba[0]) * (ba[3]-ba[2]) * (ba[5]-ba[4]))
    vb = max(1e-9, (bb[1]-bb[0]) * (bb[3]-bb[2]) * (bb[5]-bb[4]))
    return ov / va, ov / vb


def _validate_ph2_bounds(bounds: list, asm_bounds: list, placed: list):
    """
    Prüft ob Phase-2-Bounds plausibel sind.
    Gibt (is_invalid: bool, reason: str) zurück.
    """
    if not bounds or len(bounds) != 6:
        return True, "Bounds fehlen oder ungültiges Format"

    if asm_bounds and len(asm_bounds) == 6:
        # Prüfe ob Bounds identisch mit Baugruppen-Bounds sind
        if all(abs(bounds[i] - asm_bounds[i]) < 0.01 for i in range(6)):
            return True, f"Bounds identisch mit Baugruppen-Bounds {[round(v,2) for v in asm_bounds]}"
        # Prüfe ob Teil größer als die Baugruppe ist
        dx_b = max(0.0, bounds[1] - bounds[0])
        dy_b = max(0.0, bounds[3] - bounds[2])
        dz_b = max(0.0, bounds[5] - bounds[4])
        dx_a = max(0.001, asm_bounds[1] - asm_bounds[0])
        dy_a = max(0.001, asm_bounds[3] - asm_bounds[2])
        dz_a = max(0.001, asm_bounds[5] - asm_bounds[4])
        vol_b = dx_b * dy_b * dz_b
        vol_a = dx_a * dy_a * dz_a
        if vol_a > 0 and vol_b / vol_a > MAX_PART_VOLUME_RATIO:
            return True, f"Bounds zu groß ({vol_b/vol_a*100:.0f}% der Baugruppe)"

    # Prüfe starke Überlappung mit bereits platzierten Teilen
    for p in placed:
        pb = p.get("bounds", [])
        if len(pb) != 6:
            continue
        pct_a, pct_b = _bounds_overlap_pct(bounds, pb)
        if pct_a > MAX_OVERLAP_THRESHOLD or pct_b > MAX_OVERLAP_THRESHOLD:
            return True, (
                f"Starke Überlappung mit '{p.get('name','?')}' "
                f"({pct_a*100:.0f}%/{pct_b*100:.0f}%)"
            )

    return False, ""


def _auto_place_in_assembly(part: dict, asm_bounds: list, placed: list) -> list:
    """
    Fallback: Platziert ein Teil automatisch in einem freien Bereich der Baugruppe.
    Teilt die Baugruppe in ein Raster und wählt die am wenigsten belegte Zelle.
    """
    if not asm_bounds or len(asm_bounds) != 6:
        return [-0.5, 0.5, -0.5, 0.5, 0.0, 1.0]

    xmn, xmx, ymn, ymx, zmn, zmx = asm_bounds
    dx = xmx - xmn
    dy = ymx - ymn
    dz = zmx - zmn

    # Schätze sinnvolle Teilgröße: ca. 25-30% der Baugruppe pro Achse
    part_dx = max(dx * DEFAULT_PART_SIZE_RATIO,   AUTO_PLACE_MIN_SIZE)
    part_dy = max(dy * DEFAULT_PART_SIZE_RATIO,   AUTO_PLACE_MIN_SIZE)
    part_dz = max(dz * DEFAULT_PART_SIZE_RATIO_Z, AUTO_PLACE_MIN_SIZE)

    # Teile die X-Achse in Slots auf
    n_slots = max(1, int(dx / part_dx))
    best_slot = 0
    best_overlap = float("inf")

    for slot in range(n_slots):
        sx = xmn + slot * (dx / n_slots)
        ex = sx + part_dx
        candidate = [sx, min(ex, xmx),
                     ymn + dy * AUTO_PLACE_Y_MARGIN, ymx - dy * AUTO_PLACE_Y_MARGIN,
                     zmn, min(zmn + part_dz, zmx)]
        total_overlap = 0.0
        for p in placed:
            pb = p.get("bounds", [])
            if len(pb) != 6:
                continue
            pct_a, _ = _bounds_overlap_pct(candidate, pb)
            total_overlap += pct_a
        if total_overlap < best_overlap:
            best_overlap = total_overlap
            best_slot = slot

    sx = xmn + best_slot * (dx / max(1, n_slots))
    ex = sx + part_dx
    result = [sx, min(ex, xmx),
              ymn + dy * AUTO_PLACE_Y_MARGIN, ymx - dy * AUTO_PLACE_Y_MARGIN,
              zmn, min(zmn + part_dz, zmx)]
    return result


def _build_ascii_sketch(placed: list, asm_bounds: list, width: int = 40, height: int = 8) -> str:
    """
    Erzeugt eine einfache ASCII-Draufsicht (X=horizontal, Y=vertikal) der
    bereits platzierten Teile innerhalb der Baugruppen-Bounds.
    """
    if not asm_bounds or len(asm_bounds) != 6 or not placed:
        return ""

    xmn, xmx = asm_bounds[0], asm_bounds[1]
    ymn, ymx = asm_bounds[2], asm_bounds[3]
    dx = max(0.001, xmx - xmn)
    dy = max(0.001, ymx - ymn)

    grid = [["." for _ in range(width)] for _ in range(height)]

    legend = []
    chars  = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    for i, p in enumerate(placed[:len(chars)]):
        b = p.get("bounds", [])
        if len(b) != 6:
            continue
        ch = chars[i % len(chars)]
        legend.append(f"{ch}={p.get('name','?')}")
        # Mappe X→ spalte, Y→ zeile (Y-Achse invertiert für Top-Down)
        c0 = int((b[0] - xmn) / dx * (width  - 1))
        c1 = int((b[1] - xmn) / dx * (width  - 1))
        r0 = int((1.0 - (b[3] - ymn) / dy) * (height - 1))
        r1 = int((1.0 - (b[2] - ymn) / dy) * (height - 1))
        c0, c1 = max(0, min(c0, width-1)),  max(0, min(c1, width-1))
        r0, r1 = max(0, min(r0, height-1)), max(0, min(r1, height-1))
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                grid[r][c] = ch

    lines  = ["+" + "-" * width + "+"]
    for row in grid:
        lines.append("|" + "".join(row) + "|")
    lines.append("+" + "-" * width + "+")
    if legend:
        lines.append("Legende: " + ", ".join(legend[:12]))
    return "\n".join(lines)


def _build_ph2_user_prompt(part: dict, asm_item: dict, clf: dict,
                           all_placed: list, prompt: str,
                           retry_info: str = "") -> str:
    """Baut den vollständigen User-Prompt für Phase 2 (inkl. globalem Spatial-Context)."""
    part_name = part.get("name", "?")
    asm_name  = part.get("_assembly", "")
    rb        = asm_item.get("rough_bounds", clf.get("overall_bounds", []))
    dim       = clf.get("dimensions_m", {})

    # Kompakter Kontext-Summary
    user = (
        f'Objekt: "{prompt}"\n'
        f'Typ: {clf.get("object_type","?")} ({clf.get("category","?")})\n'
        f'Größe: {dim.get("length","?")}m x {dim.get("width","?")}m x {dim.get("height","?")}m\n'
        f'Overall Bounds: {clf.get("overall_bounds","?")}\n\n'
        f'=== Baugruppe: {asm_name} ===\n'
        f'Baugruppen-Bounds: {rb}\n'
        f'Beschreibung: {asm_item.get("description","")}\n\n'
    )

    if all_placed:
        # Zeige alle bereits platzierten Teile (kompaktes Format)
        same_asm = [p for p in all_placed if p.get("_assembly") == asm_name]
        other    = [p for p in all_placed if p.get("_assembly") != asm_name]

        user += f'Bereits platziert ({len(all_placed)} Teile gesamt):\n'
        if same_asm:
            user += f'  [Diese Baugruppe: {asm_name}]\n'
            for p in same_asm:
                b = p.get("bounds", [])
                if len(b) == 6:
                    user += (f'    {p["name"]:<25} '
                             f'X[{b[0]:+.3f}..{b[1]:+.3f}] '
                             f'Y[{b[2]:+.3f}..{b[3]:+.3f}] '
                             f'Z[{b[4]:+.3f}..{b[5]:+.3f}]\n')
        if other:
            user += f'  [Andere Baugruppen: {len(other)} Teile]\n'
            for p in other[:MAX_OTHER_PARTS_DISPLAY]:
                b = p.get("bounds", [])
                if len(b) == 6:
                    user += (f'    {p["name"]:<25} '
                             f'X[{b[0]:+.3f}..{b[1]:+.3f}] '
                             f'Y[{b[2]:+.3f}..{b[3]:+.3f}] '
                             f'Z[{b[4]:+.3f}..{b[5]:+.3f}]\n')

        # Globale ASCII-Draufsicht (alle platzierten Teile)
        overall_b = clf.get("overall_bounds")
        if overall_b and len(overall_b) == 6:
            sketch = _build_ascii_sketch(all_placed, overall_b)
            if sketch:
                user += f'\nDraufsicht GESAMT (X→ rechts, Y↑ oben):\n{sketch}\n'
        user += "\n"

    if retry_info:
        user += f'FEHLER BEI LETZTEM VERSUCH:\n{retry_info}\n\n'

    user += (
        f'=== Platziere jetzt ===\n'
        f'Name:         {part_name}\n'
        f'Beschreibung: {part.get("description","")}\n'
        f'Methode:      {part.get("method","box")}\n'
        f'Symmetrie:    {part.get("symmetry","none")}\n\n'
        f'WICHTIG: Bounds MÜSSEN innerhalb der Baugruppen-Bounds liegen: {rb}\n'
        f'WICHTIG: Bounds DÜRFEN NICHT identisch mit den Baugruppen-Bounds sein!\n'
        f'WICHTIG: Das Teil ist nur ein kleines Stück der Baugruppe — deutlich kleiner!\n'
        f'Gib [xmin,xmax,ymin,ymax,zmin,zmax] in Metern an.'
    )
    return user

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
    """Dispatcher für Phase 0 Sub-Schritte (0a/0b/1a)."""
    with _lock:
        step = _state.get("_ph0_step", 0)
    if step == 0:
        _h0a(raw)
    elif step == 1:
        _h0b(raw)
    elif step == 2:
        _h1a(raw)
    else:
        _log("ERR", f"Unbekannter ph0_step: {step}", phase=0)
        with _lock:
            _state["status"] = "error"
            _state["last_error"] = f"ph0_step {step} unbekannt"


def _h0a(raw):
    """Phase 0a: WAS ist das Objekt? (Typ, Kategorie, Komplexität)"""
    try:
        data = _parse_json(raw)
    except Exception as e:
        _log("WARN", f"Ph0a JSON: {e} → defaults", phase=0)
        data = {}

    obj_type   = data.get("object_type", "Objekt")
    category   = data.get("category", "other")
    complexity = data.get("complexity", "medium")

    _log("OK",
         f"Ph0a: {obj_type} | Kategorie: {category} | Komplexität: {complexity}",
         phase=0)

    with _lock:
        clf = dict(_state["classification"])
        clf.update(data)
        _state["classification"] = clf
        _state["_ph0_step"] = 1

    _run_phase()


def _h0b(raw):
    """Phase 0b: Wie GROSS? Dimensionen + overall_bounds. Setzt dynamische Limits."""
    try:
        data = _parse_json(raw)
    except Exception as e:
        _log("WARN", f"Ph0b JSON: {e} → defaults", phase=0)
        data = {}

    with _lock:
        clf = dict(_state["classification"])
        clf.update(data)
        _state["classification"] = clf

        # Dynamische Teile-Limits basierend auf Komplexität
        complexity = clf.get("complexity", "medium")
        cur_mpa    = _state["max_parts_per_assembly"]
        cur_mbp    = _state["max_bounds_parts"]

        if complexity == "simple":
            new_mpa = min(cur_mpa, 4)
            new_mbp = min(cur_mbp, 15)
        elif complexity == "complex":
            new_mpa = min(cur_mpa, 12)
            new_mbp = cur_mbp
        else:  # medium
            new_mpa = min(cur_mpa, 8)
            new_mbp = min(cur_mbp, 25)

        _state["max_parts_per_assembly"] = new_mpa
        _state["max_bounds_parts"]       = new_mbp
        _state["_ph0_step"] = 2

    dim = data.get("dimensions_m", {})
    _log("OK",
         f"Ph0b: L={dim.get('length','?')}m B={dim.get('width','?')}m H={dim.get('height','?')}m | "
         f"Bounds: {data.get('overall_bounds','?')} | "
         f"Limits → mpa={new_mpa}, mbp={new_mbp}",
         phase=0)

    cache.save_step(0, clf)
    _run_phase()


def _h1a(raw):
    """Phase 1a: Welche Hauptbaugruppen? Übergang zu Phase 1 (per-assembly detail)."""
    try:
        data = _parse_json(raw)
    except Exception as e:
        _log("WARN", f"Ph1a JSON: {e} → leere Baugruppen", phase=0)
        data = {}

    assemblies = data.get("assemblies", [])
    if not assemblies:
        # Fallback: eine Baugruppe aus dem Gesamtobjekt
        with _lock:
            clf = _state["classification"]
        assemblies = [{
            "name":        "main_body",
            "description": f"Hauptkörper von {clf.get('object_type','Objekt')}",
            "role":        "Hauptstruktur",
            "estimated_parts": 3,
            "rough_bounds": clf.get("overall_bounds", [-0.5, 0.5, -0.5, 0.5, 0.0, 1.0]),
        }]
        _log("WARN", "Ph1a: keine Baugruppen → verwende Fallback 'main_body'", phase=0)

    # Baugruppen-Bounds visualisieren
    zones = [{"name": a["name"], "bounds": a.get("rough_bounds", [])}
             for a in assemblies if a.get("rough_bounds")]
    if zones:
        mesh_builder.visualize_zones(zones)

    with _lock:
        clf = dict(_state["classification"])
        clf["assemblies"] = assemblies
        _state["classification"] = clf
        _state["assemblies"]     = assemblies
        _state["all_parts"]      = []

    _log("OK",
         f"Ph1a: {len(assemblies)} Baugruppen: " +
         ", ".join(a.get("name","?") for a in assemblies[:6]),
         phase=0)

    cache.save_step(0, clf)
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
        clf    = dict(_state["classification"])
        asm    = list(_state["assemblies"])
        prompt = _state["user_prompt"]
        model  = _state["llm_model"]
        host   = _state["llm_host"]
        retry  = _state.get("_ph2_retry_count", 0)

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
                _state["_ph2_retry_count"] = 0
            _run_phase()
            return

    # Normaler Bounds-Aufruf mit Validierung und Retry
    asm_name = current.get("_assembly", "")
    asm_item = next((a for a in asm if a.get("name") == asm_name), {})
    asm_rb   = asm_item.get("rough_bounds", clf.get("overall_bounds", []))
    same_asm = [p for p in placed if p.get("_assembly") == asm_name]

    try:
        data   = _parse_json(raw)
        bounds = data.get("bounds")
    except Exception as e:
        _log("WARN", f"Ph2 '{part_name}': {e} → Fallback", phase=2, part=part_name)
        bounds = None

    norm = mesh_builder.normalize_bounds(bounds)
    if norm is None:
        norm = asm_rb[:] if asm_rb else [-0.5, 0.5, -0.5, 0.5, 0.0, 1.0]
    b = mesh_builder.repair_bounds(norm, part_name)

    # Validierung: Prüfe ob Bounds plausibel sind
    is_invalid, reason = _validate_ph2_bounds(b, asm_rb, same_asm)

    if is_invalid and retry < MAX_BOUNDS_RETRIES:
        _log("WARN",
             f"Bounds '{part_name}' ungültig (Versuch {retry+1}/{MAX_BOUNDS_RETRIES+1}): {reason} "
             f"→ Retry mit erweitertem Prompt",
             phase=2, part=part_name)
        with _lock:
            _state["_ph2_retry_count"] = retry + 1
        retry_user = _build_ph2_user_prompt(
            current, asm_item, clf, placed, prompt,
            retry_info=(
                f"Du hast bounds={[round(v,2) for v in b]} zurückgegeben.\n"
                f"Problem: {reason}\n"
                f"Bitte andere, kleinere Bounds wählen die nur dieses eine Teil beschreiben!"
            )
        )
        _call(prompts.PHASE_2_BOUNDS, retry_user,
              model, host, 2,
              f"Bounds-Retry {retry+1}: {part_name} ({idx+1}/{len(queue)})",
              part_name)
        return

    if is_invalid:
        # Alle Retries erschöpft → Auto-Platzierung
        b = _auto_place_in_assembly(current, asm_rb, same_asm)
        b = mesh_builder.repair_bounds(b, part_name)
        _log("WARN",
             f"Auto-Platzierung '{part_name}' (nach {retry} Retries): "
             f"{[round(v,2) for v in b]}",
             phase=2, part=part_name)
    else:
        _log("OK", f"Bounds '{part_name}': {[round(v,2) for v in b]}", phase=2, part=part_name)

    placed_part           = dict(current)
    placed_part["bounds"] = b
    mesh_builder.build_placeholder(placed_part)
    with _lock:
        _state["placed"].append(placed_part)
        _state["sub_index"] += 1
        _state["_ph2_retry_count"] = 0
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
    data = {}  # Initialisierung vor try-Block verhindert NameError
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
