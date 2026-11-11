"""A small client for the Gemini API's generateContent, standard library only.

The key comes from GEMINI_API_KEY and is only ever sent in the x-goog-api-key
header; it is never printed or logged. GEMINI_API_BASE points the client at
another server (the tests' mock endpoint).
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
TRANSIENT = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
MAX_WAIT = 60


class GeminiError(RuntimeError):
    pass


def estimate_tokens(text):
    """A rough token count for planning (about four characters a token), not what the API bills."""
    return (len(text) + 3) // 4


def request_body(system, prompt, schema, temperature=None, thinking_level=None):
    config = {"responseMimeType": "application/json", "responseSchema": schema}
    if temperature is not None:
        config["temperature"] = temperature
    if thinking_level:
        config["thinkingConfig"] = {"thinkingLevel": thinking_level}
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": config,
    }


def _retry_delay(body, attempt):
    """The server's retryDelay ("12s") when it gives one, else a short backoff."""
    try:
        for d in json.loads(body).get("error", {}).get("details", []):
            m = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(d.get("retryDelay", "")))
            if m:
                return min(float(m.group(1)), MAX_WAIT)
    except (ValueError, AttributeError):
        pass
    return min(2 ** attempt * 2, MAX_WAIT)


class Client:
    def __init__(self, model=DEFAULT_MODEL, api_key=None, base=None, timeout=120, sleep=time.sleep, temperature=None, thinking_level=None):
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        self.base = (base or os.environ.get("GEMINI_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self.sleep = sleep
        self.temperature = temperature
        self.thinking_level = thinking_level
        if not self.api_key:
            raise GeminiError("GEMINI_API_KEY is not set (run through pass-env, or use --dry-run or --mock).")

    def generate(self, system, prompt, schema):
        """Send one request. Returns (answer text, usage, info) where info has latency and transport retries."""
        body = json.dumps(request_body(system, prompt, schema, self.temperature, self.thinking_level)).encode()
        url = f"{self.base}/models/{self.model}:generateContent"
        started = time.monotonic()
        retries = 0
        for attempt in range(MAX_ATTEMPTS):
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                if e.code in TRANSIENT and attempt + 1 < MAX_ATTEMPTS:
                    retries += 1
                    self.sleep(_retry_delay(raw, attempt))
                    continue
                try:
                    msg = json.loads(raw).get("error", {}).get("message", "")
                except ValueError:
                    msg = ""
                raise GeminiError(f"Gemini API answered {e.code}: {msg[:200]}".rstrip(": ")) from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt + 1 < MAX_ATTEMPTS:
                    retries += 1
                    self.sleep(_retry_delay("", attempt))
                    continue
                raise GeminiError(f"Could not reach the Gemini API: {getattr(e, 'reason', e)}") from None
        latency = round(time.monotonic() - started, 3)
        meta = data.get("usageMetadata") or {}
        usage = {
            "input_tokens": int(meta.get("promptTokenCount") or 0),
            "output_tokens": int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0),
            "thinking_tokens": int(meta.get("thoughtsTokenCount") or 0),
        }
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise GeminiError(f"Gemini returned no answer ({reason})")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return text, usage, {"latency_s": latency, "transport_retries": retries, "finish_reason": cands[0].get("finishReason", "")}
