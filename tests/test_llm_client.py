"""
test_llm_client.py
══════════════════════════════════════════
Unit-Tests für llm_client.extract_json() und Hilfsfunktionen.
Keine Netzwerkverbindung erforderlich – urllib wird gemockt.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from text_to_blender import llm_client


# ── extract_json ──────────────────────────────────────────────────────────────

class TestExtractJson:

    def test_valid_json_directly(self):
        raw = '{"name": "Teil_A", "bounds": [0, 1, 0, 1, 0, 1]}'
        result = json.loads(llm_client.extract_json(raw))
        assert result["name"] == "Teil_A"
        assert result["bounds"] == [0, 1, 0, 1, 0, 1]

    def test_json_in_markdown_json_block(self):
        raw = '```json\n{"key": "value"}\n```'
        result = json.loads(llm_client.extract_json(raw))
        assert result["key"] == "value"

    def test_json_in_generic_code_block(self):
        raw = '```\n{"answer": 42}\n```'
        result = json.loads(llm_client.extract_json(raw))
        assert result["answer"] == 42

    def test_json_embedded_in_text(self):
        raw = 'Hier ist meine Antwort:\n{"x": 1.5, "y": -0.3}'
        result = json.loads(llm_client.extract_json(raw))
        assert result["x"] == pytest.approx(1.5)

    def test_nested_json_object(self):
        raw = '{"outer": {"inner": [1, 2, 3]}}'
        result = json.loads(llm_client.extract_json(raw))
        assert result["outer"]["inner"] == [1, 2, 3]

    def test_json_with_unicode(self):
        raw = '{"name": "Rädchen", "color": "grün"}'
        result = json.loads(llm_client.extract_json(raw))
        assert result["name"] == "Rädchen"

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Leerer Text"):
            llm_client.extract_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="Leerer Text"):
            llm_client.extract_json("   \n\t  ")

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            llm_client.extract_json("Dies ist kein JSON-Text.")

    def test_extra_text_around_json_block(self):
        raw = "Hier ist das Ergebnis:\n```json\n{\"ok\": true}\n```\nFertig!"
        result = json.loads(llm_client.extract_json(raw))
        assert result["ok"] is True

    def test_bounds_array_structure(self):
        """Typisches Phase-2-Ergebnis: name + bounds-Array."""
        raw = '{"name": "Rad_vorne_links", "bounds": [-0.1, 0.1, -0.5, -0.3, 0.0, 0.2]}'
        result = json.loads(llm_client.extract_json(raw))
        assert result["name"] == "Rad_vorne_links"
        assert len(result["bounds"]) == 6

    def test_materials_array(self):
        """Typisches Phase-5-Ergebnis: materials-Array."""
        raw = json.dumps({
            "materials": [
                {"name": "Karosserie", "color_rgba": [0.8, 0.1, 0.1, 1.0],
                 "metallic": 0.0, "roughness": 0.5}
            ]
        })
        result = json.loads(llm_client.extract_json(raw))
        assert len(result["materials"]) == 1
        assert result["materials"][0]["name"] == "Karosserie"

    def test_result_is_always_valid_json_string(self):
        """extract_json soll immer einen re-parsebaren JSON-String zurückgeben."""
        raw = '{"a": 1}'
        output = llm_client.extract_json(raw)
        # muss re-parsebar sein
        json.loads(output)

    def test_float_values_preserved(self):
        raw = '{"metallic": 0.75, "roughness": 0.25}'
        result = json.loads(llm_client.extract_json(raw))
        assert result["metallic"] == pytest.approx(0.75)
        assert result["roughness"] == pytest.approx(0.25)


# ── is_busy / cancel ──────────────────────────────────────────────────────────

class TestClientState:

    def test_initially_not_busy(self):
        # Sicherstellen dass kein vorheriger Test den Status liegen lässt
        llm_client.cancel()
        assert llm_client.is_busy() is False

    def test_cancel_clears_busy(self):
        # Direktes Setzen von _busy über das Modul-Internals (White-Box)
        llm_client._busy = True
        llm_client.cancel()
        assert llm_client.is_busy() is False

    def test_is_running_alias(self):
        """is_running soll ein Alias für is_busy sein."""
        assert llm_client.is_running is llm_client.is_busy


# ── check_connection (gemockt) ────────────────────────────────────────────────

class TestCheckConnection:

    def test_empty_host_returns_false(self):
        ok, msg = llm_client.check_connection("")
        assert ok is False
        assert "Host" in msg

    def test_none_host_returns_false(self):
        ok, msg = llm_client.check_connection(None)
        assert ok is False

    def test_successful_connection(self):
        """Simuliert eine erfolgreiche Antwort von /api/tags."""
        fake_body = json.dumps({
            "models": [{"name": "qwen2.5-coder:7b"}]
        }).encode()
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__  = MagicMock(return_value=False)
        fake_resp.status    = 200
        fake_resp.read      = MagicMock(return_value=fake_body)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            ok, msg = llm_client.check_connection("http://localhost:11434")

        assert ok is True
        assert "qwen2.5-coder:7b" in msg

    def test_http_error_returns_false(self):
        """Simuliert einen Netzwerkfehler."""
        import urllib.error
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("Connection refused")):
            ok, msg = llm_client.check_connection("http://localhost:11434")

        assert ok is False
        assert "Verbindungsfehler" in msg or "Connection refused" in msg


# ── generate_async (gemockt) ──────────────────────────────────────────────────

class TestGenerateAsync:

    def setup_method(self):
        # Sauberen Zustand sicherstellen
        llm_client.cancel()

    def test_returns_false_when_busy(self):
        """Wenn bereits beschäftigt, soll False zurückgegeben werden."""
        llm_client._busy = True
        try:
            result = llm_client.generate_async(
                prompt="test",
                system_prompt=None,
                model="qwen2.5-coder:7b",
                host="http://localhost:11434",
                phase=0,
                part_name="test_part",
                on_done=lambda raw, err: None,
            )
            assert result is False
        finally:
            llm_client._busy = False

    def test_on_done_called_with_json(self):
        """Simuliert einen erfolgreichen LLM-Call und prüft den Callback."""
        import threading

        fake_content = json.dumps({"name": "Rad", "bounds": [0, 1, 0, 1, 0, 1]})
        fake_response_body = json.dumps({
            "message": {"content": fake_content}
        }).encode()

        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__  = MagicMock(return_value=False)
        fake_resp.read      = MagicMock(return_value=fake_response_body)

        done_event = threading.Event()
        results = {}

        def on_done(raw, err):
            results["raw"] = raw
            results["err"] = err
            done_event.set()

        with patch("urllib.request.urlopen", return_value=fake_resp):
            ok = llm_client.generate_async(
                prompt='{"name": "Rad", "bounds": [0, 1, 0, 1, 0, 1]}',
                system_prompt=None,
                model="qwen2.5-coder:7b",
                host="http://localhost:11434",
                phase=0,
                part_name="rad",
                on_done=on_done,
                timeout=5.0,
            )

        assert ok is True
        done_event.wait(timeout=5.0)
        assert results.get("err") is None
        parsed = json.loads(results["raw"])
        assert parsed["name"] == "Rad"
