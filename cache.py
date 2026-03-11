"""
cache.py - Text to Blender v7.0.0
════════════════════════════════════
Zentrales Log-, Cache- und Projektordner-Management.

Projektordner-Struktur:
  <projektordner>/
  ├── pipeline.log
  ├── parts_list.txt
  ├── joints_list.txt
  ├── cache/
  │   ├── phase0_classify.json
  │   ├── phase1_structure.json
  │   ├── phase2_bounds.json
  │   ├── phase3_pointclouds.json
  │   └── phase5_materials.json
  └── raw/
      └── phase{N}_{teil}_{ts}.txt
"""

import os, json, datetime

_project_dir = os.path.join(os.path.expanduser("~"), "text_to_blender", "default_project")

LEVEL_INFO  = "INFO"
LEVEL_OK    = "OK  "
LEVEL_WARN  = "WARN"
LEVEL_ERROR = "ERR "
LEVEL_STEP  = "STEP"
LEVEL_LLM   = "LLM "
LEVEL_DATA  = "DATA"

# ── Verzeichnis-Verwaltung ──────────────────────────────────────────────────

def set_project_dir(path: str):
    global _project_dir
    _project_dir = path
    _ensure_dirs()
    log(LEVEL_INFO, f"Projektordner: {path}")

def get_project_dir() -> str: return _project_dir
def get_log_path()    -> str: return os.path.join(_project_dir, "pipeline.log")
def get_cache_dir()   -> str: return os.path.join(_project_dir, "cache")
def get_raw_dir()     -> str: return os.path.join(_project_dir, "raw")
def get_parts_list_path()  -> str: return os.path.join(_project_dir, "parts_list.txt")
def get_joints_list_path() -> str: return os.path.join(_project_dir, "joints_list.txt")

def _ensure_dirs():
    for d in (_project_dir, get_cache_dir(), get_raw_dir()):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:
            print(f"[TTB][CACHE] {d}: {e}")

_ensure_dirs()

# ── Logging ─────────────────────────────────────────────────────────────────

def log(level: str, message: str, phase=0, part: str = ""):
    try:
        _ensure_dirs()
        ts       = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ph_str   = f"[Ph{phase:>3}] " if phase else "        "
        part_str = f"[{part[:20]:<20}] " if part else " " * 23
        header   = f"[{ts}] [{level}] {ph_str}{part_str}"
        lines    = message.splitlines()
        indent   = " " * len(header)
        entry    = header + lines[0] + "\n"
        for ln in lines[1:]:
            entry += indent + ln + "\n"
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"[TTB][LOG-FEHLER] {e}")

def log_separator(label: str = ""):
    try:
        _ensure_dirs()
        sep = "=" * 80
        ts  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        txt = f"\n{sep}\n  {ts}  {label}\n{sep}\n" if label else f"\n{sep}\n"
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(txt)
    except Exception:
        pass

def log_json(label: str, data, phase=0):
    try:
        log(LEVEL_LLM, f"{label}:\n{json.dumps(data, ensure_ascii=False, indent=2)}", phase=phase)
    except Exception:
        pass

def log_parts_list(parts: list, phase=0):
    if not parts:
        return
    try:
        hdr  = f"{'Nr':>3}  {'Name':<28} {'Baugruppe':<20} {'Methode':<12}  {'Bounds':^44}  {'Pts':>4}"
        sep  = "-" * 120
        rows = [hdr, sep]
        for i, p in enumerate(parts, 1):
            b   = p.get("bounds")
            pts = len(p.get("points", [])) if isinstance(p.get("points"), list) else 0
            bs  = (f"x[{b[0]:6.2f}..{b[1]:6.2f}] y[{b[2]:6.2f}..{b[3]:6.2f}] z[{b[4]:6.2f}..{b[5]:6.2f}]"
                   if b and len(b) == 6 else "(keine Bounds)                    ")
            rows.append(
                f"{i:>3}  {str(p.get('name','?'))[:28]:<28} "
                f"{str(p.get('_assembly','?'))[:20]:<20} "
                f"{str(p.get('method','box'))[:12]:<12}  "
                f"{bs:<44}  {pts:>4}"
            )
        rows += [sep, f"    Gesamt: {len(parts)} Teile"]
        text = "\n".join(rows)
        log(LEVEL_DATA, "Teile-Liste:\n" + text, phase=phase)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(get_parts_list_path(), "w", encoding="utf-8") as f:
            f.write(f"Text to Blender v6.0.0 – Teile-Liste\nErstellt: {ts} | Phase {phase}\n")
            f.write("=" * 120 + "\n" + text + "\n")
    except Exception as e:
        log(LEVEL_WARN, f"Teile-Listen-Log fehlgeschlagen: {e}")

def log_pointcloud(part_name: str, bounds: list, points: list, phase=0):
    try:
        b = bounds if bounds and len(bounds) == 6 else [-999]*6
        lines = [
            f"Pointcloud '{part_name}' ({len(points)} Punkte)",
            f"  Bounds: x[{b[0]:.3f}..{b[1]:.3f}] y[{b[2]:.3f}..{b[3]:.3f}] z[{b[4]:.3f}..{b[5]:.3f}]",
            f"  {'Nr':>3}  {'X':>8}  {'Y':>8}  {'Z':>8}  Status",
            f"  {'─'*45}",
        ]
        outside = 0
        for i, pt in enumerate(points):
            try:
                x, y, z = float(pt[0]), float(pt[1]), float(pt[2])
                ok = (b[0]<=x<=b[1]) and (b[2]<=y<=b[3]) and (b[4]<=z<=b[5])
                if not ok: outside += 1
                lines.append(f"  {i+1:>3}  {x:>8.3f}  {y:>8.3f}  {z:>8.3f}  {'OK' if ok else 'AUSSERHALB'}")
            except Exception:
                lines.append(f"  {i+1:>3}  (ungueltig)")
        lines.append(f"  {'─'*45}")
        lines.append(f"  {len(points)-outside}/{len(points)} innerhalb Bounds"
                     + (f" | {outside} geclampt" if outside else ""))
        log(LEVEL_DATA, "\n".join(lines), phase=phase, part=part_name)
    except Exception as e:
        log(LEVEL_WARN, f"Pointcloud-Log fehlgeschlagen: {e}", part=part_name)

# ── Cache I/O ────────────────────────────────────────────────────────────────

_CACHE_FILES = {
    0:   "phase0_classify.json",
    1:   "phase1_structure.json",
    2:   "phase2_bounds.json",
    3:   "phase3_pointclouds.json",
    5:   "phase5_materials.json",
}

def save_step(step, data: dict) -> bool:
    filename = _CACHE_FILES.get(step)
    if not filename:
        return False
    try:
        _ensure_dirs()
        with open(os.path.join(get_cache_dir(), filename), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log(LEVEL_OK, f"Cache gespeichert: {filename}", phase=int(step))
        return True
    except Exception as e:
        log(LEVEL_ERROR, f"Cache-Fehler ({filename}): {e}", phase=int(step))
        return False

def load_step(step) -> dict:
    filename = _CACHE_FILES.get(step)
    if not filename:
        return {}
    path = os.path.join(get_cache_dir(), filename)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_raw(raw: str, phase, part_name: str = ""):
    try:
        _ensure_dirs()
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = part_name.replace(" ", "_")[:20] if part_name else "general"
        with open(os.path.join(get_raw_dir(), f"phase{phase}_{slug}_{ts}.txt"),
                  "w", encoding="utf-8") as f:
            f.write(raw or "")
    except Exception as e:
        log(LEVEL_WARN, f"Raw-Speichern fehlgeschlagen: {e}")

import shutil

def clear_cache():
    # 1. Cache-Ordner leeren
    cache_path = get_cache_dir()
    if os.path.exists(cache_path):
        shutil.rmtree(cache_path)

    # 2. Raw-Ordner leeren
    raw_path = get_raw_dir()
    if os.path.exists(raw_path):
        shutil.rmtree(raw_path)

    # 3. Logdatei löschen
    log_file = get_log_path()
    if os.path.exists(log_file):
        try: os.remove(log_file)
        except: pass

    # Ordner direkt wieder sauber anlegen
    _ensure_dirs()
    log(LEVEL_INFO, "Projektordner wurde komplett geleert.")
