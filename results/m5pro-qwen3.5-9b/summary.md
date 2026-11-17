# Results: m5pro-qwen3.5-9b

Times in ms (p50 / p95), RTF = processing time / audio length.

```
{
 "os": "macOS 27.2.1",
 "chip": "Apple M5 Pro",
 "ram_gb": 24,
 "python": "3.12.14",
 "stt": "faster-whisper base.en (cpu, int8, beam 1)",
 "llm": "ollama qwen3.5:9b",
 "tts": [
  "say -v Samantha",
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
 "say_voice": "Samantha",
 "ollama": "0.33.3",
 "model": "qwen3.5:9b (9.0B, Q4_K_M, digest 56671c2ab938)",
 "load_at_start": {
  "loadavg_1m": 10.49,
  "ollama_loaded": [
   "qwen3.5:9b"
  ]
 },
 "load_at_end": {
  "loadavg_1m": 6.03,
  "ollama_loaded": [
   "qwen3.5:9b"
  ]
 },
 "reps": 3,
 "utterances": 12,
 "barge_in_after_s": 0.3,
 "skipped_turns": 0
}
```

| strategy | n | STT p50 / p95 | first token p50 / p95 | first audio p50 / p95 | e2e first audio p50 / p95 | mid-reply gaps p50 / p95 | turn total p50 / p95 | TTS RTF p50 / p95 | STT RTF p50 / p95 | STT WER p50 / p95 | cancel p50 / p95 | cancel LLM p50 / p95 | cancel TTS p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| say · full | 36 | 256 / 290 | 153 / 4697 | 2141 / 6709 | 2397 / 6999 | 0 / 0 | 13501 / 18865 | 0.10 / 0.17 | 0.08 / 0.10 | 0.00 / 0.12 | – / – | – / – | – / – |
| say · sentence | 36 | 255 / 318 | 74 / 3155 | 1401 / 4335 | 1661 / 4648 | 0 / 0 | 12456 / 17858 | 0.18 / 0.26 | 0.08 / 0.11 | 0.00 / 0.12 | – / – | – / – | – / – |
| say · clause | 36 | 259 / 315 | 74 / 3473 | 1380 / 4786 | 1637 / 5088 | 0 / 0 | 12838 / 17914 | 0.18 / 0.26 | 0.08 / 0.11 | 0.00 / 0.12 | – / – | – / – | – / – |
| say · barge-in after first token (sentence) | 36 | 259 / 321 | 79 / 3534 | – / – | – / – | – / – | 639 / 4161 | – / – | 0.08 / 0.11 | 0.00 / 0.12 | 0 / 1 | 0 / 1 | 0 / 1 |
| say · barge-in after first audio (sentence) | 36 | 261 / 291 | 150 / 4798 | 1430 / 6282 | 1674 / 6576 | – / – | 1982 / 6886 | 0.21 / 0.44 | 0.08 / 0.11 | 0.00 / 0.12 | 1 / 3 | – / – | 1 / 3 |
| piper · full | 36 | 268 / 310 | 151 / 4917 | 1356 / 5844 | 1629 / 6136 | 0 / 0 | 12664 / 17292 | 0.02 / 0.02 | 0.09 / 0.11 | 0.00 / 0.12 | – / – | – / – | – / – |
| piper · sentence | 36 | 254 / 309 | 70 / 4815 | 542 / 5187 | 800 / 5448 | 0 / 0 | 12109 / 16443 | 0.02 / 0.02 | 0.08 / 0.10 | 0.00 / 0.12 | – / – | – / – | – / – |
| piper · clause | 36 | 252 / 292 | 59 / 4926 | 523 / 5277 | 771 / 5564 | 0 / 0 | 11881 / 16485 | 0.02 / 0.02 | 0.08 / 0.10 | 0.00 / 0.12 | – / – | – / – | – / – |
| piper · barge-in after first token (sentence) | 36 | 255 / 282 | 56 / 4566 | 344 / 5247 | 611 / 5508 | – / – | 618 / 5145 | 0.02 / 0.03 | 0.08 / 0.10 | 0.00 / 0.12 | 0 / 1 | 0 / 1 | 0 / 1 |
| piper · barge-in after first audio (sentence) | 36 | 257 / 300 | 149 / 4661 | 576 / 5095 | 816 / 5384 | – / – | 1125 / 5692 | 0.02 / 0.03 | 0.08 / 0.11 | 0.00 / 0.12 | 0 / 40 | 0 / 0 | 0 / 40 |

Barge-ins: 144; the LLM was still generating at the interrupt in 102 of them. 'cancel LLM' and 'cancel TTS' only count turns where that stage still had work.

After each barge-in a tiny request was sent at once. Its time to first token: p50 169 ms / p95 4801 ms (normal turns: p50 70 ms). Similar numbers mean the cancelled generation really stopped instead of running on in the background.
