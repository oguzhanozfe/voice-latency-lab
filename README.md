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

Not yet committed: the real run is waiting for a quiet window on the test Mac (see Known limits).

## Known limits

- **Synthetic speech in, not people.** The 12 recordings are made with `say`: clean, no noise, no accents. STT time and WER on real callers will be worse.
- **No streamed first byte.** Both TTS backends are timed per chunk, so "first audio" is when the first chunk is fully synthesized.
- **Shared machine.** Other jobs on the same Mac share its Ollama. Each run records the 1-minute load average and Ollama's loaded models at start and end in `env.json`; numbers from a busy machine are not comparable to a quiet one.
- **Small sample.** 12 utterances × 3 repetitions = 36 turns per strategy, so p95 is close to the maximum.
- **Regex chunking.** Sentence and clause boundaries are found with a regex; abbreviations such as "Dr. Smith" split early.
- **One model, one voice, one machine.** The numbers say nothing about other hardware.

## License

MIT for this repo. The optional extras keep their own licenses and are not bundled: Piper (`piper-tts`) is GPL-3.0-or-later, faster-whisper is MIT, and the downloaded voice and Whisper model carry their publishers' terms.
