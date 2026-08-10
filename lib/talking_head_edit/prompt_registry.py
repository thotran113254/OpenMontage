"""Prompts as versioned data instead of f-strings in code.

Three things this buys, in order of how much they matter:

1. an admin can change a prompt without changing code, and see it rendered with
   a real spine before spending a director call on it
2. the version that produced a build is recorded, so "yesterday's came out
   better" becomes a question with an answer
3. two versions can be compared on the same spine (see `prompt_ab`)

**No Jinja2, no `str.format`.** These prompts are full of JSON schema — literal
`{"w0":i,"w1":j}` on nearly every line. `str.format` would raise on all of it,
and escaping every brace as `{{` is exactly the noise that made the original
f-strings hard to read. So: `{{name}}` placeholders and sequential `str.replace`.
KISS, and no dependency.

Overrides live in a separate directory from the shipped templates so `git diff`
stays readable and `git pull` never conflicts with what an admin edited.
"""

from __future__ import annotations

import difflib
import json
import re
import time
from pathlib import Path
from typing import Any

from lib.talking_head_edit.job_store import REPO_ROOT

PROMPTS_DIR = REPO_ROOT / "prompts"
FAMILY = "talking_head"
TEMPLATE_DIR = PROMPTS_DIR / FAMILY
OVERRIDE_DIR = PROMPTS_DIR / "overrides" / FAMILY
REGISTRY_PATH = PROMPTS_DIR / "registry.json"

# Path traversal is the only injection surface here, so id and version are
# restricted rather than sanitised.
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
VERSION_PATTERN = re.compile(r"^v\d+$")
PLACEHOLDER = re.compile(r"\{\{([a-z0-9_]+)\}\}")

# A prompt whose output is parsed as JSON must keep saying so. An override that
# deletes this line does not fail — it produces prose the parser rejects three
# minutes later — so it is checked when the override is saved.
REQUIRED_MARKERS: dict[str, tuple[str, ...]] = {
    "structure": ("TRẢ VỀ DUY NHẤT JSON",),
    "captions": ("TRẢ VỀ DUY NHẤT JSON",),
    "cut_verify": ("TRẢ VỀ DUY NHẤT JSON",),
    "revise": ("TRẢ VỀ DUY NHẤT JSON",),
    "select_take": ("TRẢ VỀ DUY NHẤT JSON",),
}


class PromptError(ValueError):
    pass


def _check_id(prompt_id: str) -> str:
    if not ID_PATTERN.match(prompt_id or ""):
        raise PromptError(f"id prompt không hợp lệ: {prompt_id!r} (chỉ [a-z0-9_])")
    return prompt_id


def _check_version(version: str) -> str:
    if not VERSION_PATTERN.match(version or ""):
        raise PromptError(f"version không hợp lệ: {version!r} (dạng v1, v2, …)")
    return version


def registry() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_registry(data: dict[str, Any]) -> None:
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def current_version(prompt_id: str) -> str:
    _check_id(prompt_id)
    entry = registry().get(prompt_id) or {}
    return str(entry.get("current") or "v1")


def template_path(prompt_id: str, version: str) -> Path:
    """Override first, then the shipped template.

    An override with the same version number as a shipped template wins, so an
    admin can fix `structure.v1.md` in place without inventing a version.
    """
    _check_id(prompt_id)
    _check_version(version)
    override = OVERRIDE_DIR / f"{prompt_id}.{version}.md"
    if override.exists():
        return override
    return TEMPLATE_DIR / f"{prompt_id}.{version}.md"


def load(prompt_id: str, version: str | None = None) -> str:
    version = version or current_version(prompt_id)
    path = template_path(prompt_id, version)
    if not path.exists():
        raise PromptError(
            f"Không có template {prompt_id}.{version}.md "
            f"(đã tìm trong {OVERRIDE_DIR} và {TEMPLATE_DIR})")
    return path.read_text(encoding="utf-8")


def versions(prompt_id: str) -> list[dict[str, Any]]:
    """Every version of one prompt, with where it came from."""
    _check_id(prompt_id)
    found: dict[str, dict[str, Any]] = {}
    for directory, kind in ((TEMPLATE_DIR, "builtin"), (OVERRIDE_DIR, "override")):
        for path in sorted(directory.glob(f"{prompt_id}.v*.md")):
            version = path.stem.split(".")[-1]
            if not VERSION_PATTERN.match(version):
                continue
            # An override shadows the builtin of the same version.
            found[version] = {"version": version, "source": kind,
                              "path": str(path), "bytes": path.stat().st_size}
    entry = registry().get(prompt_id) or {}
    changelog = {str(c.get("version")): c for c in (entry.get("changelog") or [])}
    current = current_version(prompt_id)
    return [
        {**row, "is_current": row["version"] == current,
         **{k: v for k, v in (changelog.get(row["version"]) or {}).items()
            if k != "version"}}
        for row in sorted(found.values(), key=lambda r: int(r["version"][1:]))
    ]


def catalog() -> list[dict[str, Any]]:
    """Every prompt id the system knows about."""
    ids: set[str] = set()
    for directory in (TEMPLATE_DIR, OVERRIDE_DIR):
        for path in directory.glob("*.v*.md"):
            prompt_id = path.stem.split(".")[0]
            if ID_PATTERN.match(prompt_id):
                ids.add(prompt_id)
    return [{"id": prompt_id, "current": current_version(prompt_id),
             "versions": versions(prompt_id)}
            for prompt_id in sorted(ids)]


def render(prompt_id: str, variables: dict[str, Any],
           version: str | None = None) -> str:
    """Substitute `{{name}}` placeholders. Missing or leftover ones raise.

    Both directions are enforced on purpose. A placeholder the caller forgot
    would otherwise reach the model as the literal text `{{spine}}`, and a
    variable the template does not use is a sign the two have drifted — both are
    cheap to catch here and expensive to notice in a model's output.
    """
    template = load(prompt_id, version)
    needed = set(PLACEHOLDER.findall(template))
    missing = needed - set(variables)
    if missing:
        raise PromptError(
            f"{prompt_id}.{version or current_version(prompt_id)}: thiếu biến "
            f"{', '.join(sorted(missing))}")

    out = template
    for name in needed:
        out = out.replace("{{" + name + "}}", str(variables[name]))

    leftover = PLACEHOLDER.findall(out)
    if leftover:
        raise PromptError(
            f"{prompt_id}: còn placeholder chưa thay: {', '.join(sorted(set(leftover)))}")
    return out


def placeholders(prompt_id: str, version: str | None = None) -> list[str]:
    return sorted(set(PLACEHOLDER.findall(load(prompt_id, version))))


def next_version(prompt_id: str) -> str:
    highest = 0
    for row in versions(prompt_id):
        highest = max(highest, int(row["version"][1:]))
    return f"v{highest + 1}"


def save_override(prompt_id: str, body: str, version: str | None = None,
                  note: str = "", author: str = "admin",
                  make_current: bool = True) -> dict[str, Any]:
    """Write an admin-edited template and register it.

    Warnings rather than refusals for content problems: the admin is the one
    person who might legitimately be restructuring a prompt, and blocking a save
    is a worse experience than telling them what looks wrong.
    """
    _check_id(prompt_id)
    version = _check_version(version or next_version(prompt_id))
    if not body.strip():
        raise PromptError("Nội dung prompt rỗng")

    warnings: list[str] = []
    for marker in REQUIRED_MARKERS.get(prompt_id, ()):
        if marker not in body:
            warnings.append(
                f"Thiếu dòng «{marker}» — output sẽ không phải JSON và parser sẽ từ chối.")
    try:
        builtin = (TEMPLATE_DIR / f"{prompt_id}.v1.md").read_text(encoding="utf-8")
    except OSError:
        builtin = ""
    if builtin:
        dropped = set(PLACEHOLDER.findall(builtin)) - set(PLACEHOLDER.findall(body))
        if dropped:
            warnings.append(
                f"Bỏ mất placeholder {', '.join(sorted(dropped))} — dữ liệu đó sẽ "
                "không đến được model.")

    OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
    path = OVERRIDE_DIR / f"{prompt_id}.{version}.md"
    path.write_text(body, encoding="utf-8")

    data = registry()
    entry = data.setdefault(prompt_id, {"current": "v1", "changelog": []})
    entry.setdefault("changelog", []).append({
        "version": version, "created_at": time.time(), "note": note, "author": author,
    })
    if make_current:
        entry["current"] = version
    _save_registry(data)
    return {"id": prompt_id, "version": version, "path": str(path),
            "is_current": make_current, "warnings": warnings}


def delete_override(prompt_id: str, version: str) -> dict[str, Any]:
    """Remove an override. Falls back to the shipped template of that version.

    If the deleted version was current, current moves back to `v1` — the shipped
    behaviour — rather than to some other override the admin did not choose.
    """
    _check_id(prompt_id)
    _check_version(version)
    path = OVERRIDE_DIR / f"{prompt_id}.{version}.md"
    if not path.exists():
        raise PromptError(f"Không có override {prompt_id}.{version}")
    path.unlink()

    data = registry()
    entry = data.get(prompt_id) or {}
    if entry.get("current") == version:
        entry["current"] = "v1"
    entry["changelog"] = [c for c in (entry.get("changelog") or [])
                          if str(c.get("version")) != version]
    if entry:
        data[prompt_id] = entry
        _save_registry(data)
    return {"deleted": f"{prompt_id}.{version}", "current": current_version(prompt_id)}


def set_current(prompt_id: str, version: str) -> dict[str, Any]:
    _check_id(prompt_id)
    _check_version(version)
    if not template_path(prompt_id, version).exists():
        raise PromptError(f"Không có {prompt_id}.{version}")
    data = registry()
    entry = data.setdefault(prompt_id, {"current": "v1", "changelog": []})
    entry["current"] = version
    _save_registry(data)
    return {"id": prompt_id, "current": version}


def fingerprint(prompt_ids: list[str]) -> str:
    """A hash over the templates that will actually be used.

    This is what makes an edited prompt re-run the director. Without it, changing
    a prompt changes nothing observable and the natural conclusion is that the
    prompt does not matter — the single easiest thing to forget in this design,
    and the one with the most misleading symptom.

    Covers a body edit, a version switch, and an override being deleted, because
    all three change either the version or the bytes.
    """
    import hashlib

    digest = hashlib.sha256()
    for prompt_id in sorted(prompt_ids):
        version = current_version(prompt_id)
        try:
            body = load(prompt_id, version)
        except PromptError:
            body = ""
        digest.update(f"{prompt_id}:{version}:".encode("utf-8"))
        digest.update(body.encode("utf-8"))
    return digest.hexdigest()


def diff(prompt_id: str, version: str, against: str = "v1") -> str:
    """Unified diff of one version against another (default: the shipped v1)."""
    left = load(prompt_id, against).splitlines(keepends=True)
    right = load(prompt_id, version).splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        left, right, fromfile=f"{prompt_id}.{against}", tofile=f"{prompt_id}.{version}"))
