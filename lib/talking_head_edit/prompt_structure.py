"""Structure pass — everything except captions.

Split out for a measured reason: asked to do the whole edit in one call, the
director spent its output budget on 54 captions and returned zero cards and
zero cuts, despite the brief demanding four cards. Captions are long and
repetitive; structure is short and high-value. Giving structure its own call
with a small expected output makes the model finish the job.

Output stays word-anchored — the model never emits a second.
"""

from __future__ import annotations

from typing import Any

from lib.talking_head_edit import prompt_registry
from lib.talking_head_edit.job_store import option_enabled
from lib.talking_head_edit.resources import bgm_table, sfx_table


def compact_spine(words: list[dict[str, Any]],
                  source_boundaries: list[int] | None = None) -> str:
    """`index:word` pairs, with a marker where one source hands over to the next.

    No timestamps: the resolver owns the clock, and showing seconds only invites
    the model to reason in them. No filenames either — the marker tells the model
    that the material changes here without telling it anything it could hallucinate
    a path from.
    """
    marks = {index: number for number, index in
             enumerate(sorted(source_boundaries or []), start=2)}
    parts: list[str] = []
    for index, word in enumerate(words):
        if index in marks:
            parts.append(f"\n--- NGUỒN {marks[index]} ---\n")
        parts.append(f'{index}:{word["word"].strip()}')
    return " ".join(parts)


def _style_block(profile: dict[str, Any] | None) -> str:
    if not profile:
        return ""
    notes = profile.get("notes") or []
    lines = ["", "PHONG CÁCH ĐÃ HỌC TỪ NGƯỜI DÙNG (ưu tiên hơn mặc định):"]
    lines += [f"- {note}" for note in notes]
    return "\n".join(lines)


def _user_block(user_prompt: str) -> str:
    if not user_prompt.strip():
        return ""
    return (
        "\n\nYÊU CẦU RIÊNG CHO VIDEO NÀY (ưu tiên CAO NHẤT, nhưng không được bịa tên file "
        f"và không được ghi giây):\n{user_prompt.strip()}"
    )


def _source_rule(boundaries: list[int] | None, cross_source_cut: bool) -> str:
    """One line, only when there is more than one source.

    Deliberately one line and not a section: the director's job does not change
    when the footage arrives in several files, and a paragraph explaining
    multi-source would invite it to invent behaviour around that.
    """
    if not boundaries:
        return ""
    if cross_source_cut:
        return ("\nXƯƠNG SỐNG GỒM NHIỀU NGUỒN QUAY (đánh dấu bằng `--- NGUỒN k ---`). "
                "Được phép đặt cut/card xuyên qua ranh giới nguồn.")
    return ("\nXƯƠNG SỐNG GỒM NHIỀU NGUỒN QUAY (đánh dấu bằng `--- NGUỒN k ---`). "
            "TUYỆT ĐỐI không đặt một cut, card, hay caption XUYÊN QUA dòng đánh dấu đó — "
            "hai bên là hai lần quay khác nhau, ghép giữa câu sẽ nghe rõ mối.")


def _broll_rule(overlay_pool: list[dict[str, Any]] | None) -> str:
    """The b-roll event type and the job's own overlay inventory, or nothing.

    Empty when the job has no b-roll source, which makes `structure.v2` render
    byte-identical to v1 for a single-source job — so v2 can be the current
    version for everything instead of needing a per-job template choice.

    Listing the real source ids is the same "only real resources" rule that
    governs sfx and bgm: the model can only name something that exists, and
    `audit` rejects anything it invents anyway.
    """
    pool = [entry for entry in (overlay_pool or []) if entry.get("src")]
    if not pool:
        return ""
    rows = "\n".join(
        f'  - src="{entry["src"]}" — {entry.get("label") or entry["src"]}'
        f' ({float(entry.get("duration") or 0):.0f}s)'
        for entry in pool)
    return (
        '\n- {"type":"broll","w0":i,"w1":j,"src":"<đúng id bên dưới>","fit":"cover",'
        '"opacity":1.0} — phủ clip b-roll lên mặt người trong khoảng đó, GIỮ NGUYÊN '
        'tiếng người nói. CHỈ dùng khi lời nói đang mô tả đúng thứ mà clip đó cho '
        'thấy. Mỗi khoảng 2-6 giây, KHÔNG đặt trong khoảng card (card sẽ che mất), '
        'cách nhau >= 15 giây.\n'
        f'  Clip b-roll CÓ THẬT của video này (chỉ được dùng các src này):\n{rows}'
    )


def build_structure_prompt(
    words: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
    style_profile: dict[str, Any] | None = None,
    source_boundaries: list[int] | None = None,
    cross_source_cut: bool = False,
    overlay_pool: list[dict[str, Any]] | None = None,
    version: str | None = None,
) -> str:
    """Assemble the structure prompt from its template.

    The blocks stay in Python because they are conditional logic, not text: which
    cold-open wording applies, whether bgm is on, what the resource tables
    currently contain. The template owns the prose and the schema; this function
    owns the decisions.
    """
    opts = options or {}
    n = len(words)
    topic = (opts.get("topic") or "").strip()
    card_plan = (opts.get("card_plan") or "").strip()

    cold_block = (
        """"cold_open": {"w0":i,"w1":j,"caption":"câu đó đã sửa chính tả","highlight":"cụm sốc","highlightColor":"#FF4D4D","keyword":{"text":"CHỮ HOA NGẮN","color":"#FF2E93","xPct":18,"yPct":12,"anim":"whip","fontSize":84}}
- Chọn ĐÚNG 1 câu ĐẮT NHẤT toàn video (~1.5-2.5s nói): hậu quả đau nhất / con số sốc / phủ định mạnh. TUYỆT ĐỐI không chọn câu chào đầu. w1 phải là từ CUỐI của câu.
- Renderer tự cắt đoạn đó phát trước làm teaser và tự thêm punch-in + riser + flash + whoosh ở điểm nối — bạn KHÔNG thêm sfx/punch cho teaser."""
        if option_enabled(opts, "cold_open")
        else '"cold_open": null (người dùng tắt cold-open).'
    )

    bgm_block = (
        f""""bgm": {{"name":"<đúng tên file bên dưới>","volume":0.22}} — chọn theo mood nội dung:
{bgm_table()}
volume 0.18-0.28. Nếu video không nên có nhạc: "bgm": null."""
        if option_enabled(opts, "bgm")
        else '"bgm": null (người dùng tắt nhạc nền).'
    )

    # A registry prompt, not an f-string, on purpose: this is the one default
    # this file changes most (a heuristic about the video, not a schema), so it
    # is the one an admin most plausibly wants to revert or A/B without a code
    # change. `card_guidance.v1` is the original fixed "usually 3-5"; `v2` (the
    # shipped default — see registry.json) asks for a decision rule instead of a
    # target. `.rstrip("\n")` because this renders as ONE LINE inside a bullet in
    # `structure.vN.md` — a template file normally ends with a trailing newline,
    # which would otherwise open a blank line in the middle of that prompt.
    card_rule = card_plan or prompt_registry.render(
        "card_guidance", {"n": n}).rstrip("\n")
    topic_line = f'Chủ đề: "{topic}".' if topic else ""

    return prompt_registry.render("structure", {
        "card_rule": card_rule,
        "sfx_table": sfx_table(),
        "cold_block": cold_block,
        "bgm_block": bgm_block,
        "topic_line": topic_line,
        "style_block": _style_block(style_profile),
        "user_block": _user_block(str(opts.get("prompt", ""))),
        "source_rule": _source_rule(source_boundaries, cross_source_cut),
        # Only used by structure.v2+; harmless for v1, which has no such
        # placeholder (render ignores variables a template does not ask for).
        "broll_rule": _broll_rule(overlay_pool),
        "n": n,
        "spine": compact_spine(words, source_boundaries),
    }, version=version)
