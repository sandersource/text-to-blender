"""
pipeline.py - Text to Blender v7.0.0
══════════════════════════════════════
Zoom-In Pipeline — jeder LLM-Call = EINE einfache Frage.

Phase 0a : WAS ist es? (Typ, Kategorie, Symmetrie)     — 1 Call
Phase 0b : Wie GROSS? (Dimensionen + overall_bounds)   — 1 Call
Phase 1a : Welche HAUPTTEILE? (max 6-8 Baugruppen)     — 1 Call
Phase 1b : Pro Baugruppe → Einzelteile                 — N Calls
Phase 2  : Pro Teil → Bounds (mit Kontext + Skizze)    — N Calls + Retry
Phase 3  : Pro convex_hull-Teil → Pointcloud           — M Calls
Phase 4  : Mesh-Bau im Blender-Main-Thread (kein LLM)  — 0 Calls
Phase 5  : Materialien                                 — 1 Call
"""

import re, json, ast, math, threading
import bpy
from . import llm_client, mesh_builder, prompts, cache

# ── State ────────────────────────────────────────────────────────────────────
_lock  = threading.Lock()
_state = {
    "status":       "idle",
    "phase":        0,
    "sub_phase":    "a",   # "a"|"b"|None — Unterphasen fuer 0 und 1
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
    "bounds_warnings":    [],

    # Retry-State fuer Phase 2
    "bounds_retry_count": 0,
    "bounds_retry_error": None,
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
    """Nutzt llm_client.extract_json fuer einheitliche JSON-Extraktion."""
    return json.loads(llm_client.extract_json(text))

# ── Dynamische Limits ────────────────────────────────────────────────────────

def _calc_dynamic_limits(dim: dict) -> int:
    """
    Berechnet maximale Teile-Anzahl basierend auf Objekt-Volumen.
    Sehr klein (Ring, Muenze):    → 8 Teile
    Klein (Gamepad, Handy, Maus): → 15 Teile
    Mittel (Stuhl, Fahrrad):      → 25 Teile
    Gross (Haus, Auto):           → 40 Teile
    """
    try:
        l = max(0.001, float(dim.get("length", 1.0)))
        w = max(0.001, float(dim.get("width",  1.0)))
        h = max(0.001, float(dim.get("height", 1.0)))
        volume = l * w * h
    except Exception:
        volume = 1.0
    # Kalibrierung:
    #   Ring/Muenze: ~0.02x0.02x0.01m = 0.000004 m³
    #   Gamepad:     ~0.15x0.10x0.05m = 0.00075 m³
    #   Handy:       ~0.15x0.07x0.01m = 0.000105 m³
    #   Stuhl:       ~0.5x0.5x0.9m    = 0.225 m³
    #   Haus:        ~10x8x6m         = 480 m³
    if volume < 0.0001:   # Sehr klein: Ring, Muenze
        return 8
    elif volume < 0.01:   # Klein: Gamepad, Handy, Maus
        return 15
    elif volume < 1.0:    # Mittel: Stuhl, Fahrrad, PC
        return 25
    else:                 # Gross: Haus, Auto
        return 40

# ── Kontext-Zusammenfassung ──────────────────────────────────────────────────

def _make_context_summary(clf: dict, placed_parts: list, asm_name: str = "") -> str:
    """
    Erstellt eine kompakte, menschenlesbare Zusammenfassung der bisherigen Platzierungen.
    Gibt dem LLM Kontext ueber bereits belegte Bereich im 3D-Raum.
    """
    obj_type = clf.get("object_type", "?")
    dim = clf.get("dimensions_m", {})
    lines = [
        f"Objekt: {obj_type}",
        f"Groesse: "
        f"{dim.get('length','?')}m x {dim.get('width','?')}m x {dim.get('height','?')}m",
    ]
    if placed_parts:
        lines.append(f"Bereits platziert ({len(placed_parts)} Teile):")
        for p in placed_parts:
            b = p.get("bounds", [])
            if len(b) == 6:
                lines.append(
                    f"  {p.get('name','?'):<22} "
                    f"x[{b[0]:+.3f}..{b[1]:+.3f}] "
                    f"y[{b[2]:+.3f}..{b[3]:+.3f}] "
                    f"z[{b[4]:+.3f}..{b[5]:+.3f}]"
                )
    return "\n".join(lines)

# ── ASCII-Draufsicht-Skizze ──────────────────────────────────────────────────

def _make_ascii_sketch(placed_parts: list, overall_bounds: list,
                       width: int = 38, height: int = 16) -> str:
    """
    Erzeugt eine ASCII-Draufsicht der bereits platzierten Teile.
    Hilft dem LLM zu sehen, welche Bereiche noch frei sind.

    Koordinatensystem der Skizze:
    - Spalten = X-Achse (links=xmin, rechts=xmax)
    - Zeilen  = Y-Achse (oben=ymax, unten=ymin)
    """
    if not overall_bounds or len(overall_bounds) != 6 or not placed_parts:
        return ""
    ob = overall_bounds
    ox = max(0.001, ob[1] - ob[0])
    oy = max(0.001, ob[3] - ob[2])

    grid = [["." for _ in range(width)] for _ in range(height)]
    legend = {}
    used_letters = set()

    for i, part in enumerate(placed_parts):
        b = part.get("bounds", [])
        if len(b) != 6:
            continue
        name = part.get("name", f"P{i}")
        # Buchstabe fuer dieses Teil
        letter = None
        for ch in (name[0].upper(), name[:2].upper(), chr(65 + (i % 26))):
            if ch[0] not in used_letters:
                letter = ch[0]
                used_letters.add(letter)
                break
        if letter is None:
            letter = chr(65 + (i % 26))
        legend[letter] = name

        # Bounds → Grid-Koordinaten
        c1 = int((b[0] - ob[0]) / ox * width)
        c2 = int((b[1] - ob[0]) / ox * width)
        r1 = int((ob[3] - b[3]) / oy * height)
        r2 = int((ob[3] - b[2]) / oy * height)

        c1 = max(0, min(width - 1, c1))
        c2 = max(0, min(width - 1, c2))
        r1 = max(0, min(height - 1, r1))
        r2 = max(0, min(height - 1, r2))
        if c1 > c2: c1, c2 = c2, c1
        if r1 > r2: r1, r2 = r2, r1

        for row in range(r1, r2 + 1):
            for col in range(c1, c2 + 1):
                grid[row][col] = letter

    lines = ["Draufsicht (X=links-rechts, Y=oben-unten, '.'=frei):"]
    lines.append("+" + "-" * width + "+")
    for row in grid:
        lines.append("|" + "".join(row) + "|")
    lines.append("+" + "-" * width + "+")
    if legend:
        leg_str = ", ".join(f"{k}={v}" for k, v in list(legend.items())[:12])
        lines.append(f"Legende: {leg_str}")
    return "\n".join(lines)

# ── Bounds-Validierung fuer Retry ────────────────────────────────────────────

def _validate_bounds_for_retry(b: list, overall_bounds: list,
                               placed: list, part_name: str) -> str:
    """
    Prueft ob Bounds plausibel sind.
    Gibt Fehlerbeschreibung zurueck (oder None wenn OK).
    """
    if not b or len(b) != 6:
        return "Ungueltige Bounds — Format muss [xmin,xmax,ymin,ymax,zmin,zmax] sein."
    try:
        b = [float(v) for v in b]
    except Exception:
        return "Bounds enthalten nicht-numerische Werte."

    if b[0] >= b[1]:
        return f"xmin ({b[0]:.3f}) muss kleiner als xmax ({b[1]:.3f}) sein."
    if b[2] >= b[3]:
        return f"ymin ({b[2]:.3f}) muss kleiner als ymax ({b[3]:.3f}) sein."
    if b[4] >= b[5]:
        return f"zmin ({b[4]:.3f}) muss kleiner als zmax ({b[5]:.3f}) sein."

    dx = b[1] - b[0]
    dy = b[3] - b[2]
    dz = b[5] - b[4]
    if dx < 0.001 or dy < 0.001 or dz < 0.001:
        return f"Bounds zu klein: {dx*100:.1f}cm x {dy*100:.1f}cm x {dz*100:.1f}cm (Minimum: 1mm)"

    if overall_bounds and len(overall_bounds) == 6:
        ob = overall_bounds
        tol = max(0.01, max(ob[1]-ob[0], ob[3]-ob[2], ob[5]-ob[4]) * 0.15)
        if (b[0] < ob[0] - tol or b[1] > ob[1] + tol or
                b[2] < ob[2] - tol or b[3] > ob[3] + tol or
                b[4] < ob[4] - tol or b[5] > ob[5] + tol):
            return (
                f"Bounds ausserhalb Gesamtgrenzen "
                f"(erlaubt mit Toleranz: "
                f"x[{ob[0]-tol:.2f}..{ob[1]+tol:.2f}] "
                f"y[{ob[2]-tol:.2f}..{ob[3]+tol:.2f}] "
                f"z[{ob[4]-tol:.2f}..{ob[5]+tol:.2f}])"
            )

    # Overlap > 50% mit vorhandenen Teilen?
    for p in placed:
        if p.get("name") == part_name:
            continue
        pb = p.get("bounds", [])
        if len(pb) != 6:
            continue
        try:
            ox_ = max(0.0, min(b[1], pb[1]) - max(b[0], pb[0]))
            oy_ = max(0.0, min(b[3], pb[3]) - max(b[2], pb[2]))
            oz_ = max(0.0, min(b[5], pb[5]) - max(b[4], pb[4]))
            ov  = ox_ * oy_ * oz_
            if ov > 0:
                va = max(1e-9, dx * dy * dz)
                pct = ov / va * 100
                if pct > 50:
                    return (
                        f"Zu starkes Ueberlappung mit '{p.get('name','?')}': "
                        f"{pct:.0f}% des Teils ueberlappen sich. "
                        f"Platziere das Teil an einer anderen Position."
                    )
        except Exception:
            pass
    return None   # Alles OK

# ── Auto-Platzierung als Fallback ────────────────────────────────────────────

def _auto_place(part: dict, asm_item: dict, placed_same_asm: list) -> list:
    """
    Platziert ein Teil automatisch wenn alle Retries fehlgeschlagen sind.
    Versucht einen freien Bereich innerhalb der Baugruppen-Bounds zu finden.
    """
    rb = asm_item.get("rough_bounds")
    if not rb or len(rb) != 6:
        rb = [-0.5, 0.5, -0.5, 0.5, 0.0, 1.0]
    try:
        rb = [float(v) for v in rb]
    except Exception:
        rb = [-0.5, 0.5, -0.5, 0.5, 0.0, 1.0]

    # Ausmasse der Baugruppe
    ax = rb[1] - rb[0]
    ay = rb[3] - rb[2]
    az = rb[5] - rb[4]

    # Sinnvolle Teilegroesse: 1/3 der Baugruppe pro Dimension
    n = max(1, len(placed_same_asm) + 1)
    grid = max(1, int(n ** 0.5))
    part_dx = ax / (grid + 1)
    part_dy = ay / (grid + 1)
    part_dz = az

    # Versuche einen freien Platz zu finden
    steps = max(3, grid + 1)
    for xi in range(steps):
        for yi in range(steps):
            cx = rb[0] + ax * (xi + 0.5) / steps
            cy = rb[2] + ay * (yi + 0.5) / steps
            candidate = [
                cx - part_dx / 2, cx + part_dx / 2,
                cy - part_dy / 2, cy + part_dy / 2,
                rb[4], rb[4] + part_dz,
            ]
            # Pruefe ob frei
            conflict = False
            for p in placed_same_asm:
                pb = p.get("bounds", [])
                if len(pb) != 6:
                    continue
                ox_ = max(0.0, min(candidate[1], pb[1]) - max(candidate[0], pb[0]))
                oy_ = max(0.0, min(candidate[3], pb[3]) - max(candidate[2], pb[2]))
                oz_ = max(0.0, min(candidate[5], pb[5]) - max(candidate[4], pb[4]))
                if ox_ * oy_ * oz_ > 0:
                    conflict = True
                    break
            if not conflict:
                return candidate

    # Letzter Fallback: Mitte der Baugruppe
    cx = (rb[0] + rb[1]) / 2
    cy = (rb[2] + rb[3]) / 2
    return [
        cx - part_dx / 2, cx + part_dx / 2,
        cy - part_dy / 2, cy + part_dy / 2,
        rb[4], rb[4] + part_dz,
    ]

# ── Pipeline starten / stoppen ───────────────────────────────────────────────

def start(prompt, model, host, detail="medium", project_dir="",
          max_parts_per_assembly=12, max_bounds_parts=40, max_pointcloud_parts=10):

    if project_dir:
        cache.set_project_dir(project_dir)
    cache.log_separator(f"TEXT TO BLENDER v7.0.0 | Prompt: {prompt}")
    _log("INFO",
         f"Limits: max_parts={max_parts_per_assembly}, "
         f"max_bounds={max_bounds_parts}, "
         f"max_pointclouds={max_pointcloud_parts}")

    with _lock:
        _state.update({
            "status": "running", "phase": 0, "sub_phase": "a",
            "phase_label": "Starte ...",
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
            "bounds_retry_count": 0,
            "bounds_retry_error": None,
        })
    _run_phase()

def reset():
    with _lock:
        _state.update({
            "status": "idle", "phase": 0, "sub_phase": "a",
            "phase_label": "",
            "sub_index": 0, "sub_total": 0, "sub_queue": [],
            "user_prompt": "",
            "classification": {}, "assemblies": [],
            "all_parts": [], "placed": [], "final_parts": [],
            "log": [], "last_error": None,
            "pending_raw": None, "pending_err": None,
            "bounds_warnings": [],
            "bounds_retry_count": 0,
            "bounds_retry_error": None,
        })
    _log("INFO", "Pipeline zurueckgesetzt.")

# ── Phase-Dispatch ────────────────────────────────────────────────────────────

def _run_phase():
    with _lock:
        phase     = _state["phase"]
        sub_phase = _state.get("sub_phase")
        model     = _state["llm_model"]
        host      = _state["llm_host"]
        prompt    = _state["user_prompt"]
        clf       = dict(_state["classification"])
        asm       = list(_state["assemblies"])
        parts     = list(_state["all_parts"])
        placed    = list(_state["placed"])
        queue     = list(_state["sub_queue"])
        idx       = _state["sub_index"]
        detail    = _state["detail"]
        mpa       = _state["max_parts_per_assembly"]
        mbp       = _state["max_bounds_parts"]
        mpc       = _state["max_pointcloud_parts"]
        retry_cnt = _state.get("bounds_retry_count", 0)
        retry_err = _state.get("bounds_retry_error")

    # ── Phase 0a: Was IST das Objekt? ───────────────────────────────────────
    if phase == 0 and sub_phase == "a":
        _call(prompts.PHASE_0A_TYPE,
              f'Beschreibe das Objekt: "{prompt}"',
              model, host, 0, "0a: Typ-Erkennung", "")

    # ── Phase 0b: Wie GROSS ist es? ──────────────────────────────────────────
    elif phase == 0 and sub_phase == "b":
        obj_type = clf.get("object_type", prompt)
        _call(prompts.PHASE_0B_SIZE,
              f'Objekt: "{obj_type}" (aus Prompt: "{prompt}")\n'
              f'Gib die realistischen Dimensionen in Metern an.',
              model, host, 0, "0b: Groessen-Erkennung", "")

    # ── Phase 1a: Welche HAUPTTEILE? ────────────────────────────────────────
    elif phase == 1 and sub_phase == "a":
        obj_type = clf.get("object_type", prompt)
        dim      = clf.get("dimensions_m", {})
        ob       = clf.get("overall_bounds", [])
        _call(prompts.PHASE_1A_MAIN_PARTS,
              f'Objekt: "{prompt}"\n'
              f'Typ: {obj_type} | Kategorie: {clf.get("category","?")}\n'
              f'Symmetrie: {clf.get("symmetry","?")}\n'
              f'Gesamtmasse: L={dim.get("length","?")}m '
              f'B={dim.get("width","?")}m '
              f'H={dim.get("height","?")}m\n'
              f'Gesamtbounds: {ob}\n\n'
              f'Liste die logischen Hauptbaugruppen auf.\n'
              f'LIMIT: maximal 6-8 Baugruppen.',
              model, host, 1, "1a: Hauptteile", "")

    # ── Phase 1b: Pro Baugruppe → Einzelteile ───────────────────────────────
    elif phase == 1 and sub_phase == "b":
        if idx < len(queue):
            asm_item = queue[idx]
            asm_name = asm_item.get("name", "?")
            rb       = asm_item.get("rough_bounds", clf.get("overall_bounds", []))
            _call(prompts.PHASE_1B_SUB_PARTS,
                  f'Objekt: "{prompt}"\n'
                  f'Typ: {clf.get("object_type","?")} | '
                  f'Kategorie: {clf.get("category","?")}\n'
                  f'Gesamtmasse: L={clf.get("dimensions_m",{}).get("length","?")}m '
                  f'B={clf.get("dimensions_m",{}).get("width","?")}m '
                  f'H={clf.get("dimensions_m",{}).get("height","?")}m\n'
                  f'Gesamtbounds: {clf.get("overall_bounds","?")}\n\n'
                  f'=== Baugruppe: {asm_name} ===\n'
                  f'Beschreibung: {asm_item.get("description","")}\n'
                  f'Rolle: {asm_item.get("role","")}\n'
                  f'Baugruppen-Bounds: {rb}\n\n'
                  f'Erstelle die Einzelteile fuer diese Baugruppe.\n'
                  f'LIMIT: maximal {mpa} Teile.\n'
                  f'Nutze symmetry "mirror_Y" fuer links/rechts gespiegelte Teile.\n'
                  f'Nutze symmetry "radial_N" fuer rotationssymmetrische Teile.',
                  model, host, 1,
                  f"1b: Baugruppe '{asm_name}' ({idx+1}/{len(queue)})",
                  asm_name)
        else:
            total = len(parts)
            _log("OK", f"Phase 1 fertig: {total} Teile in {len(asm)} Baugruppen.", phase=1)
            cache.log_parts_list(parts, phase=1)
            cache.save_step(1, {"parts": parts, "assemblies": asm})
            if total > mbp:
                _log("WARN", f"Teile-Limit: {total} > {mbp} → kuerze.", phase=1)
                with _lock:
                    _state["all_parts"] = _state["all_parts"][:mbp]
            _advance(2)

    # ── Phase 2: Pro Teil → Bounds ──────────────────────────────────────────
    elif phase == 2:
        if idx < len(queue):
            part      = queue[idx]
            part_name = part.get("name", "?")
            asm_name  = part.get("_assembly", "")
            asm_item  = next((a for a in asm if a.get("name") == asm_name), {})
            rb        = asm_item.get("rough_bounds", clf.get("overall_bounds", []))
            overall   = clf.get("overall_bounds", [])
            same_asm  = [p for p in placed if p.get("_assembly") == asm_name]

            # Kompakter Kontext-Summary
            context_summary = _make_context_summary(clf, placed, asm_name)

            # ASCII-Draufsicht-Skizze
            ascii_sketch = _make_ascii_sketch(placed, overall)

            # Retry mit Fehlerbeschreibung?
            if retry_cnt > 0 and retry_err:
                system_prompt = prompts.PHASE_2_RETRY
                user = (
                    f'=== FEHLER IN VORHERIGER ANTWORT ===\n'
                    f'Fehler: {retry_err}\n'
                    f'Versuch {retry_cnt}/2 — bitte korrigieren.\n\n'
                    f'=== Kontext ===\n'
                    f'{context_summary}\n\n'
                )
            else:
                system_prompt = prompts.PHASE_2_BOUNDS
                user = f'=== Kontext ===\n{context_summary}\n\n'

            if ascii_sketch:
                user += f'{ascii_sketch}\n\n'

            user += (
                f'=== Baugruppe: {asm_name} ===\n'
                f'Baugruppen-Bounds: {rb}\n'
                f'Beschreibung: {asm_item.get("description","")}\n\n'
                f'=== Platziere jetzt ===\n'
                f'Name:         {part_name}\n'
                f'Beschreibung: {part.get("description","")}\n'
                f'Methode:      {part.get("method","box")}\n'
                f'Symmetrie:    {part.get("symmetry","none")}\n\n'
                f'Bounds MUESSEN innerhalb der Baugruppen-Bounds liegen: {rb}\n'
                f'Das Teil muss an einer ANDEREN Position als bereits platzierte Teile sein.\n'
                f'Gib [xmin,xmax,ymin,ymax,zmin,zmax] in Metern an.'
            )
            label_retry = f" (Retry {retry_cnt}/2)" if retry_cnt > 0 else ""
            _call(system_prompt, user,
                  model, host, 2,
                  f"2: Bounds '{part_name}' ({idx+1}/{len(queue)}){label_retry}",
                  part_name)
        else:
            _log("OK", f"Phase 2 fertig: {len(placed)} Teile platziert.", phase=2)
            overall  = clf.get("overall_bounds", [])
            warnings = mesh_builder.validate_bounds_list(placed, overall, phase=2)
            # Raeumliche Verteilung pruefen
            mesh_builder.validate_spatial_distribution(placed, phase=2)
            with _lock:
                _state["bounds_warnings"] = warnings
            cache.log_parts_list(placed, phase=2)
            cache.save_step(2, {"parts": placed})
            _advance(3)

    # ── Phase 3: Pointclouds ─────────────────────────────────────────────────
    elif phase == 3:
        if idx < len(queue):
            part      = queue[idx]
            part_name = part.get("name", "?")
            bounds    = part.get("bounds", [])
            n         = DETAIL_POINTS.get(detail, 24)
            user = (
                f'Objekt: "{prompt}"\n'
                f'Koordinatensystem: X=Laenge(vorne=+X), Y=Breite(rechts=+Y), Z=Hoehe(Boden=0)\n\n'
                f'Teil: {part_name}\n'
                f'Beschreibung: {part.get("description","")}\n'
                f'Baugruppe: {part.get("_assembly","")}\n'
                f'Bounds: {[round(v,3) for v in bounds]}\n\n'
                f'Erstelle {n} Punkte die die Form von "{part_name}" beschreiben.\n'
                f'ALLE Punkte muessen strikt innerhalb der Bounds liegen.'
            )
            _call(prompts.PHASE_3_POINTCLOUD, user,
                  model, host, 3,
                  f"3: Pointcloud '{part_name}' ({idx+1}/{len(queue)})",
                  part_name)
        else:
            _log("OK", "Phase 3 fertig.", phase=3)
            with _lock:
                fp = list(_state["final_parts"])
            cache.save_step(3, {"parts": fp})
            # Phase 4 MUSS im Main-Thread laufen!
            _schedule_phase_4()

    # ── Phase 5: Materialien ─────────────────────────────────────────────────
    elif phase == 5:
        with _lock:
            fp = list(_state["final_parts"])
        teil_namen = ", ".join(p.get("name","?") for p in fp[:30])
        _call(prompts.PHASE_5_MATERIALS,
              f'Objekt: "{prompt}"\n'
              f'Teile ({len(fp)}): {teil_namen}\n'
              f'Weise jedem Teil ein realistisches Material zu.',
              model, host, 5, "5: Materialien", "")

    else:
        _log("ERR", f"Unbekannte Phase: {phase} / sub_phase: {_state.get('sub_phase')}")
        with _lock:
            _state["status"] = "error"
            _state["last_error"] = f"Phase {phase}/{_state.get('sub_phase')} unbekannt"

# ── Phase 4: Mesh-Bau (MUSS im Main-Thread laufen!) ─────────────────────────

def _schedule_phase_4():
    """Registriert Phase 4 als bpy.app.timers → laeuft sicher im Main-Thread."""
    _log("INFO", "Phase 4: Warte auf Blender Main-Thread ...")
    try:
        bpy.app.timers.register(_phase_4_main_thread, first_interval=0.05)
    except Exception as e:
        _log("ERR", f"Timer-Registrierung fehlgeschlagen: {e}")
        with _lock:
            _state["status"]     = "error"
            _state["last_error"] = str(e)

def _phase_4_main_thread():
    """Wird von bpy.app.timers im Main-Thread ausgefuehrt. Baut alle Meshes."""
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
        _log("ERR", "LLM beschaeftigt!", phase=phase)
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
        raw     = _state.get("pending_raw")
        err     = _state.get("pending_err")
        ph      = _state["phase"]
        sub_ph  = _state.get("sub_phase")
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

    # Dispatch: (phase, sub_phase) → handler
    fn_map = {
        (0, "a"): _h0a,
        (0, "b"): _h0b,
        (1, "a"): _h1a,
        (1, "b"): _h1b,
        (2, None): _h2,
        (3, None): _h3,
        (5, None): _h5,
    }
    fn = fn_map.get((ph, sub_ph))
    if fn:
        try:
            fn(raw)
        except Exception as exc:
            _log("ERR", f"Ph{ph}/{sub_ph}: {exc}", phase=ph)
            with _lock:
                _state["status"]     = "error"
                _state["last_error"] = str(exc)
    else:
        _log("ERR", f"Kein Handler fuer Phase {ph}/{sub_ph}", phase=ph)
        with _lock:
            _state["status"]     = "error"
            _state["last_error"] = f"No handler for phase {ph}/{sub_ph}"
    return None

# ── Phase-Handler ─────────────────────────────────────────────────────────────

def _h0a(raw):
    """Phase 0a: Typ und Kategorie des Objekts."""
    try:
        data = _parse_json(raw)
    except Exception as e:
        _log("WARN", f"Ph0a JSON: {e} → leere Klassifikation", phase=0)
        data = {}

    cache.log_json("Phase 0a: Typ-Erkennung", data, phase=0)

    with _lock:
        _state["classification"].update(data)

    _log("OK",
         f"Typ: {data.get('object_type','?')} | "
         f"Kategorie: {data.get('category','?')} | "
         f"Symmetrie: {data.get('symmetry','?')} | "
         f"Komplexitaet: {data.get('complexity','?')}",
         phase=0)

    # Weiter zu Phase 0b
    with _lock:
        _state["phase"]     = 0
        _state["sub_phase"] = "b"
    _run_phase()


def _h0b(raw):
    """Phase 0b: Dimensionen und overall_bounds."""
    try:
        data = _parse_json(raw)
    except Exception as e:
        _log("WARN", f"Ph0b JSON: {e} → Standard-Dimensionen", phase=0)
        data = {}

    cache.log_json("Phase 0b: Groessen-Erkennung", data, phase=0)

    with _lock:
        _state["classification"].update(data)
        clf = dict(_state["classification"])

    dim = clf.get("dimensions_m", {})
    ob  = clf.get("overall_bounds", [])
    _log("OK",
         f"Masse: L={dim.get('length','?')}m "
         f"B={dim.get('width','?')}m "
         f"H={dim.get('height','?')}m | "
         f"Bounds: {ob}",
         phase=0)

    # Dynamische Teile-Limits basierend auf Objekt-Groesse
    dynamic_max = _calc_dynamic_limits(dim)
    with _lock:
        old_mbp = _state["max_bounds_parts"]
        # Nur verringern wenn das dynamische Limit kleiner ist als das konfigurierte
        if dynamic_max < old_mbp:
            _state["max_bounds_parts"] = dynamic_max
            _log("INFO",
                 f"Dynamisches Teile-Limit: {dynamic_max} "
                 f"(basierend auf Objekt-Volumen)",
                 phase=0)

    # Zonen-Visualisierung mit overall_bounds
    if ob and len(ob) == 6:
        zones = [{"name": "gesamt", "bounds": ob}]
        mesh_builder.visualize_zones(zones)

    cache.save_step(0, clf)

    # Weiter zu Phase 1a
    _advance(1, "a")


def _h1a(raw):
    """Phase 1a: Hauptbaugruppen-Liste."""
    try:
        data = _parse_json(raw)
    except Exception as e:
        _log("WARN", f"Ph1a JSON: {e} → uebersprungen", phase=1)
        data = {}

    cache.log_json("Phase 1a: Hauptteile", data, phase=1)

    assemblies = data.get("assemblies", [])
    if not assemblies:
        _log("WARN", "Ph1a: Keine Baugruppen erhalten → leere Liste", phase=1)

    _log("OK",
         f"Phase 1a: {len(assemblies)} Baugruppen erkannt: "
         f"{', '.join(a.get('name','?') for a in assemblies[:6])}",
         phase=1)

    # Zonen-Visualisierung
    zones = [{"name": a["name"], "bounds": a.get("rough_bounds", [])}
             for a in assemblies if a.get("rough_bounds")]
    if zones:
        mesh_builder.visualize_zones(zones)

    with _lock:
        _state["assemblies"] = assemblies
        _state["all_parts"]  = []

    # Weiter zu Phase 1b (pro Baugruppe)
    _advance(1, "b")


def _h1b(raw):
    """Phase 1b: Einzelteile einer Baugruppe."""
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
        _log("WARN", f"Ph1b '{asm_name}': {e} → uebersprungen", phase=1, part=asm_name)
        with _lock:
            _state["sub_index"] += 1
        _run_phase()
        return

    raw_parts = data.get("parts", [])[:mpa]
    expanded  = []
    for p in raw_parts:
        expanded.extend(_expand_symmetry(p, asm_name))

    _log("OK",
         f"Baugruppe '{asm_name}': {len(raw_parts)} Basis-Teile → "
         f"{len(expanded)} nach Expansion",
         phase=1, part=asm_name)

    with _lock:
        _state["all_parts"].extend(expanded)
        _state["sub_index"] += 1

    _run_phase()


def _h2(raw):
    """Phase 2: Bounds fuer ein Teil (mit Retry-Logik)."""
    with _lock:
        queue     = list(_state["sub_queue"])
        idx       = _state["sub_index"]
        placed    = list(_state["placed"])
        clf       = dict(_state["classification"])
        asm       = list(_state["assemblies"])
        retry_cnt = _state.get("bounds_retry_count", 0)

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
                _state["bounds_retry_count"] = 0
                _state["bounds_retry_error"]  = None
            _run_phase()
            return

    # Normaler Bounds-Aufruf: JSON parsen
    try:
        data   = _parse_json(raw)
        bounds = data.get("bounds")
    except Exception as e:
        _log("WARN", f"Ph2 '{part_name}': {e} → Fallback", phase=2, part=part_name)
        bounds = None

    norm = mesh_builder.normalize_bounds(bounds)
    if norm is not None:
        b = mesh_builder.repair_bounds(norm, part_name)
    else:
        b = None

    # Validierung: Ist die Bounds plausibel?
    asm_name = current.get("_assembly", "")
    asm_item = next((a for a in asm if a.get("name") == asm_name), {})
    overall  = clf.get("overall_bounds", [])

    validation_error = _validate_bounds_for_retry(b, overall, placed, part_name) if b else \
        "Kein gueltiges Bounds-Format erhalten."

    if validation_error and retry_cnt < 2:
        _log("WARN",
             f"Bounds '{part_name}' ungueltig (Versuch {retry_cnt+1}/2): {validation_error}",
             phase=2, part=part_name)
        with _lock:
            _state["bounds_retry_count"] = retry_cnt + 1
            _state["bounds_retry_error"]  = validation_error
        _run_phase()
        return

    # Alle Retries aufgebraucht oder Bounds OK → akzeptieren oder Auto-Platzierung
    if validation_error:
        _log("WARN",
             f"Bounds '{part_name}' nach 2 Retries noch ungueltig → Auto-Platzierung",
             phase=2, part=part_name)
        same_asm = [p for p in placed if p.get("_assembly") == asm_name]
        b = _auto_place(current, asm_item, same_asm)
        b = mesh_builder.repair_bounds(b, part_name)

    placed_part           = dict(current)
    placed_part["bounds"] = b
    _log("OK", f"Bounds '{part_name}': {[round(v,2) for v in b]}", phase=2, part=part_name)

    mesh_builder.build_placeholder(placed_part)
    with _lock:
        _state["placed"].append(placed_part)
        _state["sub_index"] += 1
        _state["bounds_retry_count"] = 0
        _state["bounds_retry_error"]  = None
    _run_phase()


def _h3(raw):
    """Phase 3: Pointcloud fuer ein Teil."""
    with _lock:
        queue = list(_state["sub_queue"])
        idx   = _state["sub_index"]

    if idx >= len(queue):
        _advance(4)
        return

    current   = queue[idx]
    part_name = current.get("name", "?")
    bounds    = current.get("bounds", [-1, 1, -1, 1, 0, 2])

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

    finished           = dict(current)
    finished["points"] = valid
    with _lock:
        _state["final_parts"].append(finished)
        _state["sub_index"] += 1
    _run_phase()


def _h5(raw):
    """Phase 5: Materialien zuweisen."""
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

def _advance(next_phase, next_sub_phase=None):
    with _lock:
        _state["phase"]     = next_phase
        _state["sub_index"] = 0
        _state["sub_phase"] = next_sub_phase

        if next_phase == 1 and next_sub_phase == "a":
            _state["sub_queue"] = []
            _state["sub_total"] = 0
            msg = "Phase 1a: Hauptbaugruppen werden identifiziert."

        elif next_phase == 1 and next_sub_phase == "b":
            q = list(_state["assemblies"])
            _state["sub_queue"] = q
            _state["sub_total"] = len(q)
            msg = f"Phase 1b: {len(q)} Baugruppen werden konkretisiert."

        elif next_phase == 2:
            parts = list(_state["all_parts"])
            mbp   = _state["max_bounds_parts"]
            if len(parts) > mbp:
                parts = parts[:mbp]
                _state["all_parts"] = parts
            _state["sub_queue"] = parts
            _state["sub_total"] = len(parts)
            _state["bounds_retry_count"] = 0
            _state["bounds_retry_error"]  = None
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

        elif next_phase == 4:
            _state["sub_queue"] = []
            _state["sub_total"] = 0
            msg = "Phase 4: Mesh-Bau wird gestartet."

        elif next_phase == 5:
            _state["sub_queue"] = []
            _state["sub_total"] = 0
            msg = "Phase 5: Materialien werden zugewiesen."

        else:
            msg = f"Phase {next_phase} gestartet."

    _log("STEP", msg, phase=next_phase)
    _run_phase()
