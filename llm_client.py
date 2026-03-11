"""
llm_client.py - Text to Blender v6.0.0
════════════════════════════════════════
Echter Ollama HTTP-Client. Nur Python-Standardbibliothek (urllib).

- Endpunkt: /api/chat (stabiler als /api/generate)
- Robuste JSON-Extraktion aus LLM-Antworten
- Alle Aufrufe laufen in Hintergrund-Threads
- on_done(raw, error) → Pipeline registriert bpy.app.timers für Main-Thread
"""

import json, re, threading, urllib.request, urllib.error
from typing import Callable, Optional
from . import cache

_lock   = threading.Lock()
_busy   = False
_cancel = False


def check_connection(host: str, timeout: float = 5.0) -> tuple:
    """Testet Ollama-Erreichbarkeit via GET /api/tags und listet Modelle."""
    if not host:
        return False, "Kein Host angegeben."
    url = host.rstrip("/") + "/api/tags"
    cache.log(cache.LEVEL_INFO, f"Verbindungstest: {url}")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, method="GET"), timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                try:
                    models = [m.get("name", "?") for m in
                              json.loads(resp.read().decode()).get("models", [])]
                    msg = f"Verbunden. Modelle: {', '.join(models[:5]) or 'keine'}"
                except Exception:
                    msg = f"Verbunden (HTTP {resp.status})"
                cache.log(cache.LEVEL_OK, msg)
                return True, msg
            return False, f"HTTP {resp.status}"
    except urllib.error.URLError as e:
        msg = f"Verbindungsfehler: {e.reason}"
        cache.log(cache.LEVEL_ERROR, msg)
        return False, msg
    except Exception as e:
        msg = f"Fehler: {e}"
        cache.log(cache.LEVEL_ERROR, msg)
        return False, msg


def extract_json(text: str) -> str:
    """
    Extrahiert JSON aus LLM-Antwort.
    Reihenfolge: direkt → ```json...``` → ```...``` → erste {...} Klammer
    """
    if not text or not text.strip():
        raise ValueError("Leerer Text.")

    t = text.strip()

    # 1. Direkt parsen
    try:
        return json.dumps(json.loads(t))
    except Exception:
        pass

    # 2. ```json ... ```
    m = re.search(r"```json\s*(\{.*?\})\s*```", t, re.DOTALL)
    if m:
        try:
            return json.dumps(json.loads(m.group(1)))
        except Exception:
            pass

    # 3. ``` ... ```
    m = re.search(r"```\s*(\{.*?\})\s*```", t, re.DOTALL)
    if m:
        try:
            return json.dumps(json.loads(m.group(1)))
        except Exception:
            pass

    # 4. Erste { ... } Klammer (tiefste vollständige)
    start = t.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.dumps(json.loads(t[start:i+1]))
                    except Exception:
                        break

    raise ValueError(f"Kein gültiges JSON gefunden in: {t[:200]}")


def _call_ollama(prompt: str, system_prompt: Optional[str],
                 model: str, host: str, timeout: float) -> str:
    """
    Synchroner POST /api/chat Aufruf.
    Gibt den Antwort-Text des Assistenten zurück.
    """
    global _cancel
    url = host.rstrip("/") + "/api/chat"

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model":    model,
        "messages": messages,
        "stream":   False,
        "options": {
            "temperature": 0.05,   # niedrig → stabiles JSON
            "num_predict": 4096,
            "top_p":       0.9,
        },
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if _cancel:
                raise RuntimeError("Abgebrochen.")
            result  = json.loads(resp.read().decode("utf-8"))
            content = result.get("message", {}).get("content", "")
            if not content:
                raise ValueError(f"Leere Antwort: {result}")
            return content
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        raise RuntimeError(f"Ollama HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama nicht erreichbar: {e.reason}") from e


def generate_async(
    prompt:        str,
    system_prompt: Optional[str],
    model:         Optional[str],
    host:          Optional[str],
    phase,
    part_name:     str,
    on_done:       Callable,
    timeout:       float = 180.0,
) -> bool:
    """
    Startet LLM-Aufruf im Hintergrund-Thread.
    on_done(raw_json: str|None, error: str|None) wird im Worker-Thread aufgerufen.
    Returns False wenn der Client bereits beschäftigt ist.
    """
    global _busy, _cancel
    with _lock:
        if _busy:
            cache.log(cache.LEVEL_WARN, "LLM-Client bereits beschäftigt.",
                      phase=int(phase), part=part_name)
            return False
        _busy   = True
        _cancel = False

    _model = model or "qwen2.5-coder:7b"
    _host  = (host or "http://localhost:11434").rstrip("/")
    cache.log(cache.LEVEL_LLM,
              f"Starte Aufruf | {_model} | timeout={timeout}s",
              phase=int(phase), part=part_name)

    def worker():
        global _busy
        raw, err = None, None
        try:
            content = _call_ollama(prompt, system_prompt, _model, _host, timeout)
            cache.save_raw(content, phase=phase, part_name=part_name)
            cache.log(cache.LEVEL_LLM,
                      f"Antwort: {len(content)} Zeichen | Vorschau: {content[:80].replace(chr(10),' ')}",
                      phase=int(phase), part=part_name)
            # JSON extrahieren
            raw = extract_json(content)
        except ValueError as e:
            err = f"JSON-Extraktion: {e}"
            cache.log(cache.LEVEL_ERROR, err, phase=int(phase), part=part_name)
        except Exception as e:
            err = str(e)
            cache.log(cache.LEVEL_ERROR, f"LLM-Fehler: {err}",
                      phase=int(phase), part=part_name)
        finally:
            with _lock:
                _busy = False
            try:
                on_done(raw, err)
            except Exception as cb_err:
                cache.log(cache.LEVEL_ERROR, f"Callback-Fehler: {cb_err}")

    threading.Thread(target=worker, daemon=True).start()
    return True


def is_busy() -> bool:
    with _lock:
        return _busy

is_running = is_busy  # Alias

def cancel():
    global _cancel, _busy
    with _lock:
        _cancel = True
        _busy   = False
    cache.log(cache.LEVEL_WARN, "LLM-Aufruf abgebrochen.")
