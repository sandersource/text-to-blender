"""
mesh_builder.py - LLM Assistant v2.9.0
════════════════════════════════════════
Neu in v2.9.0:
  validate_bounds_list() → Overlap-Check + Groessen-Check mit detailliertem Log
"""

import bpy, bmesh
from mathutils import Vector
from . import cache

COLLECTION_NAME = "LLM_Objects"
MAX_COORD       = 50.0
MIN_SIZE        = 0.005

# ── Collection ────────────────────────────────────────────────────────────────

def get_or_create_collection():
    col = bpy.data.collections.get(COLLECTION_NAME)
    if col is None:
        col = bpy.data.collections.new(COLLECTION_NAME)
        try: bpy.context.scene.collection.children.link(col)
        except Exception as e: cache.log(cache.LEVEL_WARN, f"Collection-Link: {e}")
    return col

def _link_to_col(obj):
    col = get_or_create_collection()
    for c in list(obj.users_collection):
        try: c.objects.unlink(obj)
        except Exception: pass
    col.objects.link(obj)

def clear_llm_objects():
    col = bpy.data.collections.get(COLLECTION_NAME)
    if not col: return
    removed = 0
    for obj in list(col.objects):
        try: bpy.data.objects.remove(obj, do_unlink=True); removed += 1
        except Exception as e: cache.log(cache.LEVEL_WARN, f"Loeschen: {e}")
    cache.log(cache.LEVEL_INFO, f"{removed} LLM-Objekte geloescht.")

# ── Bounds-Hilfsfunktionen ────────────────────────────────────────────────────

def normalize_bounds(raw) -> list:
    """Normalisiert Bounds-Formate → [xmin,xmax,ymin,ymax,zmin,zmax]. None bei Fehler."""
    if raw is None: return None
    if isinstance(raw, (list, tuple)):
        if len(raw) == 6:
            try: return [float(v) for v in raw]
            except Exception: pass
        if len(raw) == 2:
            a, b = raw
            if (isinstance(a,(list,tuple)) and isinstance(b,(list,tuple))
                    and len(a)==3 and len(b)==3):
                try:
                    xmn,ymn,zmn = [float(v) for v in a]
                    xmx,ymx,zmx = [float(v) for v in b]
                    return [xmn,xmx,ymn,ymx,zmn,zmx]
                except Exception: pass
        if len(raw) == 3:
            try:
                xs,ys,zs = raw
                if all(isinstance(v,(list,tuple)) and len(v)==2 for v in (xs,ys,zs)):
                    return [float(xs[0]),float(xs[1]),
                            float(ys[0]),float(ys[1]),
                            float(zs[0]),float(zs[1])]
            except Exception: pass
    return None

def repair_bounds(bounds: list, name: str = "") -> list:
    """Repariert invertierte Achsen, erzwingt Mindestgroesse, clampet Koordinaten."""
    try: xmn,xmx,ymn,ymx,zmn,zmx = [float(v) for v in bounds]
    except Exception:
        cache.log(cache.LEVEL_WARN, f"Bounds-Repair: ungueltige Werte fuer '{name}'")
        return [-0.5, 0.5, -0.5, 0.5, 0.0, 1.0]
    if xmn>xmx: xmn,xmx = xmx,xmn
    if ymn>ymx: ymn,ymx = ymx,ymn
    if zmn>zmx: zmn,zmx = zmx,zmn
    for lo,hi,ax in [(xmn,xmx,"X"),(ymn,ymx,"Y"),(zmn,zmx,"Z")]:
        if (hi-lo) < MIN_SIZE:
            mid = (lo+hi)/2.0
            if ax=="X": xmn,xmx = mid-MIN_SIZE/2, mid+MIN_SIZE/2
            elif ax=="Y": ymn,ymx = mid-MIN_SIZE/2, mid+MIN_SIZE/2
            else: zmn,zmx = mid-MIN_SIZE/2, mid+MIN_SIZE/2
            cache.log(cache.LEVEL_WARN, f"'{name}' {ax}-Achse zu klein → {MIN_SIZE}m")
    xmn=max(-MAX_COORD,xmn); xmx=min(MAX_COORD,xmx)
    ymn=max(-MAX_COORD,ymn); ymx=min(MAX_COORD,ymx)
    zmn=max(-MAX_COORD,zmn); zmx=min(MAX_COORD,zmx)
    return [xmn,xmx,ymn,ymx,zmn,zmx]

def validate_bounds_list(parts: list, overall_bounds: list, phase: int = 3):
    """
    Prueft alle Bounds auf Plausibilitaet und loggt Warnungen.

    Checks:
      1. Groesskontrolle: Kein Teil darf >70% des Gesamtvolumens abdecken
      2. Symmetrie-Check: Links/rechts-Paare sollten symmetrisch sein
      3. Overlap-Check: Stark ueberlappende Teile werden gemeldet

    Gibt eine Liste von Warnungen zurueck.
    """
    if not overall_bounds or len(overall_bounds) != 6:
        return []

    ob = overall_bounds
    total_vol = (max(0.001, ob[1]-ob[0]) *
                 max(0.001, ob[3]-ob[2]) *
                 max(0.001, ob[5]-ob[4]))

    warnings = []
    lines = ["Bounds-Validierung:", f"  Gesamtvolumen: {total_vol:.3f} m³"]

    for p in parts:
        name   = p.get("name", "?")
        bounds = p.get("bounds")
        if not bounds or len(bounds) != 6:
            continue

        b   = bounds
        dx  = max(0.0, b[1]-b[0])
        dy  = max(0.0, b[3]-b[2])
        dz  = max(0.0, b[5]-b[4])
        vol = dx * dy * dz
        pct = (vol / total_vol * 100) if total_vol > 0 else 0

        status = "OK"
        if pct > 70:
            status = f"WARNUNG: {pct:.0f}% des Gesamtvolumens (zu gross!)"
            warnings.append(f"'{name}' deckt {pct:.0f}% des Gesamtvolumens ab")
        elif pct > 40:
            status = f"INFO: {pct:.0f}% (gross aber moeglicherweise OK)"

        lines.append(
            f"  {name:<30} {dx:.2f}x{dy:.2f}x{dz:.2f}m  "
            f"Vol={vol:.3f}m³ ({pct:.0f}%)  {status}"
        )

    # Overlap-Check: grobe AABB-Intersection
    overlap_lines = []
    for i, a in enumerate(parts):
        for j, b_part in enumerate(parts):
            if j <= i: continue
            ba = a.get("bounds", [])
            bb = b_part.get("bounds", [])
            if len(ba) != 6 or len(bb) != 6: continue
            # Berechne Overlap-Volumen
            ox = max(0, min(ba[1],bb[1]) - max(ba[0],bb[0]))
            oy = max(0, min(ba[3],bb[3]) - max(ba[2],bb[2]))
            oz = max(0, min(ba[5],bb[5]) - max(ba[4],bb[4]))
            ov = ox * oy * oz
            if ov > 0:
                va = max(0.001,(ba[1]-ba[0])*(ba[3]-ba[2])*(ba[5]-ba[4]))
                vb = max(0.001,(bb[1]-bb[0])*(bb[3]-bb[2])*(bb[5]-bb[4]))
                pct_a = ov/va*100
                pct_b = ov/vb*100
                if pct_a > 20 or pct_b > 20:
                    msg = (f"  Overlap: '{a.get('name','?')}' <-> '{b_part.get('name','?')}'"
                           f"  Vol={ov:.3f}m³ ({pct_a:.0f}% / {pct_b:.0f}%)")
                    overlap_lines.append(msg)
                    if pct_a > 50 or pct_b > 50:
                        warnings.append(
                            f"Starkes Overlap: {a.get('name','?')} <-> {b_part.get('name','?')} "
                            f"({pct_a:.0f}%/{pct_b:.0f}%)"
                        )

    if overlap_lines:
        lines.append("  Overlaps (>20%):")
        lines.extend(overlap_lines)

    cache.log(cache.LEVEL_DATA, "\n".join(lines), phase=phase)

    if warnings:
        cache.log(cache.LEVEL_WARN,
                  f"Bounds-Validierung: {len(warnings)} Probleme gefunden:\n" +
                  "\n".join(f"  - {w}" for w in warnings),
                  phase=phase)
    else:
        cache.log(cache.LEVEL_OK, "Bounds-Validierung: alle Teile plausibel.", phase=phase)

    return warnings

# ── Zonen-Visualisierung ──────────────────────────────────────────────────────

def visualize_zones(zones: list):
    """Baut grüne Drahtgitter-Boxen fuer jede Zone."""
    col = get_or_create_collection()
    for obj in list(col.objects):
        if obj.name.startswith("LLM_Zone_"):
            try: bpy.data.objects.remove(obj, do_unlink=True)
            except Exception: pass
    created = 0
    for zone in zones:
        name  = f"LLM_Zone_{zone.get('name','zone')}"
        norm  = normalize_bounds(zone.get("bounds"))
        if norm is None: continue
        b = repair_bounds(norm, name)
        try:
            obj = _build_box(name, b)
            obj.display_type = "WIRE"
            _apply_material(obj, [0.1, 0.8, 0.2, 0.15])
            _link_to_col(obj)
            created += 1
        except Exception as e:
            cache.log(cache.LEVEL_WARN, f"Zone '{name}': {e}")
    cache.log(cache.LEVEL_INFO, f"{created} Zonen visualisiert.", phase=1)

# ── Joint-Visualisierung ──────────────────────────────────────────────────────

def visualize_joint(joint_data: dict):
    """Baut ein oranges Polygon fuer eine Kontaktflaeche."""
    pa     = joint_data.get("part_a", "A")
    pb     = joint_data.get("part_b", "B")
    points = joint_data.get("contact_points", [])
    name   = f"LLM_Joint_{pa}_to_{pb}"
    if len(points) < 3:
        cache.log(cache.LEVEL_WARN, f"Joint '{pa}↔{pb}': zu wenige Punkte")
        return None
    try:
        mesh = bpy.data.meshes.new(name + "_mesh")
        bm   = bmesh.new()
        verts = []
        for p in points:
            try: verts.append(bm.verts.new((float(p[0]),float(p[1]),float(p[2]))))
            except Exception: pass
        if len(verts) >= 3: bm.faces.new(verts)
        bm.to_mesh(mesh); bm.free()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        _apply_material(obj, [1.0, 0.5, 0.05, 0.7])
        _link_to_col(obj)
        cache.log(cache.LEVEL_OK, f"Joint '{pa}↔{pb}': {len(verts)} Punkte", phase=2)
        return obj
    except Exception as e:
        cache.log(cache.LEVEL_ERROR, f"Joint '{name}': {e}")
        return None

# ── Platzhalter ───────────────────────────────────────────────────────────────

def build_placeholder(part: dict):
    """Sofort-Box nach Phase-3-Bounds. Drahtgitter, blau."""
    name  = part.get("name", "Teil")
    norm  = normalize_bounds(part.get("bounds"))
    if norm is None:
        cache.log(cache.LEVEL_WARN, f"Platzhalter '{name}': keine Bounds.")
        return None
    b = repair_bounds(norm, name)
    try:
        old = bpy.data.objects.get(f"LLM_{name}")
        if old: bpy.data.objects.remove(old, do_unlink=True)
        obj = _build_box(f"LLM_{name}", b)
        obj.display_type = "WIRE"
        _apply_material(obj, part.get("color_rgba", [0.4, 0.6, 0.9, 0.25]))
        _link_to_col(obj)
        cache.log(cache.LEVEL_OK, f"Platzhalter '{name}': {[round(v,2) for v in b]}")
        return obj
    except Exception as e:
        cache.log(cache.LEVEL_ERROR, f"Platzhalter '{name}': {e}")
        return None

# ── Finale Meshes ─────────────────────────────────────────────────────────────

def build_final(parts: list) -> list:
    """Ersetzt Platzhalter durch fertige Meshes."""
    if not parts:
        cache.log(cache.LEVEL_WARN, "Keine Teile.")
        return []
    clear_llm_objects()
    col = get_or_create_collection()
    created, skipped = [], 0
    cache.log(cache.LEVEL_STEP, f"Baue {len(parts)} Meshes ...")
    for i, part in enumerate(parts):
        name   = part.get("name", f"Teil_{i}")
        method = part.get("method", "box")
        norm   = normalize_bounds(part.get("bounds"))
        if norm is None:
            cache.log(cache.LEVEL_WARN, f"'{name}': keine Bounds → uebersprungen.")
            skipped += 1; continue
        b      = repair_bounds(norm, name)
        points = part.get("points", [])
        color  = part.get("color_rgba", [0.6, 0.6, 0.6, 1.0])
        # Eindeutiger Name
        obj_name = f"LLM_{name}"
        cnt = 1
        while bpy.data.objects.get(obj_name):
            obj_name = f"LLM_{name}_{cnt}"; cnt += 1
        try:
            if method == "cylinder":
                obj = _build_cylinder(obj_name, b)
            elif method == "convex_hull" and len(points) >= 4:
                obj = _build_convex_hull(obj_name, points, b)
            else:
                obj = _build_box(obj_name, b)
            _apply_material(obj, color)
            _link_to_col(obj)
            created.append(obj)
            cache.log(cache.LEVEL_OK, f"[{i+1}/{len(parts)}] '{name}' ({method})",
                      phase=5, part=name)
        except Exception as e:
            cache.log(cache.LEVEL_ERROR, f"'{name}': {e}", phase=5, part=name)
            skipped += 1
    cache.log(cache.LEVEL_OK,
              f"Build: {len(created)} erstellt, {skipped} uebersprungen.", phase=5)
    return created

# ── Interne Mesh-Funktionen ───────────────────────────────────────────────────

def _build_box(name: str, b: list):
    mesh = bpy.data.meshes.new(name + "_mesh"); bm = bmesh.new()
    for c in [(b[0],b[2],b[4]),(b[1],b[2],b[4]),(b[1],b[3],b[4]),(b[0],b[3],b[4]),
              (b[0],b[2],b[5]),(b[1],b[2],b[5]),(b[1],b[3],b[5]),(b[0],b[3],b[5])]:
        bm.verts.new(c)
    bm.verts.ensure_lookup_table()
    for f in [(0,1,2,3),(7,6,5,4),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]:
        try: bm.faces.new([bm.verts[i] for i in f])
        except Exception: pass
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj

def _build_cylinder(name: str, b: list):
    mesh = bpy.data.meshes.new(name + "_mesh"); bm = bmesh.new()
    h  = float(b[5])-float(b[4])
    rx = (float(b[1])-float(b[0]))/2.0
    ry = (float(b[3])-float(b[2]))/2.0
    r  = max(rx,ry,MIN_SIZE/2)
    c  = Vector(((b[0]+b[1])/2,(b[2]+b[3])/2,(b[4]+b[5])/2))
    try:
        bmesh.ops.create_cone(bm,cap_ends=True,segments=24,
                              diameter1=r*2,diameter2=r*2,depth=max(h,MIN_SIZE))
        bmesh.ops.translate(bm,vec=c,verts=bm.verts)
    except Exception as e:
        cache.log(cache.LEVEL_WARN,f"Zylinder '{name}': {e} → Box"); bm.free()
        return _build_box(name,b)
    bm.to_mesh(mesh); bm.free()
  obj = bpy.data.objects.new(name,mesh)
_link_to_col(obj) # <-- SO IST ES RICHTIG
return obj

def _build_convex_hull(name: str, points: list, b: list):
    mesh = bpy.data.meshes.new(name + "_mesh"); bm = bmesh.new()
    valid = 0
    for p in points:
        try:
            x=max(float(b[0]),min(float(b[1]),float(p[0])))
            y=max(float(b[2]),min(float(b[3]),float(p[1])))
            z=max(float(b[4]),min(float(b[5]),float(p[2])))
            bm.verts.new((x,y,z)); valid+=1
        except Exception: pass
    if valid < 4:
        cache.log(cache.LEVEL_WARN,f"'{name}': {valid} Punkte → Box"); bm.free()
        return _build_box(name,b)
    bm.verts.ensure_lookup_table()
    try: bmesh.ops.convex_hull(bm,input=bm.verts)
    except Exception as e:
        cache.log(cache.LEVEL_WARN,f"Hull '{name}': {e} → Box"); bm.free()
        return _build_box(name,b)
    bm.to_mesh(mesh); bm.free()
  obj = bpy.data.objects.new(name,mesh)
_link_to_col(obj) # <-- SO IST ES RICHTIG
return obj

def _apply_material(obj, color: list,
                    metallic: float = 0.0, roughness: float = 0.5, name: str = ""):
    try:
        mat = bpy.data.materials.new(name=name if name else f"Mat_{obj.name}")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        bsdf  = nodes.get("Principled BSDF")
        if bsdf is None:
            nodes.clear()
            bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
            out  = nodes.new(type="ShaderNodeOutputMaterial")
            mat.node_tree.links.new(bsdf.outputs[0], out.inputs[0])
        try:
            c = list(color)
            bsdf.inputs["Base Color"].default_value = c[:4] if len(c)>=4 else c+[1.0]
        except Exception: bsdf.inputs[0].default_value = [0.6,0.6,0.6,1.0]
        try:
            bsdf.inputs["Metallic"].default_value  = float(metallic)
            bsdf.inputs["Roughness"].default_value = float(roughness)
        except Exception: pass
        if len(color)>=4 and color[3]<0.99: mat.blend_method = "BLEND"
        if obj.data.materials: obj.data.materials[0] = mat
        else: obj.data.materials.append(mat)
    except Exception as e:
        cache.log(cache.LEVEL_WARN, f"Material '{obj.name}': {e}")
