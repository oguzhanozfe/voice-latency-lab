"""One voice turn: STT -> streaming LLM -> chunker -> TTS worker, recorded as timestamped events.

Metrics are computed afterwards from the events by `metrics()`, a pure function, so they can be
tested without any model or audio.
"""
from __future__ import annotations

import queue
import re
import threading
import time

from .stages import Cancelled, word_error_rate

MODES = ("full", "sentence", "clause")

_SENTENCE = re.compile(r"[.!?][\"')\]]*\s")
_CLAUSE = re.compile(r"[.!?,;:—][\"')\]]*\s")


class Chunker:
    """Decides when text is handed to TTS.

    full:     everything at the end.
    sentence: each sentence as soon as its end punctuation and the next space arrive.
    clause:   the first chunk at the first clause break (, ; : —), then sentences.
    Chunks shorter than `min_words` are merged into the next one.
    """
    # ponytail: regex boundaries; abbreviations like "Dr. Smith" split early, a real segmenter fixes that.

    def __init__(self, mode: str, min_words: int = 3):
        if mode not in MODES:
            raise ValueError(mode)
        self.mode, self.min_words, self.buf, self.first = mode, min_words, "", True

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out: list[str] = []
        if self.mode == "full":
            return out
        while True:
            pat = _CLAUSE if self.mode == "clause" and self.first else _SENTENCE
            cut = next((m.end() for m in pat.finditer(self.buf)
                        if len(self.buf[:m.end()].split()) >= self.min_words), None)
            if cut is None:
                return out
            out.append(self.buf[:cut].strip())
            self.buf, self.first = self.buf[cut:], False

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


def run_turn(utt, stt, llm, tts, mode: str, barge_in_after: float | None = None,
             barge_in_anchor: str = "audio") -> dict:
    """Run one turn. If barge_in_after is set, the user interrupts that many seconds after the
    anchor and everything is torn down. anchor "audio": after the first audio chunk is ready (the
    user talks over the reply). anchor "token": after the first LLM token (the user resumes
    speaking while the reply is still being generated, e.g. after a false end-of-turn)."""
    events: list[dict] = []
    marks: dict = {}
    t0 = time.perf_counter()

    def now():
        return time.perf_counter() - t0

    s = now()
    transcript = stt.transcribe(utt)
    events.append({"stage": "stt", "start": s, "end": now(), "measured": stt.measured})

    cancel = threading.Event()
    q: queue.Queue = queue.Queue()
    timer: list[threading.Timer] = []

    s = now()
    stream = llm.stream(transcript)

    def interrupt():
        marks["interrupt"] = now()
        cancel.set()
        stream.close()
        tts.kill()
        q.put(None)  # wake the worker if it is waiting for text

    def tts_worker():
        while not cancel.is_set():
            chunk = q.get()
            if chunk is None:
                break
            cs = now()
            try:
                audio = tts.synth(chunk, cancel)
            except Cancelled:
                events.append({"stage": "tts", "start": cs, "end": now(), "text": chunk, "cancelled": True})
                break
            events.append({"stage": "tts", "start": cs, "end": now(), "text": chunk, "audio_s": audio})
            if barge_in_after is not None and barge_in_anchor == "audio" and not timer:
                timer.append(threading.Timer(barge_in_after, interrupt))
                timer[0].start()
        marks["tts_exit"] = now()

    worker = threading.Thread(target=tts_worker, daemon=True)
    worker.start()

    chunker, parts, first = Chunker(mode), [], None
    for piece in stream:
        if first is None and piece.strip():
            first = now()
            if barge_in_after is not None and barge_in_anchor == "token":
                timer.append(threading.Timer(barge_in_after, interrupt))
                timer[0].start()
        parts.append(piece)
        for c in chunker.feed(piece):
            q.put(c)
    if not cancel.is_set():
        for c in chunker.flush():
            q.put(c)
    q.put(None)
    events.append({"stage": "llm", "start": s, "end": now(), "first_token": first,
                   "text": "".join(parts), "cancelled": cancel.is_set(),
                   "stats": getattr(stream, "stats", {})})
    worker.join()
    if timer:
        timer[0].join()  # an interrupt after all audio was ready still counts (it stops playback)
        worker.join()
    return {"utt": utt.id, "mode": mode if barge_in_after is None else f"barge-in after first {barge_in_anchor} ({mode})",
            "transcript": transcript, "ref_text": utt.text, "audio_in_s": utt.seconds, "events": sorted(events, key=lambda e: e["start"]),
            "marks": marks}


def metrics(turn: dict) -> dict:
    """All per-turn numbers, from events only. Times in seconds.

    first_audio_s and ttft_s are measured from the moment the transcript is ready.
    e2e_* add the STT time (from end of user speech). Playback is simulated: chunk i starts at
    max(ready_i, end of chunk i-1), so `gap_s` is the silence the listener hears mid-reply.
    """
    ev = turn["events"]
    stt = next(e for e in ev if e["stage"] == "stt")
    llm = next(e for e in ev if e["stage"] == "llm")
    done = [e for e in ev if e["stage"] == "tts" and not e.get("cancelled")]
    stt_s = stt["end"] - stt["start"]
    m = {"stt_s": stt_s if stt["measured"] else None,
         "stt_rtf": stt_s / turn["audio_in_s"] if stt["measured"] and turn["audio_in_s"] else None,
         "ttft_s": llm["first_token"] - llm["start"] if llm["first_token"] is not None else None,
         "llm_s": llm["end"] - llm["start"],
         "stt_wer": word_error_rate(turn["ref_text"], turn["transcript"])
         if stt["measured"] and "ref_text" in turn else None,
         "chunks": len(done), "reply_words": len(llm["text"].split())}
    stats = llm.get("stats") or {}
    if stats.get("eval_count") and stats.get("eval_duration"):
        m["tok_per_s"] = stats["eval_count"] / (stats["eval_duration"] / 1e9)
    if done:
        m["first_audio_s"] = done[0]["end"] - llm["start"]
        m["e2e_first_audio_s"] = done[0]["end"] - stt["start"]
        synth = sum(e["end"] - e["start"] for e in done)
        audio = sum(e["audio_s"] for e in done)
        m["tts_rtf"] = synth / audio if audio else None
        end, gap = None, 0.0
        for e in done:
            start = e["end"] if end is None else max(e["end"], end)
            if end is not None:
                gap += start - end
            end = start + e["audio_s"]
        m["gap_s"], m["turn_total_s"] = gap, end - stt["start"]
    it = turn.get("marks", {}).get("interrupt")
    if it is not None:
        # None = that stage had already finished when the user interrupted (nothing to cancel).
        llm_c = llm["end"] - it
        tts_c = turn["marks"]["tts_exit"] - it
        m["cancel_llm_s"] = llm_c if llm_c > 0 else None
        m["cancel_tts_s"] = tts_c if tts_c > 0 else None
        m["cancel_s"] = max(llm_c, tts_c, 0.0)
        m["llm_running_at_interrupt"] = llm_c > 0
        m.pop("gap_s", None)
        m["turn_total_s"] = it - stt["start"]
    return m


def pct(xs: list[float], q: float) -> float | None:
    """Percentile with linear interpolation between closest ranks (q in 0..100)."""
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q / 100
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)
