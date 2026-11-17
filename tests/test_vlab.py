import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vlab.__main__ import Utt, main  # noqa: E402
from vlab.pipeline import Chunker, metrics, pct, run_turn  # noqa: E402
from vlab.report import summary_table, waterfall_html  # noqa: E402
from vlab.stages import FakeLLM, FakeSTT, FakeTTS  # noqa: E402

TEXT = "Sure thing, I can help. Your order shipped. It arrives Friday."


def feed_words(c, text):
    out = []
    for i, w in enumerate(text.split(" ")):
        out += c.feed(w if i == 0 else " " + w)
    return out


class ChunkerTests(unittest.TestCase):
    def test_full_emits_only_on_flush(self):
        c = Chunker("full")
        self.assertEqual(feed_words(c, TEXT), [])
        self.assertEqual(c.flush(), [TEXT])
        self.assertEqual(c.flush(), [])

    def test_sentence_mode(self):
        c = Chunker("sentence")
        out = feed_words(c, TEXT)
        self.assertEqual(out, ["Sure thing, I can help.", "Your order shipped."])
        self.assertEqual(c.flush(), ["It arrives Friday."])

    def test_clause_mode(self):
        c = Chunker("clause")
        out = feed_words(c, "Well sure thing, I can help. Your order shipped. Bye now")
        self.assertEqual(out, ["Well sure thing,", "I can help.", "Your order shipped."])
        self.assertEqual(c.flush(), ["Bye now"])

    def test_clause_skips_comma_below_min_words(self):
        c = Chunker("clause")
        out = feed_words(c, "Sure thing, I can help. Next")
        self.assertEqual(out, ["Sure thing, I can help."])

    def test_short_chunks_merge_with_next(self):
        c = Chunker("sentence", min_words=3)
        out = feed_words(c, "Hi. Your order shipped. Bye now")
        self.assertEqual(out, ["Hi. Your order shipped."])
        self.assertEqual(c.flush(), ["Bye now"])

    def test_decimal_does_not_split(self):
        c = Chunker("sentence")
        out = feed_words(c, "It weighs about 3.5 kg in total. Done")
        self.assertEqual(out, ["It weighs about 3.5 kg in total."])

    def test_invalid_mode(self):
        with self.assertRaises(ValueError):
            Chunker("nope")


class PctTests(unittest.TestCase):
    def test_values(self):
        xs = [1, 2, 3, 4]
        self.assertAlmostEqual(pct(xs, 50), 2.5)
        self.assertAlmostEqual(pct(xs, 95), 3.85)
        self.assertEqual(pct(xs, 0), 1)
        self.assertEqual(pct(xs, 100), 4)

    def test_ignores_none_and_empty(self):
        self.assertAlmostEqual(pct([None, 1, 3, None], 50), 2)
        self.assertIsNone(pct([], 50))
        self.assertIsNone(pct([None], 50))


def turn(tts, llm_end=0.8, marks=None, measured=True, audio_in=2.0):
    ev = [{"stage": "stt", "start": 0.0, "end": 0.1, "measured": measured},
          {"stage": "llm", "start": 0.1, "end": llm_end, "first_token": 0.3,
           "text": "one two three four", "cancelled": False, "stats": {}}]
    for s, e, a in tts:
        ev.append({"stage": "tts", "start": s, "end": e, "text": "x", "audio_s": a})
    return {"utt": "u1", "mode": "sentence", "transcript": "hi", "audio_in_s": audio_in,
            "events": ev, "marks": marks or {}}


class MetricsTests(unittest.TestCase):
    def test_basic_and_positive_gap(self):
        # chunk1 ready 0.5, plays 0.5-1.0; chunk2 ready 1.3 -> 0.3 s of silence, ends 1.3+0.4=1.7
        m = metrics(turn([(0.4, 0.5, 0.5), (1.0, 1.3, 0.4)]))
        self.assertAlmostEqual(m["ttft_s"], 0.2)
        self.assertAlmostEqual(m["first_audio_s"], 0.4)
        self.assertAlmostEqual(m["e2e_first_audio_s"], 0.5)
        self.assertAlmostEqual(m["gap_s"], 0.3)
        self.assertAlmostEqual(m["turn_total_s"], 1.7)
        self.assertAlmostEqual(m["tts_rtf"], (0.1 + 0.3) / 0.9)
        self.assertAlmostEqual(m["stt_s"], 0.1)
        self.assertAlmostEqual(m["stt_rtf"], 0.05)
        self.assertEqual(m["chunks"], 2)
        self.assertEqual(m["reply_words"], 4)

    def test_zero_gap_when_ready_early(self):
        # chunk2 ready at 0.7, before chunk1 finishes playing at 1.0 -> starts 1.0, ends 1.4
        m = metrics(turn([(0.4, 0.5, 0.5), (0.5, 0.7, 0.4)]))
        self.assertEqual(m["gap_s"], 0.0)
        self.assertAlmostEqual(m["turn_total_s"], 1.4)

    def test_stt_not_measured(self):
        m = metrics(turn([(0.4, 0.5, 0.5)], measured=False))
        self.assertIsNone(m["stt_s"])
        self.assertIsNone(m["stt_rtf"])

    def test_barge_in_llm_still_running(self):
        m = metrics(turn([(0.4, 0.5, 2.0)], llm_end=0.8, marks={"interrupt": 0.6, "tts_exit": 0.65}))
        self.assertAlmostEqual(m["cancel_llm_s"], 0.2)
        self.assertAlmostEqual(m["cancel_tts_s"], 0.05)
        self.assertAlmostEqual(m["cancel_s"], 0.2)
        self.assertTrue(m["llm_running_at_interrupt"])
        self.assertNotIn("gap_s", m)
        self.assertAlmostEqual(m["turn_total_s"], 0.6)

    def test_barge_in_llm_already_done(self):
        m = metrics(turn([(0.4, 0.5, 2.0)], llm_end=0.5, marks={"interrupt": 0.6, "tts_exit": 0.65}))
        self.assertIsNone(m["cancel_llm_s"])
        self.assertFalse(m["llm_running_at_interrupt"])
        self.assertAlmostEqual(m["cancel_s"], 0.05)


LONG = ("Sure, I can help with that. Your order shipped yesterday. It should arrive on Friday. "
        "Please keep the tracking number safe. Let me know if you need anything else today.")
UTT = Utt("u1", "where is my order", None, None)


def fake_turn(mode, llm=None, **kw):
    return run_turn(UTT, FakeSTT(0.01), llm or FakeLLM(LONG, delay=0.01), FakeTTS(per_char=0.001), mode, **kw)


class RunTurnTests(unittest.TestCase):
    def test_sentence_first_audio_beats_full(self):
        full, sent = metrics(fake_turn("full")), metrics(fake_turn("sentence"))
        self.assertLess(sent["first_audio_s"], full["first_audio_s"])
        self.assertGreater(sent["chunks"], full["chunks"])
        self.assertIsNotNone(sent["ttft_s"])

    def test_barge_in(self):
        for anchor in ("audio", "token"):
            with self.subTest(anchor=anchor):
                t = fake_turn("sentence", llm=FakeLLM(LONG, delay=0.05), barge_in_after=0.0,
                              barge_in_anchor=anchor)
                self.assertTrue(t["mode"].startswith("barge-in"))
                self.assertIn(anchor, t["mode"])
                self.assertIn("interrupt", t["marks"])
                self.assertIn("tts_exit", t["marks"])
                llm = next(e for e in t["events"] if e["stage"] == "llm")
                self.assertTrue(llm["cancelled"])
                self.assertLess(len(llm["text"]), len(LONG))
                self.assertLess(metrics(t)["cancel_s"], 0.5)


class ReportTests(unittest.TestCase):
    def test_table_and_waterfall_have_every_mode(self):
        turns = [fake_turn(m) for m in ("full", "sentence", "clause")]
        turns.append(fake_turn("sentence", llm=FakeLLM(LONG, delay=0.05), barge_in_after=0.0))
        table = summary_table(turns)
        page = waterfall_html(turns, "t", {"os": "x"})
        barge = turns[-1]["mode"]
        for name in ("full", "sentence", "clause", barge):
            self.assertIn(f"| {name} |", table)
            self.assertIn(name, page)


class CliTests(unittest.TestCase):
    def test_fake_run_writes_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                main(["run", "--fake", "--reps", "1", "--limit", "2", "--out", d])
            for f in ("turns.jsonl", "env.json", "summary.md", "waterfall.html"):
                self.assertTrue((Path(d) / f).stat().st_size > 0, f)
            turns = (Path(d) / "turns.jsonl").read_text().splitlines()
            self.assertEqual(len(turns), 10)  # 2 utterances x (3 modes + 2 barge-in anchors)


if __name__ == "__main__":
    unittest.main()
