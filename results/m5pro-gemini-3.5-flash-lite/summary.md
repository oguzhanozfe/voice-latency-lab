# Results: m5pro-gemini-3.5-flash-lite

Times in ms (p50 / p95), RTF = processing time / audio length.

```
{
 "os": "macOS 27.2.1",
 "chip": "Apple M5 Pro",
 "ram_gb": 24,
 "python": "3.12.14",
 "stt": "faster-whisper base.en (cpu, int8, beam 1)",
 "llm": "gemini gemini-3.5-flash-lite (cloud)",
 "tts": [
  "piper en_US-lessac-medium.onnx (in-process)"
 ],
 "packages": {
  "faster-whisper": "1.2.1",
  "ctranslate2": "4.9.0",
  "piper-tts": "1.8.0",
  "onnxruntime": "1.32.0"
 },
 "model_files_sha256": {
  "whisper model.bin": "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef",
  "piper en_US-lessac-medium.onnx": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"
 },
 "load_at_start": {
  "loadavg_1m": 4.94
 },
 "load_at_end": {
  "loadavg_1m": 2.29
 },
 "reps": 1,
 "utterances": 12,
 "barge_in_after_s": 0.3,
 "skipped_turns": 0
}
```

| strategy | n | STT p50 / p95 | first token p50 / p95 | first audio p50 / p95 | e2e first audio p50 / p95 | mid-reply gaps p50 / p95 | turn total p50 / p95 | TTS RTF p50 / p95 | STT RTF p50 / p95 | STT WER p50 / p95 | cancel p50 / p95 | cancel LLM p50 / p95 | cancel TTS p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| full | 12 | 243 / 263 | 2679 / 23165 | 3328 / 23465 | 3574 / 23715 | 0 / 0 | 14647 / 32097 | 0.01 / 0.02 | 0.08 / 0.09 | 0.00 / 0.06 | – / – | – / – | – / – |
| sentence | 12 | 224 / 255 | 975 / 10687 | 1080 / 10977 | 1301 / 11204 | 0 / 0 | 11438 / 19341 | 0.01 / 0.01 | 0.07 / 0.09 | 0.00 / 0.06 | – / – | – / – | – / – |
| clause | 12 | 223 / 238 | 4215 / 12944 | 4332 / 13210 | 4557 / 13441 | 0 / 0 | 14054 / 25479 | 0.01 / 0.02 | 0.07 / 0.09 | 0.00 / 0.06 | – / – | – / – | – / – |
| barge-in after first token (sentence) | 12 | 228 / 269 | 1213 / 12112 | 1132 / 11822 | 1356 / 12066 | – / – | 1746 / 12688 | 0.01 / 0.02 | 0.07 / 0.10 | 0.00 / 0.06 | 0 / 0 | 0 / 1 | 0 / 0 |
| barge-in after first audio (sentence) | 12 | 249 / 264 | 1238 / 11888 | 1907 / 11976 | 2167 / 12213 | – / – | 2474 / 12514 | 0.01 / 0.02 | 0.08 / 0.10 | 0.00 / 0.06 | 0 / 0 | 0 / 0 | 0 / 0 |

Barge-ins: 24; the LLM was still generating at the interrupt in 3 of them. 'cancel LLM' and 'cancel TTS' only count turns where that stage still had work.

After each barge-in a tiny request was sent at once. Its time to first token: p50 2149 ms / p95 14755 ms (normal turns: p50 975 ms). Similar numbers are consistent with the cancelled generation having stopped (a probe alone cannot prove it if the server runs requests in parallel).

## Run-level checks

- 1-minute load average per turn: min 1.88, median 2.62, max 4.72.
- Turns with time to first token over 1 s: 34 of 60 (run-order indices 0–58).
- Time to first token, all turns (n=60): p50 1307 ms, p95 14354 ms.
- Mid-reply gaps: 0 of 36 non-barge-in turns had one; max 0 ms.
- Prompt cache: turns repeating the previous turn's prompt got the first token in p50 1023 ms (n=36); turns after a probe or a new utterance in 2024 ms (n=24).
