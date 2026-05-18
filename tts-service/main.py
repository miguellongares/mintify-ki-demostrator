"""
Qwen3-TTS inference server for RunPod.

Endpoints:
    POST /v1/audio/speech         — blocking, returns full WAV
    POST /v1/audio/speech/stream  — streaming MP3 (sentence-chunked)
    GET  /health                  — liveness

Run on RunPod with:
    uvicorn main:app --host 0.0.0.0 --port 8006

Then open SSH tunnel on your local machine:
    ssh -N -L 8006:localhost:8006 ...

Requires ffmpeg installed on the RunPod host (apt-get install -y ffmpeg).
"""

import io
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from queue import Queue

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from qwen_tts import Qwen3TTSModel

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
DEFAULT_INSTRUCT = (
    "Male, native German speaker, middle-aged, expert in finance and professional tone."
)
MIN_SENTENCE_CHARS = 40
MP3_BITRATE = "96k"

_model: Qwen3TTSModel | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    print(f"[TTS] Loading {MODEL_ID} ...", flush=True)
    _model = Qwen3TTSModel.from_pretrained(
        MODEL_ID,
        device_map="auto",
        dtype=torch.float16,
    )
    print("[TTS] Model ready.", flush=True)
    yield
    _model = None


app = FastAPI(lifespan=lifespan)


class SpeechRequest(BaseModel):
    input: str
    task_type: str = "VoiceDesign"
    instructions: str = DEFAULT_INSTRUCT


def _synthesize(text: str, instruct: str) -> tuple[np.ndarray, int]:
    wavs, sr = _model.generate_voice_design(  # type: ignore[union-attr]
        text=text,
        language="German",
        instruct=instruct,
    )
    wav = wavs[0]
    if hasattr(wav, "detach"):
        wav = wav.detach().cpu().numpy()
    return np.asarray(wav, dtype=np.float32), int(sr)


@app.post("/v1/audio/speech")
def audio_speech(req: SpeechRequest) -> Response:
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    wav, sr = _synthesize(req.input, req.instructions)
    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="WAV")
    return Response(content=buf.getvalue(), media_type="audio/wav")


# ── streaming ────────────────────────────────────────────────────────────────

_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")


def split_sentences_de(text: str, min_chars: int = MIN_SENTENCE_CHARS) -> list[str]:
    """Split text on sentence-ending punctuation. Merge fragments shorter than
    `min_chars` into the next chunk so prosody isn't choppy."""
    raw = [s.strip() for s in _SENT_SPLIT.split(text) if s and s.strip()]
    if not raw:
        return []
    merged: list[str] = []
    buf = ""
    for s in raw:
        buf = f"{buf} {s}".strip() if buf else s
        if len(buf) >= min_chars:
            merged.append(buf)
            buf = ""
    if buf:
        if merged:
            merged[-1] = f"{merged[-1]} {buf}".strip()
        else:
            merged.append(buf)
    return merged


def _spawn_ffmpeg(sr: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            "ffmpeg", "-loglevel", "error",
            "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
            "-f", "mp3", "-b:a", MP3_BITRATE, "pipe:1",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )


@app.post("/v1/audio/speech/stream")
def audio_speech_stream(req: SpeechRequest) -> StreamingResponse:
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    sentences = split_sentences_de(req.input)
    if not sentences:
        raise HTTPException(status_code=400, detail="Empty input after sentence split")

    print(f"[TTS] Streaming {len(sentences)} sentence(s) for {len(req.input)} chars",
          flush=True)

    # Probe sample rate on the first sentence; we need it to configure ffmpeg.
    first_wav, sr = _synthesize(sentences[0], req.instructions)

    ff = _spawn_ffmpeg(sr)

    # Order-preserving queue of completed PCM chunks fed by a worker pool.
    pending: Queue[tuple[int, np.ndarray | None]] = Queue()
    executor = ThreadPoolExecutor(max_workers=2)

    def _job(idx: int, text: str):
        try:
            wav, _ = _synthesize(text, req.instructions)
            pending.put((idx, wav))
        except Exception as e:
            print(f"[TTS] sentence {idx} failed: {e}", flush=True)
            pending.put((idx, None))

    # Sentence 0 already done; submit the rest.
    pending.put((0, first_wav))
    for i, s in enumerate(sentences[1:], start=1):
        executor.submit(_job, i, s)

    def _feeder():
        next_idx = 0
        buffered: dict[int, np.ndarray | None] = {}
        total = len(sentences)
        try:
            while next_idx < total:
                if next_idx in buffered:
                    wav = buffered.pop(next_idx)
                else:
                    idx, wav = pending.get()
                    if idx != next_idx:
                        buffered[idx] = wav
                        continue
                if wav is not None:
                    ff.stdin.write(wav.tobytes())  # type: ignore[union-attr]
                    ff.stdin.flush()  # type: ignore[union-attr]
                next_idx += 1
        finally:
            try:
                ff.stdin.close()  # type: ignore[union-attr]
            except Exception:
                pass

    threading.Thread(target=_feeder, daemon=True).start()

    def _reader():
        try:
            while True:
                chunk = ff.stdout.read(16384)  # type: ignore[union-attr]
                if not chunk:
                    break
                yield chunk
        finally:
            ff.wait()
            executor.shutdown(wait=False)

    return StreamingResponse(_reader(), media_type="audio/mpeg")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_loaded": _model is not None}
