"""OpenAI-compatible client for the director/revise calls.

The project's gateway (NINE_ROUTER_BASE_URL) sits behind Cloudflare, which
rejects the default urllib user-agent with `error code: 1010` — verified: the
identical request succeeds once a browser UA is sent. That header is therefore
not cosmetic; do not remove it.

Only text goes over the wire: the director reasons over the word spine, never
over the video, which is exactly why its output can be word-anchored.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from lib.talking_head_edit.job_store import REPO_ROOT

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
DEFAULT_MODEL = "ag/gemini-3.7-flash-high"
# Grok-4.5 reasons by default at "high" and routinely exceeds the gateway's
# Cloudflare 120s read timeout (HTTP 524) on structure prompts. Prefer low
# unless the operator overrides AUTOEDIT_REASONING_EFFORT.
DEFAULT_REASONING_EFFORT = "low"


class DirectorError(RuntimeError):
    """Raised with a user-facing, blocker-shaped message."""


def load_env_file(path: Path | None = None) -> None:
    """Populate os.environ from the repo .env without overriding real env vars."""
    env_path = path or (REPO_ROOT / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #")[0].strip().strip('"').strip("'")
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value


def gateway_config() -> tuple[str, str]:
    load_env_file()
    base = (os.environ.get("AUTOEDIT_DIRECTOR_BASE_URL")
            or os.environ.get("NINE_ROUTER_BASE_URL") or "").rstrip("/")
    key = (os.environ.get("AUTOEDIT_DIRECTOR_API_KEY")
           or os.environ.get("NINE_ROUTER_API_KEY") or "")
    if not base or not key:
        raise DirectorError(
            "Thiếu cấu hình gateway director. Đặt NINE_ROUTER_BASE_URL và "
            "NINE_ROUTER_API_KEY trong .env (hoặc AUTOEDIT_DIRECTOR_BASE_URL/"
            "AUTOEDIT_DIRECTOR_API_KEY)."
        )
    return base, key


def default_model() -> str:
    load_env_file()
    return os.environ.get("AUTOEDIT_DIRECTOR_MODEL") or DEFAULT_MODEL


def default_reasoning_effort(model: str | None = None) -> str | None:
    """Reasoning depth for models that support it (Grok 4.5 family).

    Returns None when the model is not known to use reasoning, so other
    providers keep receiving a clean OpenAI-compatible payload.
    """
    load_env_file()
    name = (model or default_model()).lower()
    if not (name.startswith("xai/") or "grok" in name):
        return None
    effort = (os.environ.get("AUTOEDIT_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT).strip().lower()
    if effort in ("", "default", "none", "off"):
        # Grok-4.5 cannot fully disable reasoning; "low" is the safe floor.
        return "low" if effort in ("none", "off", "") else None
    if effort not in ("low", "medium", "high"):
        return DEFAULT_REASONING_EFFORT
    return effort


def _apply_reasoning(payload: dict[str, Any], model: str) -> dict[str, Any]:
    """Attach reasoning controls in both shapes gateways commonly accept."""
    effort = default_reasoning_effort(model)
    if not effort:
        return payload
    # Chat Completions style (OpenAI/xAI) + nested shape some routers forward.
    payload["reasoning_effort"] = effort
    payload["reasoning"] = {"effort": effort}
    return payload


def _post(base: str, key: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": BROWSER_UA,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_json_loose(text: str) -> Any:
    """Parse JSON that may be wrapped in prose or a ```json fence."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise DirectorError("Model không trả về JSON hợp lệ.")
    return json.loads(cleaned[start:end + 1])


def chat_json(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.25,
    max_tokens: int = 50000,
    timeout: int = 600,
    retries: int = 3,
    raw_dump: Path | None = None,
) -> tuple[Any, dict[str, Any], str]:
    """One JSON-mode completion. Returns (parsed, usage, raw_text)."""
    base, key = gateway_config()
    model = model or default_model()
    payload = _apply_reasoning({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }, model)

    last_error = ""
    for attempt in range(retries):
        try:
            data = _post(base, key, payload, timeout)
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            if raw_dump:
                raw_dump.write_text(text, encoding="utf-8")
            return parse_json_loose(text), data.get("usage", {}) or {}, text
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300] if hasattr(exc, "read") else ""
            last_error = f"HTTP {exc.code}: {body}"
            if exc.code in (401, 403) and "1010" not in body:
                raise DirectorError(
                    f"Gateway từ chối xác thực ({last_error}). Kiểm tra NINE_ROUTER_API_KEY."
                ) from exc
            # Some gateways reject unknown reasoning fields with 400 — strip once
            # and retry so non-xAI routes stay usable without a special code path.
            if exc.code == 400 and ("reasoning" in body.lower() or "effort" in body.lower()):
                payload.pop("reasoning_effort", None)
                payload.pop("reasoning", None)
            # 429/5xx/Cloudflare blips: back off and retry
            time.sleep(20 if exc.code == 429 else 5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = f"Mạng: {exc}"
            time.sleep(5 * (attempt + 1))
        except DirectorError as exc:
            # JSON malformed — one cheap repair attempt with a stricter nudge
            last_error = str(exc)
            payload["messages"] = [
                {"role": "user", "content": prompt},
                {"role": "system", "content": "CHỈ trả về một object JSON hợp lệ, không giải thích."},
            ]
            time.sleep(2)

    raise DirectorError(
        f"Gọi director thất bại sau {retries} lần với model '{model}'. Lỗi cuối: {last_error}"
    )


def _multimodal(prompt: str, parts: list[dict[str, Any]], model: str | None,
                max_tokens: int, timeout: int) -> tuple[Any, dict[str, Any]]:
    """One completion whose message carries media parts alongside the text."""
    base, key = gateway_config()
    payload = {
        "model": model or default_model(),
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, *parts]}],
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": BROWSER_UA},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")

    # the gateway appends extra bytes after the JSON object on large replies,
    # so a plain json.loads() raises "Extra data"
    data, _ = json.JSONDecoder().raw_decode(raw)
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return parse_json_loose(text), data.get("usage", {}) or {}


def chat_with_images(prompt: str, images: list[tuple[str, Path]],
                     model: str | None = None, max_tokens: int = 3000,
                     timeout: int = 600) -> tuple[Any, dict[str, Any]]:
    """Ask the model to LOOK. Returns (parsed JSON, usage).

    Same reason as the audio variant: picture quality is a judgement, and the
    numbers disagree with the eye often enough to matter — the sharpest grade
    measured here was also the one that looked over-processed.
    """
    import base64

    parts: list[dict[str, Any]] = []
    for label, path in images:
        suffix = Path(path).suffix.lstrip(".").lower() or "png"
        encoded = base64.b64encode(Path(path).read_bytes()).decode()
        parts.append({"type": "text", "text": f"--- {label} ---"})
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:image/{suffix};base64,{encoded}"}})
    return _multimodal(prompt, parts, model, max_tokens, timeout)


def chat_with_audio(prompt: str, audio_files: list[tuple[str, Path]],
                    model: str | None = None, max_tokens: int = 3000,
                    timeout: int = 600) -> tuple[Any, dict[str, Any]]:
    """Ask the model to LISTEN. Returns (parsed JSON, usage).

    Exists because audio quality cannot be settled by measurement: chasing
    signal-to-noise picked the preset a listening pass rated worst for
    naturalness. A model that can actually hear breaks that tie.
    """
    import base64

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for label, path in audio_files:
        content.append({"type": "text", "text": f"--- {label} ---"})
        content.append({
            "type": "input_audio",
            "input_audio": {
                "data": base64.b64encode(Path(path).read_bytes()).decode(),
                "format": Path(path).suffix.lstrip(".") or "mp3",
            },
        })

    return _multimodal(prompt, content[1:], model, max_tokens, timeout)


def estimate_cost(usage: dict[str, Any], model: str) -> float:
    """Cost in USD when a price table is configured, else 0.0 (unknown).

    Set AUTOEDIT_PRICE_<IN|OUT>_PER_MTOK to make spend visible in the UI.
    """
    try:
        price_in = float(os.environ.get("AUTOEDIT_PRICE_IN_PER_MTOK", "0"))
        price_out = float(os.environ.get("AUTOEDIT_PRICE_OUT_PER_MTOK", "0"))
    except ValueError:
        return 0.0
    tokens_in = float(usage.get("prompt_tokens", 0) or 0)
    tokens_out = float(usage.get("completion_tokens", 0) or 0)
    return round(tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out, 6)
