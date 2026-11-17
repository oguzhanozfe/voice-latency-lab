# voice-latency-lab

Measures where the time goes in a local voice-agent turn (speech to text → LLM → text to speech) and tests which streaming tricks actually shorten the wait before the agent starts talking.

It is the measurement companion to [voice-support-agent](https://github.com/oguzhanozfe/voice-support-agent), a local voice support agent. That repo is about whether the agent answers correctly. This one is about how long the caller waits.

The core is standard-library Python 3.10+. faster-whisper and Piper are optional extras. Every stage is pluggable, and the unit tests and CI run the whole loop with fake stages, so they need no model or audio.

## What it measures

For every turn:

| metric | meaning |
|---|---|
| STT | time to transcribe the user's recording; STT RTF = that time / recording length |
| first token | time from transcript ready to the first LLM text |
| first audio | time from transcript ready to the first chunk of synthesized audio being ready |
| e2e first audio | the same, plus STT: end of the user's speech to the first audio |
| mid-reply gaps | silence inside the reply, when the next chunk is not ready before the previous one finishes playing |
| turn total | end of user speech to end of (simulated) playback |
| TTS RTF | synthesis time / audio length |
| cancel | barge-in: time from the user interrupting until the LLM stream and TTS are both torn down |

It compares three ways of feeding the LLM's streamed text into TTS:

- **full**: wait for the whole reply, then synthesize it.
- **sentence**: send each sentence as soon as it is complete.
- **clause**: send the first clause (up to the first comma, semicolon or colon) as soon as it is complete, then sentences.

It also simulates two kinds of barge-in on the sentence strategy and measures the cancel latency:

- **after first token**: the user resumes talking 0.3 s after the reply started generating, for example after a false end-of-turn.
- **after first audio**: the user talks over the reply 0.3 s after its first audio is ready.

The LLM is cancelled by shutting down its HTTP socket from outside the blocking read, which makes Ollama stop generating. TTS is cancelled by killing the synthesis process. After each barge-in a tiny request is sent straight away; if its time to first token matches normal turns, the cancelled generation really stopped instead of running on in the background.

Playback is simulated from each chunk's audio length (chunk *i* starts at the later of "ready" and "chunk *i−1* finished"). Nothing goes to a speaker, so runs are repeatable and quiet.

## Stages

| stage | backends | notes |
|---|---|---|
| STT | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) `base.en`, int8 on CPU, beam 1 (optional) | model loaded once; WER against the known text is reported so a fast but wrong transcript shows up. Without it, the known transcript is used and STT is reported as **not measured** |
| LLM | Ollama, streaming `/api/chat`, `think: false`, temperature 0, fixed seed, max 120 tokens | `--model`, `OLLAMA_URL` |
| TTS | macOS `say` | a new process per chunk that writes a WAV file; killed on barge-in |
| TTS | [Piper](https://github.com/OHF-Voice/piper1-gpl) `en_US-lessac-medium`, in-process (optional) | voice loaded once; cannot be interrupted mid-inference, so cancel waits for the current Piper sentence |
| all | fake stages (`--fake`) | used by the tests and CI |

"First audio" means the first chunk's audio is fully synthesized, for both TTS backends. Neither is measured as a streamed first byte.

`python -m vlab detect` shows what is installed. The tool never installs anything by itself.

## Run it

```bash
git clone https://github.com/oguzhanozfe/voice-latency-lab && cd voice-latency-lab
python3 -m unittest discover -s tests -v          # offline, fake stages
python3 -m vlab run --fake --reps 1 --out results/fake-demo
```

On a Mac with Ollama running, optionally with the extras (about 230 MB of packages plus 210 MB of models):

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python faster-whisper piper-tts
.venv/bin/python -m piper.download_voices --data-dir models/piper en_US-lessac-medium
.venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', download_root='models/whisper')"
```

Then, from the repo root (use `.venv/bin/python` if you installed the extras):

```bash
python3 -m vlab gen-utterances                    # 12 recorded questions, made with `say`, into audio/
python3 -m vlab run --reps 3 --out results/my-run
open results/my-run/waterfall.html
```

Each run writes `turns.jsonl` (every event with timestamps), `env.json` (hardware, OS, model and digest, voice, machine load at start and end), `summary.md` (p50 / p95 per strategy) and `waterfall.html` (a timeline per turn).

The utterance texts are in `utterances.txt`. The audio is generated on your machine and not committed, because redistributing Apple voice output is not clearly allowed.

## Results

Run [`results/m5pro-qwen3.5-9b`](results/m5pro-qwen3.5-9b): Apple M5 Pro, 24 GB, macOS 27.2.1, Python 3.12.
- **LLM:** Ollama 0.33.3 with `qwen3.5:9b` (Q4_K_M, digest `56671c2ab938`).
- **STT:** faster-whisper 1.2.1 `base.en`, int8 on CPU.
- **TTS:** Piper 1.8.0 `en_US-lessac-medium`, in-process, and macOS `say -v Samantha`.

That is 12 utterances × 3 repetitions = **36 turns per row**, 360 turns in all, none skipped. Package versions and model file hashes are in [`env.json`](results/m5pro-qwen3.5-9b/env.json), every event is in `turns.jsonl`, and the timelines are in [`waterfall.html`](results/m5pro-qwen3.5-9b/waterfall.html), which shows the first repetition.

**The machine was not idle.** Other jobs shared this Mac, so the per-turn 1-minute load average ran from 2.3 to 10.1 (median 5.0). In one stretch of about 70 consecutive turns, another process was using Ollama, and time to first token went above 1 s. The **p50 values are the comparison**; the p95 values mostly show that contention, not the pipeline.

Time from the end of the user's speech to the first audio chunk ready, in ms:

| TTS | full reply | sentence streaming | first-clause start |
|---|---|---|---|
| `say` (process per chunk) | 2397 | 1661 | 1637 |
| Piper (in-process) | 1629 | 800 | 771 |

The p50 parts of one Piper sentence-streaming turn: STT 254 ms, first LLM token 70 ms after the transcript, first audio 542 ms after the transcript. The full table is in [`summary.md`](results/m5pro-qwen3.5-9b/summary.md).

What the numbers say:

- **Sentence streaming is the trick that matters.** It cuts first audio by about 0.8 s with Piper and about 0.7 s with `say`, because the first sentence of a reply (p50 35 words) is synthesized while the LLM is still writing the rest.
- **Starting at the first clause adds almost nothing**: 24–29 ms. Short openers ("Sure,") are merged into the next chunk (minimum 3 words), and the first sentences here are short anyway.
- **The TTS engine matters as much as the strategy.** `say` launches a process per chunk: TTS real-time factor 0.18 (time spent synthesizing ÷ length of audio produced). Piper keeps its voice loaded: real-time factor 0.02. Piper with no streaming (1629 ms) is about as fast as `say` with streaming (1661 ms).
- **No mid-reply silence in any turn** (gap p95 = 0). Both engines synthesize much faster than the audio plays, so once the first chunk is ready the rest keeps up.
- **STT costs about 255 ms** for 2.5–5.1 s questions (real-time factor 0.08, the time to transcribe ÷ length of the recording). The word error rate is 0 on every utterance except one: "opened electronics" became "open electronics" in all 30 of that utterance's turns.
- **Barge-in is cheap to cancel.** Shutting the Ollama socket stops the LLM in under 1 ms (p95 1 ms). Killing `say` takes 1–3 ms. Piper cannot be interrupted mid-inference, so cancel waits for the current sentence: p50 0 ms, p95 40 ms. Of 144 barge-ins, the LLM was still generating in 102.
- **Cancelled generations really stop.** A tiny request sent straight after each barge-in got its first token in 169 ms (p50). That matches the 153 ms of other requests whose prompt was not already cached, so Ollama was not still working on the cancelled reply.

### Cloud comparison: Gemini instead of the local LLM

Run [`results/m5pro-gemini-3.5-flash-lite`](results/m5pro-gemini-3.5-flash-lite) uses the same STT, Piper and utterances, with Gemini `gemini-3.5-flash-lite` instead of Ollama. It streams through the OpenAI-compatible endpoint with minimal reasoning, on the free tier with a key shared by other jobs. It is one repetition (12 utterances × 5 turn types = 60 turns), 4 requests were rate-limited and retried as whole turns, and none were skipped.

| LLM | time to first token, p50 / p95 (all Piper turns) | sentence streaming: speech end to first audio, p50 |
|---|---|---|
| local `qwen3.5:9b` (180 turns) | 145 / 5300 ms | 800 ms |
| Gemini free tier, cloud (60 turns) | 1307 / 14354 ms | 1301 ms |

On this setup the local 9B model starts answering about 1.2 s sooner at the median, and its worst cases are far less extreme. The cloud numbers depend on free-tier queueing and the network on this day, not just on the model, so read them as one sample. Its LLM had already finished in 21 of the 24 barge-ins. A likely reason is that Gemini streams in a few large pieces: one response checked by hand arrived in three. The run files do not record piece counts.

A confound to know about: Ollama caches the prompt prefix, and the turns for one utterance run back to back. A turn that repeats the previous turn's prompt gets its first token in about 91 ms (p50, 324 turns). A turn with a new prompt takes about 153 ms (36 turns). The "full" turn always comes first for each utterance, after a probe with a different prompt, so it pays about 60–80 ms more. That is small next to the 700–800 ms streaming gain, but the gaps between rows are slightly inflated by it.

## Known limits

- **Synthetic speech in, not people.** The 12 recordings are made with `say`: clean, no noise, no accents. STT time and WER on real callers will be worse.
- **No streamed first byte.** Both TTS backends are timed per chunk, so "first audio" is when the first chunk is fully synthesized.
- **Shared machine.** Other jobs on the same Mac share its Ollama. Each run records the 1-minute load average and Ollama's loaded models at start and end in `env.json`; numbers from a busy machine are not comparable to a quiet one.
- **Small sample.** 12 utterances × 3 repetitions = 36 turns per strategy, so p95 is close to the maximum.
- **Prompt cache.** Turns for one utterance share a cached prompt (see Results). Shuffling the order or adding a per-turn nonce would remove that.
- **CPU load is not GPU load.** The recorded load average does not see another process using Ollama on the GPU. The contended stretch shows up in time to first token, not in the load column.
- **Regex chunking.** Sentence and clause boundaries are found with a regex; abbreviations such as "Dr. Smith" split early.
- **One model, one voice, one machine.** The numbers say nothing about other hardware.

## License

MIT for this repo. The optional extras keep their own licenses and are not bundled: Piper (`piper-tts`) is GPL-3.0-or-later, faster-whisper is MIT, and the downloaded voice and Whisper model carry their publishers' terms.
