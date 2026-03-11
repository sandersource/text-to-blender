"""
test_cache.py
══════════════════════════════════════════
Unit-Tests für cache.py:
  - Projektordner-Verwaltung
  - Logging
  - Cache I/O (save_step / load_step)
  - Raw-Speicherung
  - clear_cache
"""

import json
import os
import pytest
from text_to_blender import cache


# ── Fixture: sauberes temporäres Projektverzeichnis ───────────────────────────

@pytest.fixture(autouse=True)
def tmp_project(tmp_path):
    """Jeder Test bekommt ein frisches, leeres Projektverzeichnis."""
    cache.set_project_dir(str(tmp_path))
    yield tmp_path
    # Aufräumen: Projektordner zurücksetzen (optional, verhindert Seiteneffekte)
    cache.set_project_dir(str(tmp_path))


# ── Projektordner-Verwaltung ──────────────────────────────────────────────────

class TestProjectDir:

    def test_set_and_get_project_dir(self, tmp_path):
        new_dir = str(tmp_path / "myproject")
        cache.set_project_dir(new_dir)
        assert cache.get_project_dir() == new_dir

    def test_set_project_dir_creates_subdirs(self, tmp_path):
        new_dir = str(tmp_path / "proj")
        cache.set_project_dir(new_dir)
        assert os.path.isdir(cache.get_cache_dir())
        assert os.path.isdir(cache.get_raw_dir())

    def test_get_log_path_inside_project(self, tmp_path):
        assert cache.get_log_path().startswith(str(tmp_path))
        assert cache.get_log_path().endswith("pipeline.log")

    def test_get_cache_dir_inside_project(self, tmp_path):
        assert cache.get_cache_dir().startswith(str(tmp_path))

    def test_get_raw_dir_inside_project(self, tmp_path):
        assert cache.get_raw_dir().startswith(str(tmp_path))

    def test_get_parts_list_path(self, tmp_path):
        path = cache.get_parts_list_path()
        assert path.startswith(str(tmp_path))
        assert "parts_list" in path

    def test_get_joints_list_path(self, tmp_path):
        path = cache.get_joints_list_path()
        assert path.startswith(str(tmp_path))
        assert "joints_list" in path


# ── Logging ───────────────────────────────────────────────────────────────────

class TestLogging:

    def test_log_creates_file(self):
        cache.log(cache.LEVEL_INFO, "Test-Eintrag")
        assert os.path.exists(cache.get_log_path())

    def test_log_entry_in_file(self):
        cache.log(cache.LEVEL_INFO, "Hallo Welt")
        with open(cache.get_log_path(), encoding="utf-8") as f:
            content = f.read()
        assert "Hallo Welt" in content

    def test_log_level_ok_in_file(self):
        cache.log(cache.LEVEL_OK, "Alles gut")
        with open(cache.get_log_path(), encoding="utf-8") as f:
            content = f.read()
        assert cache.LEVEL_OK.strip() in content

    def test_log_level_error_in_file(self):
        cache.log(cache.LEVEL_ERROR, "Fehler aufgetreten")
        with open(cache.get_log_path(), encoding="utf-8") as f:
            content = f.read()
        assert "Fehler aufgetreten" in content

    def test_log_multiline_message(self):
        cache.log(cache.LEVEL_INFO, "Zeile 1\nZeile 2\nZeile 3")
        with open(cache.get_log_path(), encoding="utf-8") as f:
            content = f.read()
        assert "Zeile 1" in content
        assert "Zeile 2" in content

    def test_log_separator_creates_entry(self):
        cache.log_separator("Test-Separator")
        with open(cache.get_log_path(), encoding="utf-8") as f:
            content = f.read()
        assert "Test-Separator" in content

    def test_log_with_phase_and_part(self):
        cache.log(cache.LEVEL_INFO, "Phase-Test", phase=2, part="Rad_vorne")
        with open(cache.get_log_path(), encoding="utf-8") as f:
            content = f.read()
        assert "Phase-Test" in content
        assert "Rad_vorne" in content

    def test_log_appends_multiple_entries(self):
        cache.log(cache.LEVEL_INFO, "Erster Eintrag")
        cache.log(cache.LEVEL_INFO, "Zweiter Eintrag")
        with open(cache.get_log_path(), encoding="utf-8") as f:
            content = f.read()
        assert "Erster Eintrag" in content
        assert "Zweiter Eintrag" in content

    def test_log_json(self):
        data = {"test": True, "wert": 42}
        cache.log_json("Testdaten", data, phase=1)
        with open(cache.get_log_path(), encoding="utf-8") as f:
            content = f.read()
        assert "Testdaten" in content
        assert '"wert"' in content


# ── Cache I/O ─────────────────────────────────────────────────────────────────

class TestCacheIO:

    def test_save_and_load_step(self):
        data = {"assemblies": [{"name": "Rahmen", "estimated_parts": 4}]}
        cache.save_step(1, data)
        loaded = cache.load_step(1)
        assert loaded == data

    def test_load_nonexistent_step_returns_empty_dict(self):
        result = cache.load_step(99)
        assert result == {}

    def test_save_step_invalid_key_returns_false(self):
        result = cache.save_step(99, {"x": 1})
        assert result is False

    def test_save_step_creates_file(self):
        cache.save_step(0, {"object_type": "Haus"})
        expected = os.path.join(cache.get_cache_dir(), "phase0_classify.json")
        assert os.path.exists(expected)

    def test_all_valid_step_keys(self):
        for step in (0, 1, 2, 3, 4, 5):
            payload = {"step": step}
            ok = cache.save_step(step, payload)
            assert ok is True, f"save_step({step}) schlug fehl"
            loaded = cache.load_step(step)
            assert loaded == payload, f"load_step({step}) stimmt nicht überein"

    def test_save_step_overwrites_existing(self):
        cache.save_step(0, {"v": 1})
        cache.save_step(0, {"v": 2})
        assert cache.load_step(0) == {"v": 2}

    def test_loaded_data_is_deep_equal(self):
        data = {
            "materials": [
                {"name": "Stahl", "metallic": 1.0, "roughness": 0.1,
                 "color_rgba": [0.7, 0.72, 0.75, 1.0]}
            ]
        }
        cache.save_step(5, data)
        assert cache.load_step(5) == data


# ── Raw-Speicherung ───────────────────────────────────────────────────────────

class TestSaveRaw:

    def test_save_raw_creates_file(self):
        cache.save_raw('{"key": "val"}', phase=2, part_name="Rad")
        raw_dir = cache.get_raw_dir()
        files = os.listdir(raw_dir)
        assert len(files) == 1
        assert files[0].startswith("phase2_Rad")

    def test_save_raw_content_matches(self):
        content = '{"bounds": [0, 1, 0, 1, 0, 1]}'
        cache.save_raw(content, phase=3, part_name="Achse")
        raw_dir = cache.get_raw_dir()
        fpath = os.path.join(raw_dir, os.listdir(raw_dir)[0])
        with open(fpath, encoding="utf-8") as f:
            assert f.read() == content

    def test_save_raw_without_part_name(self):
        cache.save_raw("allgemein", phase=0)
        files = os.listdir(cache.get_raw_dir())
        assert len(files) == 1
        assert "phase0" in files[0]

    def test_save_raw_empty_string(self):
        cache.save_raw("", phase=1, part_name="leer")
        files = os.listdir(cache.get_raw_dir())
        assert len(files) == 1


# ── clear_cache ───────────────────────────────────────────────────────────────

class TestClearCache:

    def test_clear_cache_removes_log(self):
        cache.log(cache.LEVEL_INFO, "Wird gelöscht")
        cache.clear_cache()
        # Log-Datei kann nach clear_cache wieder angelegt werden
        assert not os.path.exists(cache.get_log_path()) or \
               os.path.getsize(cache.get_log_path()) < 200  # nur neuer Eintrag

    def test_clear_cache_removes_step_files(self):
        cache.save_step(0, {"x": 1})
        cache.clear_cache()
        assert cache.load_step(0) == {}

    def test_clear_cache_removes_raw_files(self):
        cache.save_raw("test", phase=1, part_name="foo")
        cache.clear_cache()
        assert os.listdir(cache.get_raw_dir()) == []

    def test_clear_cache_recreates_dirs(self):
        cache.clear_cache()
        assert os.path.isdir(cache.get_cache_dir())
        assert os.path.isdir(cache.get_raw_dir())

    def test_clear_cache_allows_new_saves(self):
        cache.clear_cache()
        cache.save_step(2, {"bounds": []})
        assert cache.load_step(2) == {"bounds": []}


# ── Level-Konstanten ──────────────────────────────────────────────────────────

class TestLevelConstants:

    def test_all_levels_are_strings(self):
        levels = [
            cache.LEVEL_INFO, cache.LEVEL_OK,   cache.LEVEL_WARN,
            cache.LEVEL_ERROR, cache.LEVEL_STEP, cache.LEVEL_LLM,
            cache.LEVEL_DATA,
        ]
        for lvl in levels:
            assert isinstance(lvl, str), f"Level {lvl!r} ist kein String"
            assert len(lvl.strip()) > 0

    def test_levels_are_distinct(self):
        levels = [
            cache.LEVEL_INFO, cache.LEVEL_OK,   cache.LEVEL_WARN,
            cache.LEVEL_ERROR, cache.LEVEL_STEP, cache.LEVEL_LLM,
            cache.LEVEL_DATA,
        ]
        assert len(set(levels)) == len(levels), "Doppelte Level-Konstanten"
