"""Tables (Markdown) and a per-turn HTML waterfall."""
from __future__ import annotations

import html

from .pipeline import metrics, pct

COLUMNS = [  # (metric, header)
    ("stt_s", "STT"), ("ttft_s", "first token"), ("first_audio_s", "first audio"),
    ("e2e_first_audio_s", "e2e first audio"), ("gap_s", "mid-reply gaps"),
    ("turn_total_s", "turn total"), ("tts_rtf", "TTS RTF"), ("stt_rtf", "STT RTF"),
    ("cancel_s", "cancel"), ("cancel_llm_s", "cancel LLM"), ("cancel_tts_s", "cancel TTS"),
]


def _fmt(v, metric):
    if v is None:
        return "–"
    return f"{v:.2f}" if "rtf" in metric else f"{v * 1000:.0f}"


def summary_table(turns: list[dict]) -> str:
    """One row per strategy, p50 / p95 of each metric. Times in ms, RTF as a ratio."""
    by_mode: dict[str, list[dict]] = {}
    for t in turns:
        by_mode.setdefault(t["mode"], []).append(metrics(t))
    cols = [(k, h) for k, h in COLUMNS if any(m.get(k) is not None for ms in by_mode.values() for m in ms)]
    lines = ["| strategy | n | " + " | ".join(f"{h} p50 / p95" for _, h in cols) + " |",
             "|---|---|" + "---|" * len(cols)]
    for mode, ms in by_mode.items():
        cells = []
        for k, _ in cols:
            xs = [m.get(k) for m in ms]
            cells.append(f"{_fmt(pct(xs, 50), k)} / {_fmt(pct(xs, 95), k)}")
        lines.append(f"| {mode} | {len(ms)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e2dc;--stt:#7a8fb3;--llm:#c9a227;--tok:#1d1d1b;
--tts:#3f8f6b;--play:#a7d3bd;--cut:#c0392b}
@media (prefers-color-scheme:dark){:root{--bg:#171716;--fg:#ecebe6;--muted:#9a988f;--line:#2e2d2a;
--stt:#8ea3c7;--llm:#d8b443;--tok:#ecebe6;--tts:#58b088;--play:#2f6b52;--cut:#e0604f}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px}
h1{font-size:20px}.turn{border-top:1px solid var(--line);padding:10px 0}.meta{color:var(--muted);font-size:12px}
.row{display:flex;align-items:center;height:18px}.lab{width:70px;flex:none;font-size:12px;color:var(--muted)}
.lane{position:relative;flex:1;height:14px}.b{position:absolute;top:0;height:14px;border-radius:2px}
.stt{background:var(--stt)}.llm{background:var(--llm)}.tts{background:var(--tts)}.play{background:var(--play)}
.tok{position:absolute;top:-2px;width:2px;height:18px;background:var(--tok)}
.cut{position:absolute;top:-4px;width:2px;height:22px;background:var(--cut)}
.legend span{display:inline-block;width:10px;height:10px;margin:0 4px 0 12px;border-radius:2px}
"""


def waterfall_html(turns: list[dict], title: str, env: dict) -> str:
    rows = []
    span = max((metrics(t).get("turn_total_s") or 0) for t in turns) or 1

    def bar(cls, a, b, tip):
        return (f'<div class="b {cls}" style="left:{100 * a / span:.2f}%;width:{max(0.2, 100 * (b - a) / span):.2f}%"'
                f' title="{html.escape(tip)}"></div>')

    for t in turns:
        m, ev = metrics(t), t["events"]
        stt = next(e for e in ev if e["stage"] == "stt")
        llm = next(e for e in ev if e["stage"] == "llm")
        lanes = {"STT": [bar("stt", stt["start"], stt["end"], f"STT {stt['end'] - stt['start']:.3f}s")],
                 "LLM": [bar("llm", llm["start"], llm["end"], f"LLM {llm['end'] - llm['start']:.3f}s")],
                 "TTS": [], "playback": []}
        if llm["first_token"] is not None:
            lanes["LLM"].append(f'<div class="tok" style="left:{100 * llm["first_token"] / span:.2f}%"'
                                f' title="first token"></div>')
        end = None
        for e in ev:
            if e["stage"] != "tts":
                continue
            lanes["TTS"].append(bar("tts", e["start"], e["end"], ("cancelled: " if e.get("cancelled") else "") + e["text"]))
            if e.get("cancelled"):
                continue
            ps = e["end"] if end is None else max(e["end"], end)
            end = ps + e["audio_s"]
            cut = t["marks"].get("interrupt")
            if cut is not None and ps >= cut:
                continue
            lanes["playback"].append(bar("play", ps, min(end, cut) if cut is not None else end, e["text"]))
        cut = t["marks"].get("interrupt")
        body = ""
        for name, items in lanes.items():
            extra = f'<div class="cut" style="left:{100 * cut / span:.2f}%" title="user interrupts"></div>' if cut else ""
            body += f'<div class="row"><div class="lab">{name}</div><div class="lane">{"".join(items)}{extra}</div></div>'
        fa = m.get("e2e_first_audio_s")
        summary = (f"first audio {fa * 1000:.0f} ms" if fa is not None else "no audio") + \
                  f" · total {m.get('turn_total_s', 0) * 1000:.0f} ms" + \
                  (f" · cancel {m['cancel_s'] * 1000:.0f} ms" if "cancel_s" in m else "") + \
                  (f" · gaps {m['gap_s'] * 1000:.0f} ms" if m.get("gap_s") else "")
        rows.append(f'<div class="turn"><b>{html.escape(t["utt"])}</b> · {html.escape(t["mode"])} '
                    f'<span class="meta">{summary}</span>'
                    f'<div class="meta">“{html.escape(t["transcript"])}” → “{html.escape(llm["text"].strip())}”</div>'
                    f'{body}</div>')
    env_line = " · ".join(f"{k}: {html.escape(str(v))}" for k, v in env.items())
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>'
            f'<style>{_CSS}</style></head><body><h1>{html.escape(title)}</h1><p class="meta">{env_line}</p>'
            f'<p class="meta legend">Full width = {span * 1000:.0f} ms. Hover a bar for its text.'
            f'<span class="stt"></span>STT<span class="llm"></span>LLM (black tick = first token)'
            f'<span class="tts"></span>TTS synthesis<span class="play"></span>simulated playback'
            f'<span class="cut" style="position:static;display:inline-block;height:10px"></span>barge-in</p>'
            + "".join(rows) + "</body></html>")
