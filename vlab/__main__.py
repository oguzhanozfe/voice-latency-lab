"""voice-latency-lab command line.

  python -m vlab detect                      what is installed
  python -m vlab gen-utterances              synthesize the fixed utterance set with macOS `say`
  python -m vlab run --out results/x         measure (real stages where available)
  python -m vlab run --fake --out results/x  same loop with fake stages, no models needed
  python -m vlab report results/x            rebuild summary.md and waterfall.html
  python -m vlab demo                        rebuild docs/ from committed runs, then a few turns with
                                             whatever is installed (Ollama if running, else fake stages)
"""
from __future__ import annotations

import argparse
import shutil
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import stages
from .pipeline import MODES, metrics, pct, run_turn
from .report import index_html, summary_table, waterfall_html

ROOT = Path(__file__).resolve().parent.parent
AUDIO = ROOT / "audio"


@dataclass
class Utt:
    id: str
    text: str
    wav: str | None
    seconds: float | None


def load_utterances() -> list[Utt]:
    out = []
    for line in (ROOT / "utterances.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            uid, text = line.split("|", 1)
            wav = AUDIO / f"{uid}.wav"
            out.append(Utt(uid, text.strip(), str(wav) if wav.exists() else None,
                           stages.wav_seconds(str(wav)) if wav.exists() else None))
    return out


def gen_utterances(voice: str):
    AUDIO.mkdir(exist_ok=True)
    for u in load_utterances():
        subprocess.run(["say", "-v", voice, "-o", str(AUDIO / f"{u.id}.wav"), "--file-format=WAVE",
                        "--data-format=LEI16@16000", u.text], check=True)
        print(u.id, f"{stages.wav_seconds(str(AUDIO / f'{u.id}.wav')):.2f}s", u.text)


def _sh(*argv) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def environment(llm, ttss, stt) -> dict:
    env = {"os": f"macOS {platform.mac_ver()[0]}" if sys.platform == "darwin" else platform.platform(),
           "chip": _sh("sysctl", "-n", "machdep.cpu.brand_string") or platform.machine(),
           "ram_gb": round(int(_sh("sysctl", "-n", "hw.memsize") or 0) / 2**30) or None,
           "python": platform.python_version(), "stt": stt.name, "llm": llm.name,
           "tts": [t.name for t in ttss]}
    pkgs = {}
    for pkg in ("faster-whisper", "ctranslate2", "piper-tts", "onnxruntime"):
        try:
            pkgs[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            pass
    if pkgs:
        env["packages"] = pkgs
    files = []
    if isinstance(stt, stages.FasterWhisperSTT):
        files += [p for p in Path(stt.model_dir).rglob("model.bin")]
    files += [Path(t.model_path) for t in ttss if isinstance(t, stages.PiperTTS)]
    if files:
        env["model_files_sha256"] = {("whisper " if p.name == "model.bin" else "piper ") + p.name: _sha256(p)
                                     for p in files}
    if any(isinstance(t, stages.SayTTS) for t in ttss):
        env["say_voice"] = next(t.voice for t in ttss if isinstance(t, stages.SayTTS))
    if isinstance(llm, stages.OllamaLLM):
        try:
            with urllib.request.urlopen(llm.base_url + "/api/version", timeout=5) as r:
                env["ollama"] = json.load(r)["version"]
            with urllib.request.urlopen(llm.base_url + "/api/tags", timeout=5) as r:
                tag = next(m for m in json.load(r)["models"] if m["name"] == llm.model)
            d = tag["details"]
            env["model"] = f"{llm.model} ({d['parameter_size']}, {d['quantization_level']}, digest {tag['digest'][:12]})"
        except (OSError, StopIteration, KeyError, ValueError):
            pass
    return env


def network_snapshot(host: str) -> dict:
    """How far away the cloud is right now: TCP connect time (about one round trip), median of 5,
    and whether the default route is Wi-Fi or wired. No addresses are recorded."""
    import socket
    rtts = []
    for _ in range(5):
        s = time.perf_counter()
        try:
            socket.create_connection((host, 443), timeout=5).close()
            rtts.append(time.perf_counter() - s)
        except OSError:
            pass
    iface = next((ln.split(":")[1].strip() for ln in _sh("route", "-n", "get", "default").splitlines()
                  if "interface:" in ln), "")
    ports = _sh("networksetup", "-listallhardwareports").split("Hardware Port: ")
    kind = next((b.splitlines()[0] for b in ports if f"Device: {iface}" in b), iface or "unknown")
    return {"host": host, "tcp_connect_ms_p50": round(pct(rtts, 50) * 1000, 1) if rtts else None, "link": kind}


def load_snapshot(llm) -> dict:
    """Machine load at this moment: other processes sharing the Mac and its Ollama skew latency."""
    snap = {"loadavg_1m": round(os.getloadavg()[0], 2)}
    if isinstance(llm, stages.GroqLLM):
        snap["network"] = network_snapshot("api.groq.com")
    if isinstance(llm, stages.OllamaLLM):
        try:
            with urllib.request.urlopen(llm.base_url + "/api/ps", timeout=5) as r:
                snap["ollama_loaded"] = [m["name"] for m in json.load(r)["models"]]
        except (OSError, KeyError, ValueError):
            pass
    return snap


def probe_ttft(llm) -> float:
    """Time to first token of a tiny request: if a cancelled generation were still running, this would wait."""
    s = time.perf_counter()
    try:
        stream = llm.stream("Say OK.")
    except stages.RateLimited:
        return None
    for piece in stream:
        if piece.strip():
            break
    t = time.perf_counter() - s
    stream.close()
    return t


def build_stages(a):
    if a.fake:
        return stages.FakeSTT(), stages.FakeLLM(), [stages.FakeTTS()]
    found = stages.available()
    if a.stt == "groq":
        stt = stages.GroqSTT()
    elif a.stt == "faster-whisper" or (a.stt == "auto" and found["faster-whisper"]):
        stt = stages.FasterWhisperSTT(device=a.stt_device, compute_type="int8" if a.stt_device == "cpu" else "float16")
    else:
        stt = stages.OracleSTT()
    ttss = []
    for name in a.tts.split(","):
        if name == "say" and found["say"]:
            ttss.append(stages.SayTTS(a.voice))
        elif name == "piper" and found["piper"]:
            ttss.append(stages.PiperTTS())
        else:
            print(f"skipping TTS {name!r}: not installed", file=sys.stderr)
    if not ttss:
        sys.exit("no TTS available")
    llm = {"gemini": lambda: stages.GeminiLLM(), "groq": lambda: stages.GroqLLM(a.groq_model),
           "ollama": lambda: stages.OllamaLLM(a.model)}[a.llm]()
    return stt, llm, ttss


def turn_with_retry(*args, **kw):
    """Free-tier cloud APIs answer 429 under load: wait and rerun the whole turn, so backoff never
    lands inside a measured time."""
    for wait in (5, 15, 30, 60, 60, 60):
        try:
            return run_turn(*args, **kw)
        except stages.RateLimited:
            print(f"rate limited, retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    return None  # gave up: the turn is skipped and counted in env.json


def run(a):
    stt, llm, ttss = build_stages(a)
    utts = load_utterances()[: a.limit or None]
    if not isinstance(stt, stages.OracleSTT) and not a.fake and any(u.wav is None for u in utts):
        sys.exit("audio missing: run `python -m vlab gen-utterances` first")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    env = environment(llm, ttss, stt)
    env["load_at_start"] = load_snapshot(llm)
    print(json.dumps(env, indent=1), file=sys.stderr)
    if not a.fake:  # warm up: cold model loads and first calls are excluded from the numbers
        for piece in llm.stream("Hello."):
            pass
        for tts in ttss:
            tts.synth("Warming up.", threading.Event())
        if utts:
            stt.transcribe(utts[0])
    turns, skipped = [], 0
    for rep in range(a.reps):
        for u in utts:  # strategies (and voices) interleaved per utterance so drift hits all alike
            for tts in ttss:
                label = (lambda m: f"{tts.name.split()[0]} · {m}") if len(ttss) > 1 else (lambda m: m)
                for mode in MODES:
                    t = turn_with_retry(u, stt, llm, tts, mode)
                    if t is None:
                        skipped += 1
                        continue
                    turns.append(dict(t, mode=label(t["mode"]), rep=rep, loadavg_1m=round(os.getloadavg()[0], 2)))
                for anchor in ("token", "audio"):
                    t = turn_with_retry(u, stt, llm, tts, "sentence", barge_in_after=a.barge_in_after,
                                        barge_in_anchor=anchor)
                    if t is None:
                        skipped += 1
                        continue
                    t["probe_ttft_s"] = probe_ttft(llm)
                    turns.append(dict(t, mode=label(t["mode"]), rep=rep, loadavg_1m=round(os.getloadavg()[0], 2)))
                print(f"rep {rep} {u.id}: " + ", ".join(
                    f"{t['mode']} first audio {metrics(t).get('first_audio_s', 0) * 1000:.0f} ms"
                    for t in turns[-5:-2]), file=sys.stderr)
    with open(out / "turns.jsonl", "w") as f:
        for t in turns:
            f.write(json.dumps(t) + "\n")
    env["load_at_end"] = load_snapshot(llm)
    (out / "env.json").write_text(json.dumps(dict(env, reps=a.reps, utterances=len(utts),
                                                  barge_in_after_s=a.barge_in_after, skipped_turns=skipped), indent=1) + "\n")
    report(out)


def extra_stats(turns: list[dict]) -> list[str]:
    """Run-level checks quoted in the README, recomputed from turns.jsonl."""
    ms = [metrics(t) for t in turns]
    ms_ms = lambda xs, q: f"{pct(xs, q) * 1000:.0f}"  # noqa: E731
    lines = ["## Run-level checks", ""]
    loads = [t["loadavg_1m"] for t in turns if "loadavg_1m" in t]
    if loads:
        lines.append(f"- 1-minute load average per turn: min {min(loads)}, median {pct(loads, 50):.2f}, max {max(loads)}.")
    slow = [i for i, m in enumerate(ms) if (m.get("ttft_s") or 0) > 1]
    lines.append(f"- Turns with time to first token over 1 s: {len(slow)} of {len(turns)}"
                 + (f" (run-order indices {slow[0]}–{slow[-1]})." if slow else "."))
    groups: dict[str, list] = {}
    for t, m in zip(turns, ms):
        groups.setdefault(t["mode"].split(" · ")[0] if " · " in t["mode"] else "all", []).append(m["ttft_s"])
    for g, xs in groups.items():
        lines.append(f"- Time to first token, {g} turns (n={len(xs)}): p50 {ms_ms(xs, 50)} ms, p95 {ms_ms(xs, 95)} ms.")
    gaps = [m["gap_s"] for m in ms if m.get("gap_s") is not None]
    if gaps:
        lines.append(f"- Mid-reply gaps: {sum(g > 0 for g in gaps)} of {len(gaps)} non-barge-in turns had one; max {max(gaps) * 1000:.0f} ms.")
    rep, cold, after, other = [], [], [], []
    for i, (t, m) in enumerate(zip(turns, ms)):
        prev = turns[i - 1] if i else None
        fresh = prev is None or prev["utt"] != t["utt"] or "probe_ttft_s" in prev
        (cold if fresh else rep).append(m["ttft_s"])
        if m.get("tok_per_s"):
            (after if prev is not None and "probe_ttft_s" in prev else other).append(m["tok_per_s"])
    if rep and cold:
        lines.append(f"- Prompt cache: turns repeating the previous turn's prompt got the first token in p50 "
                     f"{ms_ms(rep, 50)} ms (n={len(rep)}); turns after a probe or a new utterance in "
                     f"{ms_ms(cold, 50)} ms (n={len(cold)}).")
    if after and other:
        lines.append(f"- Decode speed of the turn right after a barge-in: p50 {pct(after, 50):.1f} tok/s (n={len(after)}), "
                     f"other turns {pct(other, 50):.1f} tok/s (n={len(other)}).")
    return lines + [""]


def report(out: Path, quiet: bool = False):
    turns = [json.loads(line) for line in (out / "turns.jsonl").read_text().splitlines()]
    env = json.loads((out / "env.json").read_text())
    normal = [metrics(t).get("ttft_s") for t in turns
              if t["mode"].endswith("sentence") and "interrupt" not in t.get("marks", {})]
    probes = [t["probe_ttft_s"] for t in turns if t.get("probe_ttft_s") is not None]
    md = [f"# Results: {out.name}", "", "Times in ms (p50 / p95), RTF = processing time / audio length.", "",
          "```", json.dumps(env, indent=1), "```", "", summary_table(turns), ""]
    barge = [metrics(t) for t in turns if "interrupt" in t.get("marks", {})]
    if barge:
        md += [f"Barge-ins: {len(barge)}; the LLM was still generating at the interrupt in "
               f"{sum(m['llm_running_at_interrupt'] for m in barge)} of them. 'cancel LLM' and 'cancel TTS' "
               f"only count turns where that stage still had work.", ""]
    if probes:
        md += [f"After each barge-in a tiny request was sent at once. Its time to first token: "
               f"p50 {pct(probes, 50) * 1000:.0f} ms / p95 {pct(probes, 95) * 1000:.0f} ms "
               f"(normal turns: p50 {pct(normal, 50) * 1000:.0f} ms). Similar numbers are consistent with "
               f"the cancelled generation having stopped (a probe alone cannot prove it if the server runs requests in parallel).", ""]
    md += extra_stats(turns)
    (out / "summary.md").write_text("\n".join(md))
    first_rep = [t for t in turns if t.get("rep", 0) == 0]
    (out / "waterfall.html").write_text(waterfall_html(first_rep, f"Voice turn waterfall: {out.name}", env))
    if not quiet:
        print("\n".join(md))


REPO = "https://github.com/oguzhanozfe/voice-latency-lab"


def build_site(docs: Path):
    """Regenerate every committed run's report and copy them into docs/ (published by GitHub Pages)."""
    runs = []
    for out in sorted((p.parent for p in (ROOT / "results").glob("*/turns.jsonl")), reverse=True):  # local run first
        report(out, quiet=True)
        (docs / out.name).mkdir(parents=True, exist_ok=True)
        shutil.copy(out / "waterfall.html", docs / out.name / "waterfall.html")
        turns = [json.loads(line) for line in (out / "turns.jsonl").read_text().splitlines()]
        runs.append((out.name, json.loads((out / "env.json").read_text()), summary_table(turns)))
    (docs / "index.html").write_text(index_html(runs, REPO))
    print(f"rebuilt docs/index.html from {len(runs)} committed runs: " + ", ".join(r[0] for r in runs))


def ollama_has(model: str) -> bool:
    url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=1) as r:
            return any(m.get("name") == model for m in json.load(r).get("models", []))
    except (OSError, ValueError):
        return False


def demo(a):
    build_site(ROOT / "docs")
    found = stages.available()
    tts = next((n for n in ("piper", "say") if found[n]), None)
    if a.fake or tts is None or (a.llm == "ollama" and not ollama_has(a.model)) \
            or (a.llm != "ollama" and not os.environ.get(f"{a.llm.upper()}_API_KEY")):
        mode, fake = "fake stages (no Ollama/TTS/key found, or --fake): timings are synthetic", True
    else:
        mode, fake = f"live: {a.llm} {a.model if a.llm == 'ollama' else '(cloud)'}, TTS {tts}", False
    has_audio = all(u.wav for u in load_utterances()[:3])
    stt = ("groq" if a.llm == "groq" else "faster-whisper" if found["faster-whisper"] else "oracle") \
        if has_audio else "oracle"
    print(f"mode: {mode}", file=sys.stderr)
    run(argparse.Namespace(out=a.out, fake=fake, llm=a.llm, model=a.model, groq_model="qwen/qwen3.8-27b", voice="Samantha", stt=stt,
                           stt_device="cpu", tts=tts or "say", reps=1, limit=3, barge_in_after=0.3))
    print(f"\nmode: {mode}\nopen {a.out}/waterfall.html  (published runs: docs/index.html)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="vlab", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detect")
    g = sub.add_parser("gen-utterances")
    g.add_argument("--voice", default="Samantha")
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--fake", action="store_true", help="fake stages, no models")
    r.add_argument("--llm", default="ollama", choices=["ollama", "gemini", "groq"],
                   help="gemini / groq need GEMINI_API_KEY / GROQ_API_KEY and leave the machine")
    r.add_argument("--groq-model", default="qwen/qwen3.8-27b")
    r.add_argument("--model", default=os.environ.get("VLAB_MODEL", "qwen3.5:9b"))
    r.add_argument("--voice", default="Samantha", help="macOS say voice")
    r.add_argument("--stt", default="auto", choices=["auto", "faster-whisper", "oracle", "groq"],
                   help="groq = whisper-large-v3-turbo in the cloud, needs GROQ_API_KEY")
    r.add_argument("--stt-device", default="cpu", choices=["cpu", "mps"])
    r.add_argument("--tts", default="say,piper", help="comma list of: say, piper (missing ones are skipped)")
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--limit", type=int, default=0, help="only the first N utterances")
    r.add_argument("--barge-in-after", type=float, default=0.3,
                   help="seconds after the anchor (first token / first audio chunk) when the user interrupts")
    d = sub.add_parser("demo", help="no keys needed")
    d.add_argument("--out", default="demo-out")
    d.add_argument("--fake", action="store_true", help="force fake stages")
    d.add_argument("--llm", default="ollama", choices=["ollama", "gemini", "groq"],
                   help="cloud LLMs only if GEMINI_API_KEY / GROQ_API_KEY is set; groq also does STT")
    d.add_argument("--model", default=os.environ.get("VLAB_MODEL", "qwen3.5:9b"))
    rp = sub.add_parser("report")
    rp.add_argument("out")
    a = p.parse_args(argv)
    if a.cmd == "detect":
        print(json.dumps(stages.available(), indent=1))
    elif a.cmd == "gen-utterances":
        gen_utterances(a.voice)
    elif a.cmd == "run":
        run(a)
    elif a.cmd == "demo":
        demo(a)
    else:
        report(Path(a.out))


if __name__ == "__main__":
    main()
