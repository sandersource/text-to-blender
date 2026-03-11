# Text to Blender
**Universeller Text-to-3D Generator für Blender — Meshes, Materialien, Animationen, Scripte**

Version: **7.0.0** (Zoom-In Pipeline)  
Modell: `qwen2.5-coder:7b` via [Ollama](https://ollama.ai) (lokal, keine Cloud erforderlich)  
Blender: 4.0+

---

## Beschreibung

Dieses Addon generiert 3D-Objekte in Blender aus einem frei formulierten Textprompt.
Die Pipeline zerlegt jeden Prompt automatisch in kleine, für ein 7B-Modell handhabbare Teilaufgaben
(„Zoom-In Pipeline"), damit auch auf Consumer-Hardware präzise Ergebnisse entstehen.

**Features:**
- Universelle Pipeline: Meshes, Materialien, Animationen, Scripte
- Vollständig lokal / offline (kein OpenAI-Key erforderlich)
- Hierarchische Pipeline mit 8 Sub-Phasen — jeder LLM-Call = EINE einfache Frage
- ASCII-Draufsicht-Skizze als räumlicher Kontext bei der Bauteil-Platzierung
- Retry-Loop mit Fehlerbeschreibung (bis zu 2 Retries pro Teil)
- Dynamische Teile-Limits basierend auf Objekt-Volumen
- Symmetrie-Expansion (mirror_Y, radial_N)
- Thread-sicheres Design für Blender

---

## Pipeline v7.0.0 — Zoom-In

| Phase | Was passiert | LLM-Calls |
|-------|-------------|-----------|
| **0a** | WAS ist es? (Typ, Kategorie, Symmetrie) | 1 |
| **0b** | Wie GROSS? (Dimensionen + overall_bounds) | 1 |
| **1a** | Welche HAUPTTEILE? (max 6–8 Baugruppen) | 1 |
| **1b** | Pro Baugruppe → Einzelteile | N |
| **2**  | Pro Teil → Bounds (mit ASCII-Skizze + Retry) | ≤ max_bounds_parts × 3 |
| **3**  | Pro convex_hull-Teil → Pointcloud | ≤ max_pointcloud_parts |
| **4**  | Mesh-Bau im Blender-Main-Thread | 0 |
| **5**  | Materialien | 1 |

---

## Vergleich mit ähnlichen Projekten

> Stand: März 2026 — aktiver Entwicklungsbereich, Lage kann sich schnell ändern.

| Projekt | Ansatz | Lokale LLM | Mesh-Bau | Pipeline | Blender-Version |
|---------|--------|-----------|----------|----------|-----------------|
| **Text to Blender** *(dieses Projekt)* | Hierarchische Zoom-In Pipeline — Bounds & Meshes direkt in bpy | ✅ Ollama | ✅ direkt (bpy) | 8 Sub-Phasen, Retry, ASCII-Kontext | 4.0+ |
| [mac999/blender-llm-addin](https://github.com/mac999/blender-llm-addin) | LLM generiert Python-Script → exec() | ✅ Ollama + OpenAI | ⚠️ via Script | 1-Shot Script-Generierung | 3.x/4.x |
| [gd3kr/BlenderGPT](https://github.com/gd3kr/BlenderGPT) | GPT-4 generiert Python-Script → exec() | ❌ nur OpenAI | ⚠️ via Script | 1-Shot | 3.x |
| [Technologic101/prompt2Blend](https://github.com/Technologic101/prompt2Blend) | Script-Generierung mit RAG-Kontext | ✅ Ollama + OpenAI | ⚠️ via Script | RAG-Unterstützung | 4.4+ |
| [FreedomIntelligence/BlenderLLM](https://github.com/FreedomIntelligence/BlenderLLM) | Speziell fine-getuntes LLM (Qwen2.5-Coder-7B) für CAD-Scripting | ✅ eigene Weights | ⚠️ via Script | Forschungsprojekt | 3.x |
| [anders94/blender-llm](https://github.com/anders94/blender-llm) | Chat-Interface → Blender-Script | ✅ Ollama | ⚠️ via Script | Chat-only | 4.x |
| [tin2tin/LLM4Blender](https://github.com/tin2tin/LLM4Blender) | Multi-Modal (Code + Bild-Prompts + Screenplay) | ✅ Ollama | ⚠️ via Script | Text-Editor-basiert | 4.0+ |
| [MeshGen/LLaMA-Mesh](https://github.com/nv-tlabs/LLaMA-Mesh) | LLM gibt Vertex/Face-Listen aus (Mesh als Text) | ⚠️ NVIDIA-Modell | ✅ direkt | Forschung, experimentell | beliebig |

### Wichtigste Unterschiede

**Script-Generierung (die meisten Projekte) vs. direkte Mesh-Konstruktion (dieses Projekt):**

Die meisten vergleichbaren Addons lassen das LLM Python-Code generieren, der dann per `exec()` ausgeführt wird.
Das hat Vorteile (flexibel, kann alles was bpy kann), aber auch Nachteile:
- 7B-Modelle erzeugen oft syntaktisch falschen oder semantisch sinnlosen Code
- Kein räumlicher Zusammenhang über mehrere Objekte hinweg
- Kein Retry-Mechanismus für einzelne Teile

**Text to Blender** geht einen anderen Weg:
Das LLM liefert nur strukturierte Daten (JSON mit Bounds, Methode, Farbe).
Der Python-Code für Blender wird vollständig im Addon selbst geschrieben.
Das erlaubt präzise Validierung, räumlichen Kontext via ASCII-Skizze und gezielte Retries.

**Stärken dieses Projekts:**
- Optimiert für 7B-Modelle auf Consumer-Hardware (keine GPU-Serverinfrastruktur)
- Vollständig offline / kein API-Key
- Validierung + Retry für jedes einzelne Bauteil
- ASCII-Skizze gibt dem LLM räumliches Feedback

**Wo andere Projekte besser sind:**
- Wenn komplexe, kreative Blender-Operationen (Modifier, Partikel, Shader-Nodes) benötigt werden → Script-Generierung (BlenderGPT, blender-llm-addin)
- Wenn GPT-4 / Cloud-API-Qualität benötigt wird → BlenderGPT
- Wenn RAG / Wissensdatenbank benötigt wird → Prompt2Blend
- Wenn Forschung / fine-tuning interessiert → BlenderLLM (FreedomIntelligence)

---

## Installation

1. Ollama installieren: https://ollama.ai
2. Modell laden: `ollama pull qwen2.5-coder:7b`
3. Diesen Ordner als Blender-Addon installieren (ZIP oder Ordner in Blender Preferences → Add-ons)
4. In Blender: View3D → Sidebar (N) → Tab „LLM"

## Verwendung

1. Ollama starten: `ollama serve`
2. Prompt eingeben (z.B. *„erstelle ein Gamepad"*)
3. „Pipeline starten" klicken
4. Warten — die Pipeline arbeitet in Sub-Phasen (im Fortschrittsbalken sichtbar)
