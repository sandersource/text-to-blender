"""
test_prompts.py
═══════════════════════════════════════
Smoke-Tests: Alle Prompt-Konstanten in prompts.py müssen
nicht-leere Strings sein und die für die Pipeline obligatorischen
Schlüsselwörter enthalten.
"""

import pytest
from text_to_blender import prompts


# ── Hilfsfunktion ─────────────────────────────────────────────────────────────

def _all_prompt_names():
    """Alle öffentlichen Konstanten aus prompts.py (Strings)."""
    return [
        name for name in dir(prompts)
        if name.startswith("PHASE_") and isinstance(getattr(prompts, name), str)
    ]


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", _all_prompt_names())
def test_prompt_is_non_empty_string(name):
    """Jeder Prompt muss ein nicht-leerer String sein."""
    value = getattr(prompts, name)
    assert isinstance(value, str)
    assert len(value.strip()) > 0, f"{name} ist leer"


@pytest.mark.parametrize("name", _all_prompt_names())
def test_prompt_contains_json_instruction(name):
    """Jeder Prompt muss eine JSON-Ausgabe-Anweisung enthalten."""
    value = getattr(prompts, name)
    assert "JSON" in value.upper(), (
        f"{name} enthält keine JSON-Anweisung"
    )


def test_phase_0a_required_fields():
    p = prompts.PHASE_0A_TYPE
    for field in ("object_type", "category", "complexity"):
        assert field in p, f"PHASE_0A_TYPE: Feld '{field}' fehlt"


def test_phase_0b_required_fields():
    p = prompts.PHASE_0B_SIZE
    for field in ("dimensions_m", "overall_bounds"):
        assert field in p, f"PHASE_0B_SIZE: Feld '{field}' fehlt"


def test_phase_1a_required_fields():
    p = prompts.PHASE_1A_MAIN_PARTS
    assert "assemblies" in p, "PHASE_1A_MAIN_PARTS: Feld 'assemblies' fehlt"


def test_phase_1b_required_fields():
    p = prompts.PHASE_1B_SUB_PARTS
    for field in ("assembly", "parts", "method"):
        assert field in p, f"PHASE_1B_SUB_PARTS: Feld '{field}' fehlt"


def test_phase_2_required_fields():
    p = prompts.PHASE_2_BOUNDS
    assert "bounds" in p, "PHASE_2_BOUNDS: Feld 'bounds' fehlt"


def test_phase_2_retry_required_fields():
    p = prompts.PHASE_2_RETRY
    assert "bounds" in p, "PHASE_2_RETRY: Feld 'bounds' fehlt"


def test_phase_3_required_fields():
    p = prompts.PHASE_3_POINTCLOUD
    assert "points" in p, "PHASE_3_POINTCLOUD: Feld 'points' fehlt"


def test_phase_5_required_fields():
    p = prompts.PHASE_5_MATERIALS
    for field in ("materials", "metallic", "roughness"):
        assert field in p, f"PHASE_5_MATERIALS: Feld '{field}' fehlt"


def test_coordinate_system_documented_in_bounds_prompts():
    """Bounds-Prompts müssen das Koordinatensystem beschreiben."""
    for name in ("PHASE_2_BOUNDS", "PHASE_2_RETRY", "PHASE_3_POINTCLOUD"):
        p = getattr(prompts, name)
        assert "X" in p and "Y" in p and "Z" in p, (
            f"{name}: Koordinatensystem-Beschreibung fehlt"
        )
