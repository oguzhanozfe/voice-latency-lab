"""Pluggable STT / LLM / TTS stages. Each one is a small class with a plain method.

STT:  transcribe(utt) -> str             (utt has .wav and .text)
LLM:  stream(text) -> Stream             (iterable of text pieces, .close() aborts from any thread)
TTS:  synth(text, cancel) -> seconds     (seconds of audio produced; raises Cancelled)
      kill()                             (stop in-flight synthesis from another thread)
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import wave
from urllib.parse import urlparse


class Cancelled(Exception):
    pass


def wav_seconds(path: str) -> float:
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


# ---------------------------------------------------------------- STT

class OracleSTT:
    """No speech-to-text installed: hand back the known transcript. Not a measurement."""
    name, measured = "oracle (not measured)", False

    def transcribe(self, utt) -> str:
        return utt.text


class FakeSTT:
    name, measured = "fake", True

    def __init__(self, delay: float = 0.02):
        self.delay = delay

    def transcribe(self, utt) -> str:
        time.sleep(self.delay)
        return utt.text


# ---------------------------------------------------------------- LLM

SYSTEM = ("You are a voice support agent for an online shop. Reply in two or three short, "
          "plain sentences that will be read aloud. No lists, no markdown.")


class FakeStream:
    def __init__(self, pieces, delay, first_delay):
        self.pieces, self.delay, self.first_delay = pieces, delay, first_delay
        self.closed = threading.Event()

    def __iter__(self):
        for i, p in enumerate(self.pieces):
            if self.closed.wait(self.first_delay if i == 0 else self.delay):
                return
            yield p

    def close(self):
        self.closed.set()


class FakeLLM:
    name = "fake"

    def __init__(self, reply: str = "Sure, I can help with that. Your order shipped yesterday. "
                                    "It should arrive on Friday.", delay=0.005, first_delay=0.03):
        self.reply, self.delay, self.first_delay = reply, delay, first_delay

    def stream(self, text: str) -> FakeStream:
        words = self.reply.split(" ")
        return FakeStream([w if i == 0 else " " + w for i, w in enumerate(words)],
                          self.delay, self.first_delay)


class OllamaStream:
    def __init__(self, base_url: str, payload: dict):
        u = urlparse(base_url)
        self.conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=300)
        self.conn.request("POST", "/api/chat", json.dumps(payload), {"Content-Type": "application/json"})
        self.resp = self.conn.getresponse()
        if self.resp.status != 200:
            raise RuntimeError(f"ollama {self.resp.status}: {self.resp.read()[:200]!r}")
        self.stats: dict = {}

    def __iter__(self):
        try:
            while True:
                try:
                    line = self.resp.readline()
                except (OSError, ValueError, http.client.HTTPException):
                    return  # socket shut down by close()
                if not line.strip():
                    return
                d = json.loads(line)
                if d.get("error"):
                    raise RuntimeError(d["error"])
                piece = d.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if d.get("done"):
                    self.stats = {k: d.get(k) for k in ("eval_count", "eval_duration", "prompt_eval_count")}
                    return
        finally:
            self.conn.close()

    def close(self):
        # Shut the socket from outside the blocking read: Ollama stops generating when the client goes away.
        try:
            self.conn.sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass


class OllamaLLM:
    def __init__(self, model="qwen3.5:9b", base_url=None, num_predict=120, seed=7):
        self.model, self.num_predict, self.seed = model, num_predict, seed
        self.base_url = base_url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        self.name = f"ollama {model}"

    def stream(self, text: str) -> OllamaStream:
        return OllamaStream(self.base_url, {
            "model": self.model, "stream": True, "think": False, "keep_alive": "30m",
            "options": {"temperature": 0, "seed": self.seed, "num_predict": self.num_predict},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
        })


# ---------------------------------------------------------------- TTS

class FakeTTS:
    """Synthesis takes `per_char` seconds per character and yields 0.06 s of audio per character."""
    name = "fake"

    def __init__(self, per_char=0.0005, audio_per_char=0.06):
        self.per_char, self.audio_per_char = per_char, audio_per_char

    def synth(self, text: str, cancel: threading.Event) -> float:
        if cancel.wait(self.per_char * len(text)):
            raise Cancelled
        return self.audio_per_char * len(text)

    def kill(self):
        pass


class _SubprocessTTS:
    """Runs a synthesizer process that writes a WAV file; kill() stops it mid-synthesis."""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.tmp = tempfile.mkdtemp(prefix="vlab-tts-")

    def cmd(self, text: str, out: str) -> tuple[list[str], bytes | None]:
        raise NotImplementedError

    def synth(self, text: str, cancel: threading.Event) -> float:
        out = os.path.join(self.tmp, "chunk.wav")
        argv, stdin = self.cmd(text, out)
        with self.lock:
            if cancel.is_set():
                raise Cancelled
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.proc.communicate(stdin)
        if cancel.is_set():
            raise Cancelled
        if self.proc.returncode != 0:
            raise RuntimeError(f"{argv[0]} exited {self.proc.returncode}")
        return wav_seconds(out)

    def kill(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()


class SayTTS(_SubprocessTTS):
    """macOS `say`. It writes a whole file per call, so 'first audio' means the first chunk's file is ready."""

    def __init__(self, voice="Samantha"):
        super().__init__()
        self.voice, self.name = voice, f"say -v {voice}"

    def cmd(self, text, out):
        return ["say", "-v", self.voice, "-o", out, "--file-format=WAVE",
                "--data-format=LEI16@22050", text], None


def available() -> dict:
    """What is installed here. Nothing is installed by this tool."""
    found = {"say": bool(shutil.which("say")),
             "piper": bool(shutil.which("piper")),
             "whisper-cli": bool(shutil.which("whisper-cli"))}
    try:
        import faster_whisper  # noqa: F401
        found["faster-whisper"] = True
    except ImportError:
        found["faster-whisper"] = False
    return found
