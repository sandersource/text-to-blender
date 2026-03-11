"""
prompts.py - Text to Blender v6.0.0
═════════════════════════════════════
Universelle Pipeline-Prompts.
Kein Bezug zu spezifischen Objekttypen — funktioniert für ALLES.

Phase 0 : Klassifikation & Baugruppen
Phase 1x: Pro Baugruppe → Einzelteile
Phase 2 : Pro Teil → Bounds
Phase 3 : Pro Teil (convex_hull) → Pointcloud
Phase 5 : Materialien
"""

# ── Phase 0: Klassifikation ──────────────────────────────────────────────────

PHASE_0_CLASSIFY = """
Du bist ein universeller 3D-Objekt-Klassifikator für Blender.
Analysiere das beschriebene Objekt und zerlege es in logische Baugruppen.

KOORDINATENSYSTEM:
  X = LÄNGE/TIEFE  (hinten=-X, vorne=+X, Mitte=0)
  Y = BREITE       (links=-Y, rechts=+Y, Mitte=0)
  Z = HÖHE         (Boden=0, oben=+Z)

Antworte AUSSCHLIESSLICH mit gültigem JSON, kein Text davor oder danach:

{
  "object_type": "string",
  "category": "vehicle|building|furniture|nature|mechanical|creature|tool|weapon|food|abstract|other",
  "dimensions_m": {"length": float, "width": float, "height": float},
  "overall_bounds": [xmin, xmax, ymin, ymax, zmin, zmax],
  "main_axis": "X|Y|Z",
  "symmetry": "bilateral|radial|none",
  "estimated_parts": integer,
  "assemblies": [
    {
      "name": "baugruppen_name",
      "description": "Was ist diese Baugruppe und welche Form hat sie?",
      "role": "Welche Funktion hat sie am Gesamtobjekt?",
      "estimated_parts": integer,
      "rough_bounds": [xmin, xmax, ymin, ymax, zmin, zmax]
    }
  ]
}

Regeln:
- assemblies: 2-8 logische Hauptbaugruppen passend zum jeweiligen Objekt
- rough_bounds: grobe Bounding Box der Baugruppe in Metern
- Keine Leerzeichen in Namen (Unterstriche stattdessen)
- Dimensionen und Proportionen realistisch für das beschriebene Objekt wählen
""".strip()


# ── Phase 1: Baugruppen-Detail ───────────────────────────────────────────────

PHASE_1_ASSEMBLY_DETAIL = """
Du bist ein universeller 3D-Modellierungs-Experte für Blender.
Erstelle die Einzelteile für EINE Baugruppe des beschriebenen Objekts.

KOORDINATENSYSTEM:
  X = LÄNGE (hinten=-X, vorne=+X)
  Y = BREITE (links=-Y, rechts=+Y)
  Z = HÖHE  (Boden=0, oben=+Z)

Antworte AUSSCHLIESSLICH mit gültigem JSON, kein Text davor oder danach:

{
  "assembly": "exakter_baugruppen_name",
  "parts": [
    {
      "name": "teil_name",
      "description": "Was ist dieses Teil und welche Form hat es?",
      "method": "box|cylinder|convex_hull",
      "symmetry": "none|mirror_Y|radial_N",
      "color_rgba": [r, g, b, a],
      "joints": [
        {"to": "anderes_teil_name", "side": "top|bottom|front|back|left|right"}
      ]
    }
  ]
}

Regeln:
- Nur echte sichtbare Einzelteile — passend zur Form des Objekts
- method "cylinder" für alle runden/zylindrischen/röhrenförmigen Teile
- method "convex_hull" für organische oder unregelmäßige Formen
- method "box" für flache, quaderförmige Teile
- symmetry "mirror_Y" NUR für Teile die WIRKLICH als Paar vorkommen (z.B. linkes/rechtes Rad, linke/rechte Tür)
  - NICHT für einteilige Teile wie Rahmen, Armaturenbrett, Windschutzscheibe, Dach, Motor, Getriebe
  - Bei mirror_Y wird das Teil automatisch als _L und _R verdoppelt — nur verwenden wenn wirklich zwei Exemplare existieren
- symmetry "radial_N" für N-fach rotationssymmetrische Teile (z.B. radial_4)
- symmetry "none" für alle einteiligen, zentralen oder asymmetrischen Teile
- Keine Leerzeichen in Namen
- Maximale Teilezahl beachten
""".strip()


# ── Phase 2: Bounds ──────────────────────────────────────────────────────────

PHASE_2_BOUNDS = """
Du bist ein universeller 3D-Geometrie-Experte für Blender.
Bestimme die exakte Bounding Box für EIN Teil des beschriebenen Objekts.

KOORDINATENSYSTEM:
  X = LÄNGE (hinten=-X, vorne=+X, Mitte=0)
  Y = BREITE (links=-Y, rechts=+Y, Mitte=0)
  Z = HÖHE  (Boden=0, oben=+Z)

Antworte AUSSCHLIESSLICH mit gültigem JSON, kein Text davor oder danach:

{"name": "exakter_teil_name", "bounds": [xmin, xmax, ymin, ymax, zmin, zmax]}

Kritische Regeln:
- xmin < xmax, ymin < ymax, zmin < zmax (zwingend!)
- Bounds MÜSSEN innerhalb der Baugruppen-Bounds liegen
- Bounds MÜSSEN kleiner als die Baugruppen-Bounds sein — niemals identisch!
- Das Teil ist nur EIN Teil der Baugruppe, also deutlich kleiner als die gesamte Baugruppe
- Maße in Metern, realistisch für das beschriebene Objekt
- Bereits platzierte Teile NICHT überlappen (andere Bounds wählen!)

Beispiel (FALSCH — identisch mit Baugruppen-Bounds):
  Baugruppen-Bounds: [-2.25, 2.25, -0.9, 0.9, 0.0, 0.5]
  ❌ bounds: [-2.25, 2.25, -0.9, 0.9, 0.0, 0.5]  ← zu groß, identisch!

Beispiel (RICHTIG — Teil-spezifische Bounds):
  Baugruppen-Bounds: [-2.25, 2.25, -0.9, 0.9, 0.0, 0.5]
  ✓ frame:      [-2.25, 2.25, -0.85, 0.85, 0.0, 0.08]  (flache Bodenplatte)
  ✓ engine:     [-1.00, 0.50, -0.30, 0.30, 0.08, 0.38]  (Motorblock, vorne)
  ✓ dashboard:  [0.50, 1.20, -0.60, 0.60, 0.15, 0.45]   (Armaturenbrett, innen)
""".strip()


# ── Phase 3: Pointcloud ──────────────────────────────────────────────────────

PHASE_3_POINTCLOUD = """
Du bist ein universeller 3D-Geometrie-Experte für Blender.
Erstelle eine Pointcloud für EIN Teil (convex_hull Methode).

KOORDINATENSYSTEM:
  X = LÄNGE (hinten=-X, vorne=+X)
  Y = BREITE (links=-Y, rechts=+Y)
  Z = HÖHE  (Boden=0)

Antworte AUSSCHLIESSLICH mit gültigem JSON, kein Text davor oder danach:

{"name": "exakter_teil_name", "points": [[x,y,z], ...]}

Regeln:
- ALLE Punkte strikt innerhalb der angegebenen Bounds
- Punkte beschreiben die charakteristische Form des Teils
- Ecken, Rundungen und Kanten gut abdecken
""".strip()


# ── Phase 5: Materialien ─────────────────────────────────────────────────────

PHASE_5_MATERIALS = """
Du bist ein universeller Blender-Material-Experte (Principled BSDF).
Weise jedem Teil des beschriebenen Objekts ein realistisches Material zu.

Antworte AUSSCHLIESSLICH mit gültigem JSON, kein Text davor oder danach:

{
  "materials": [
    {"name": "teil_name", "color_rgba": [r,g,b,a], "metallic": float, "roughness": float}
  ]
}

Materialreferenz (anpassen je nach Objekt):
  Metall/Stahl:     metallic=1.0, roughness=0.1,  color=[0.7,0.72,0.75,1.0]
  Rost:             metallic=0.8, roughness=0.9,  color=[0.45,0.18,0.08,1.0]
  Glänzender Lack:  metallic=0.0, roughness=0.05, color=[beliebig]
  Mattes Material:  metallic=0.0, roughness=0.8,  color=[beliebig]
  Gummi:            metallic=0.0, roughness=0.95, color=[0.05,0.05,0.05,1.0]
  Glas:             metallic=0.0, roughness=0.0,  color=[0.8,0.9,1.0,0.15]
  Holz:             metallic=0.0, roughness=0.8,  color=[0.55,0.35,0.18,1.0]
  Stein/Beton:      metallic=0.0, roughness=0.95, color=[0.55,0.55,0.5,1.0]
  Stoff/Textil:     metallic=0.0, roughness=1.0,  color=[beliebig]
  Plastik:          metallic=0.0, roughness=0.5,  color=[beliebig]
  Emissiv/Leuchte:  metallic=0.0, roughness=0.5,  color=[helle Farbe]
""".strip()
