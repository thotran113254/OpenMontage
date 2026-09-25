"""Time mapping and mechanical guards — the parts that must never drift.

No API key and no ffmpeg needed: these cover the pure logic that turns word
indices into seconds and repairs artefacts the director cannot see.
"""

from __future__ import annotations

import pytest

from lib.talking_head_edit.prompt_captions import chunk_ranges
from lib.talking_head_edit.resolve_events import TimeMapper, apply_guards
from lib.talking_head_edit.resolve_spans import compute_removes, kept_spans


def make_words(count: int, step: float = 1.0) -> list[dict[str, object]]:
    return [
        {"word": f"w{i}", "start": round(i * step, 3), "end": round(i * step + step * 0.8, 3)}
        for i in range(count)
    ]


class TestComputeRemoves:
    def test_pads_edges_without_eating_neighbouring_words(self):
        words = make_words(10)
        # remove word 5 only
        spans = compute_removes([[5, 5]], words)
        assert len(spans) == 1
        start, end = spans[0]
        # start sits after word 4 ends (4.8) but not past word 5's onset (5.0)
        assert 4.8 <= start <= 5.0
        # end sits before word 6's onset (6.0)
        assert end <= 6.0

    def test_skips_cuts_shorter_than_minimum(self):
        words = [
            {"word": "a", "start": 0.0, "end": 1.0},
            {"word": "b", "start": 1.0, "end": 1.05},   # 0.05s — below MIN_CUT
            {"word": "c", "start": 1.05, "end": 2.0},
        ]
        assert compute_removes([[1, 1]], words) == []

    def test_skips_padded_filler_under_min_cut(self):
        # Real "À" from the trial clip: 0.10s word, pad expands to 0.22s.
        words = [
            {"word": "nha.", "start": 13.58, "end": 13.74},
            {"word": "À", "start": 13.94, "end": 14.04},
            {"word": "bây", "start": 14.04, "end": 14.28},
        ]
        assert compute_removes([[1, 1]], words) == []

    def test_merges_adjacent_spans(self):
        words = make_words(12)
        spans = compute_removes([[3, 3], [4, 4]], words)
        assert len(spans) == 1, "hai cut liền nhau phải gộp thành một"

    def test_handles_reversed_and_out_of_range_indices(self):
        words = make_words(6)
        assert compute_removes([[4, 2]], words), "chỉ số đảo ngược vẫn phải xử lý"
        assert compute_removes([[99, 200]], words), "chỉ số vượt biên phải kẹp vào biên"


class TestKeptSpans:
    def test_complement_of_removes(self):
        kept = kept_spans([(2.0, 3.0)], 10.0)
        assert kept == [(0.0, 2.0), (3.0, 10.0)]

    def test_never_returns_empty(self):
        assert kept_spans([(0.0, 10.0)], 10.0) == [(0.0, 10.0)]


class TestTimeMapper:
    """Single-source mapping, built through `from_removes`.

    The multi-source construction (spans across several files) is covered in
    test_talking_head_resolve_multisource.py.
    """

    def test_shifts_time_back_by_everything_removed_before_it(self):
        words = make_words(10)
        removes = [(2.0, 4.0)]          # 2s removed
        mapper = TimeMapper.from_removes(words, removes, new_duration=8.0, tempo=1.0,
                                         duration=10.0)
        # word 6 started at 6.0 in the source, 2s of material before it is gone
        assert mapper.at(6) == pytest.approx(4.0, abs=0.01)

    def test_applies_tempo(self):
        words = make_words(10)
        mapper = TimeMapper.from_removes(words, [], new_duration=10.0, tempo=2.0,
                                         duration=10.0)
        assert mapper.at(4) == pytest.approx(2.0, abs=0.01)

    def test_word_inside_a_cut_clamps_to_the_cut_edge(self):
        words = make_words(10)
        mapper = TimeMapper.from_removes(words, [(3.0, 5.0)], new_duration=8.0,
                                         tempo=1.0, duration=10.0)
        assert mapper.at(4) == pytest.approx(3.0, abs=0.01)

    def test_never_exceeds_the_new_duration(self):
        words = make_words(10)
        mapper = TimeMapper.from_removes(words, [], new_duration=3.0, tempo=1.0,
                                         duration=10.0)
        assert mapper.at(9, use_end=True) <= 3.0


class TestGuards:
    def test_keyword_inside_a_card_is_moved_before_it(self):
        events = [
            {"type": "card", "at": 5.0, "end": 12.0, "title": "X"},
            {"type": "keyword", "at": 7.0, "end": 8.5, "text": "HIDDEN"},
        ]
        out, report = apply_guards(events)
        moved = report["keywords_moved"]
        assert len(moved) == 1
        keyword = next(e for e in out if e["type"] == "keyword")
        assert keyword["end"] <= 5.0, "keyword phải kết thúc trước khi card vào"

    def test_keyword_with_no_room_is_dropped_with_its_sfx(self):
        events = [
            {"type": "card", "at": 0.1, "end": 20.0, "title": "X"},
            {"type": "keyword", "at": 1.0, "end": 2.5, "text": "HIDDEN"},
            {"type": "sfx", "at": 1.0, "name": "sfx_pop.mp3", "volume": 0.5},
        ]
        out, report = apply_guards(events)
        assert report["keywords_dropped"]
        assert not [e for e in out if e["type"] == "keyword"]
        assert not [e for e in out if e["type"] == "sfx"], "pop mồ côi phải bị bỏ theo"

    def test_sfx_follows_a_moved_keyword(self):
        events = [
            {"type": "card", "at": 6.0, "end": 12.0, "title": "X"},
            {"type": "keyword", "at": 7.0, "end": 8.0, "text": "K"},
            {"type": "sfx", "at": 7.0, "name": "sfx_pop.mp3", "volume": 0.5},
        ]
        out, _ = apply_guards(events)
        keyword = next(e for e in out if e["type"] == "keyword")
        sfx = next(e for e in out if e["type"] == "sfx")
        assert sfx["at"] == pytest.approx(keyword["at"], abs=0.01)

    def test_short_caption_is_merged_into_the_next_one(self):
        events = [
            {"type": "caption", "at": 0.0, "end": 0.3, "text": "Chào"},
            {"type": "caption", "at": 0.3, "end": 2.0, "text": "các bạn"},
        ]
        out, report = apply_guards(events)
        captions = [e for e in out if e["type"] == "caption"]
        assert report["captions_merged"] == 1
        assert len(captions) == 1
        assert captions[0]["text"] == "Chào các bạn"
        assert captions[0]["end"] == 2.0

    def test_untouched_events_survive(self):
        events = [{"type": "punchIn", "at": 3.0, "scale": 1.08, "holdSeconds": 1.0}]
        out, _ = apply_guards(events)
        assert out == events


# Keyword text fitting is intentionally NOT tested here: it moved into the
# renderer (KeywordView), which measures the real font instead of estimating
# from character count. See remotion-composer/src/mona/MonaSample.tsx.


class TestCaptionChunks:
    def test_covers_every_word_exactly_once(self):
        words = make_words(400)
        ranges = chunk_ranges(words)
        covered: list[int] = []
        for start, end in ranges:
            covered.extend(range(start, end + 1))
        assert covered == list(range(400))

    def test_short_input_is_a_single_chunk(self):
        assert chunk_ranges(make_words(20)) == [(0, 19)]

    def test_prefers_splitting_at_the_longest_pause(self):
        words = make_words(300, step=0.5)
        # a clear 2s silence after word 120
        for index in range(121, 300):
            words[index]["start"] += 2.0
            words[index]["end"] += 2.0
        ranges = chunk_ranges(words)
        assert ranges[0][1] == 120, "phải cắt đúng chỗ im lặng dài nhất"


class TestCutSafety:
    def test_a_filler_squeezed_between_words_is_not_worth_an_edit_point(self):
        """Duration is judged on the real cut window, in the resolver — not here."""
        from lib.talking_head_edit.cut_safety import filter_unsafe_cuts
        from lib.talking_head_edit.resolve_spans import is_worth_cutting

        words = [
            {"word": "nha.", "start": 13.58, "end": 13.74},
            {"word": "À", "start": 13.94, "end": 14.04},
            {"word": "bây", "start": 14.04, "end": 14.28},
        ]
        kept, _ = filter_unsafe_cuts([[1, 1]], words)
        assert kept == [[1, 1]]
        assert not is_worth_cutting([1, 1], words)

    def test_a_tiny_asr_filler_inside_a_long_pause_is_cut(self):
        """An ASR "ờ" is often 0.06s long inside a second of silence."""
        from lib.talking_head_edit.cut_safety import filter_unsafe_cuts
        from lib.talking_head_edit.resolve_spans import is_worth_cutting

        words = [
            {"word": "như,", "start": 10.0, "end": 10.3},
            {"word": "ờ...", "start": 11.24, "end": 11.30},
            {"word": "sản", "start": 11.82, "end": 12.1},
        ]
        kept, rejected = filter_unsafe_cuts([[1, 1]], words)
        assert kept == [[1, 1]] and rejected == []
        assert is_worth_cutting([1, 1], words)

    def test_a_short_repeat_needs_the_tight_level(self):
        from lib.talking_head_edit.cut_safety import filter_unsafe_cuts

        words = [{"word": w, "start": i, "end": i + 0.5}
                 for i, w in enumerate(["đến", "và", "và", "từ"])]
        reasons = {(1, 1): "repeat"}
        assert filter_unsafe_cuts([[1, 1]], words, "normal", reasons)[0] == []
        assert filter_unsafe_cuts([[1, 1]], words, "tight", reasons)[0] == [[1, 1]]
        assert filter_unsafe_cuts([[1, 1]], words, "tight", {})[0] == []

    def test_cut_level_follows_the_prompt_when_left_on_auto(self):
        from lib.talking_head_edit.cut_safety import resolve_cut_level

        assert resolve_cut_level({"prompt": "Cắt kỹ các đoạn vấp"}) == "tight"
        assert resolve_cut_level({"prompt": "giữ nguyên lời"}) == "light"
        assert resolve_cut_level({"prompt": "nhấn phần chi phí"}) == "normal"
        assert resolve_cut_level({"prompt": "cắt kỹ", "cut_level": "light"}) == "light"

    def test_keeps_longer_pure_filler(self):
        from lib.talking_head_edit.cut_safety import filter_unsafe_cuts

        words = [
            {"word": "xong", "start": 0.0, "end": 0.4},
            {"word": "ờ", "start": 0.5, "end": 0.9},
            {"word": "thì", "start": 1.0, "end": 1.3},
        ]
        kept, rejected = filter_unsafe_cuts([[1, 1]], words)
        assert kept == [[1, 1]]
        assert rejected == []


class TestSlivers:
    def test_a_silent_pad_tail_between_a_cut_and_the_run_edge_is_dropped(self):
        from lib.talking_head_edit.resolve_spans import plan_spans

        words = [{"word": w, "start": s, "end": e, "src": "s1", "_orig_index": i}
                 for i, (w, s, e) in enumerate([("a", 0.0, 0.4), ("b", 0.5, 0.9),
                                                ("và", 2.9, 3.0)])]
        words.append({"word": "c", "start": 9.0, "end": 9.4, "src": "s1", "_orig_index": 10})
        spans, _ = plan_spans(words, [[2, 2]], {"s1": "x.mp4"}, {"s1": 20.0})
        assert all(span.end - span.start >= 0.25 for span in spans)

    def test_cutting_a_whole_take_drops_it_when_others_remain(self):
        from lib.talking_head_edit.resolve_spans import plan_spans

        words = [{"word": w, "start": s, "end": s + 0.4, "src": src, "_orig_index": i}
                 for i, (w, s, src) in enumerate([("x", 0.0, "s0"), ("y", 0.5, "s0"),
                                                  ("a", 0.0, "s1"), ("b", 0.5, "s1")])]
        spans, removes = plan_spans(words, [[0, 1]], {"s0": "0.mp4", "s1": "1.mp4"},
                                    {"s0": 1.0, "s1": 1.0})
        assert {span.src_id for span in spans} == {"s1"}
        assert removes and removes[0]["src"] == "s0"
