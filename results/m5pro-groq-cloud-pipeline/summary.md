# Results: m5pro-groq-cloud-pipeline

Times in ms (p50 / p95), RTF = processing time / audio length.

```
{
 "os": "macOS 27.2.1",
 "chip": "Apple M5 Pro",
 "ram_gb": 24,
 "python": "3.12.14",
 "stt": "groq whisper-large-v3-turbo (cloud)",
 "llm": "groq qwen/qwen3.8-27b (cloud)",
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
  "piper en_US-lessac-medium.onnx": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"
 },
 "load_at_start": {
  "loadavg_1m": 4.27,
  "network": {
   "host": "api.groq.com",
   "tcp_connect_ms_p50": 44.4,
   "link": "Wi-Fi"
  }
 },
 "load_at_end": {
  "loadavg_1m": 5.02,
  "network": {
   "host": "api.groq.com",
   "tcp_connect_ms_p50": 43.5,
   "link": "Wi-Fi"
  }
 },
 "reps": 1,
 "utterances": 4,
 "barge_in_after_s": 0.3,
 "skipped_turns": 0
}
```

| strategy | n | STT p50 / p95 | first token p50 / p95 | first audio p50 / p95 | e2e first audio p50 / p95 | mid-reply gaps p50 / p95 | turn total p50 / p95 | TTS RTF p50 / p95 | STT RTF p50 / p95 | STT WER p50 / p95 | cancel p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| full | 4 | 467 / 502 | 205 / 304 | 393 / 511 | 825 / 1001 | 0 / 0 | 8127 / 10320 | 0.02 / 0.02 | 0.14 / 0.15 | 0.00 / 0.00 | – / – |
| sentence | 4 | 428 / 492 | 338 / 649 | 391 / 770 | 859 / 1167 | 0 / 0 | 7876 / 10761 | 0.01 / 0.01 | 0.13 / 0.14 | 0.00 / 0.00 | – / – |
| clause | 4 | 462 / 501 | 339 / 740 | 385 / 801 | 868 / 1267 | 0 / 0 | 8149 / 10957 | 0.01 / 0.01 | 0.14 / 0.17 | 0.00 / 0.00 | – / – |
| barge-in after first token (sentence) | 4 | 500 / 512 | 324 / 573 | 414 / 645 | 866 / 1126 | – / – | 1081 / 1368 | 0.01 / 0.02 | 0.14 / 0.15 | 0.00 / 0.00 | 0 / 0 |
| barge-in after first audio (sentence) | 4 | 498 / 501 | 296 / 592 | 348 / 656 | 847 / 1156 | – / – | 1149 / 1457 | 0.01 / 0.02 | 0.14 / 0.17 | 0.00 / 0.00 | 0 / 0 |

Barge-ins: 8; the LLM was still generating at the interrupt in 0 of them. 'cancel LLM' and 'cancel TTS' only count turns where that stage still had work.

After each barge-in a tiny request was sent at once. Its time to first token: p50 379 ms / p95 2598 ms (normal turns: p50 338 ms). Similar numbers are consistent with the cancelled generation having stopped (a probe alone cannot prove it if the server runs requests in parallel).

## Run-level checks

- 1-minute load average per turn: min 4.33, median 4.81, max 5.02.
- Turns with time to first token over 1 s: 0 of 20.
- Time to first token, all turns (n=20): p50 322 ms, p95 707 ms.
- Mid-reply gaps: 0 of 12 non-barge-in turns had one; max 0 ms.
- Prompt cache: turns repeating the previous turn's prompt got the first token in p50 338 ms (n=12); turns after a probe or a new utterance in 237 ms (n=8).
