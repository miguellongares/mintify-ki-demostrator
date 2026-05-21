"""
Qwen3-TTS inference server for RunPod.

Endpoints:
    POST /v1/audio/speech         — blocking, returns full WAV
    POST /v1/audio/speech/stream  — streaming MP3 (chunked PCM via ffmpeg)
    GET  /health                  — liveness

Run on RunPod:
    uvicorn main:app --host 0.0.0.0 --port 8006

Requires ffmpeg on host:
    apt-get install -y ffmpeg
"""

import asyncio
import io
import subprocess
import time
from contextlib import asynccontextmanager

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from qwen_tts import Qwen3TTSModel

# ── Config ───────────────────────────────────────────────────────────────────
MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
REF_AUDIO = "/workspace/tts_service/german_ref.wav"
REF_TEXT = (
    "Der durchschnittliche Zinssatz für Tagesgeldkonten beträgt derzeit "
    "3,75 Prozent – deutlich höher als noch vor zwölf Monaten."
    "ür spezifische Sparstrategien oder Unterstützung bei der Finanzplanung würde ich"
    "empfehlen, sich an einen Finanzberater oder eine Finanzberatungsstelle zu wenden."
)

DEFAULT_INSTRUCT = (
    "Male, native German speaker, middle-aged, expert in finance and professional tone."
)
EMIT_EVERY = 4
DECODE_WINDOW = 80
MP3_BITRATE = "96k"
SAMPLE_RATE = 24000

torch.set_float32_matmul_precision("high")

_model: Qwen3TTSModel | None = None
_voice_prompt = None
_tts_lock = asyncio.Lock()  # serialize TTS requests (CUDA graphs aren't thread-safe)


# ── Lifespan: load + warmup ─────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _voice_prompt

    print(f"[TTS] Loading {MODEL_ID} ...", flush=True)
    t0 = time.time()
    _model = Qwen3TTSModel.from_pretrained(
        MODEL_ID,
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="flash_attention_2",
    )
    print(f"[TTS] Model loaded in {time.time()-t0:.1f}s", flush=True)

    print("[TTS] Creating voice clone prompt...", flush=True)
    _voice_prompt = _model.create_voice_clone_prompt(ref_audio=REF_AUDIO, ref_text=REF_TEXT)

    print("[TTS] Enabling streaming optimizations...", flush=True)
    _model.enable_streaming_optimizations(
        decode_window_frames=DECODE_WINDOW,
        use_compile=True,
        use_cuda_graphs=False,
        compile_mode="reduce-overhead",
        use_fast_codebook=True,
        compile_codebook_predictor=True,
        compile_talker=True,
    )

    print("[TTS] Warming up (compilation, ~90-180s)...", flush=True)
    warmup_texts = [
        "Test eins zwei drei vier fünf. Test eins zwei drei vier fünf.",
        "Hallo, wie geht es dir? Das ist der zweite Aufwärmlauf.",
        "Der dritte Testlauf zum vollständigen Aufwärmen aller Modellkomponenten.",
    ]
    for i, text in enumerate(warmup_texts, 1):
        t0 = time.time()
        chunks = list(_stream_chunks(text))
        print(f"[TTS] Warmup {i}: {time.time()-t0:.1f}s, {len(chunks)} chunks", flush=True)

    print("[TTS] Ready.", flush=True)
    yield
    _model = None


app = FastAPI(lifespan=lifespan)


# ── Models ───────────────────────────────────────────────────────────────────
class SpeechRequest(BaseModel):
    input: str
    task_type: str = "VoiceClone"           # kept for API compatibility, not used
    instructions: str = DEFAULT_INSTRUCT    # kept for API compatibility, not used


# ── Core generator ──────────────────────────────────────────────────────────
def _stream_chunks(text: str):
    """Yield PCM float32 numpy arrays as the model produces them."""
    for chunk, _sr in _model.stream_generate_voice_clone(  # type: ignore[union-attr]
        text=text,
        language="german",
        voice_clone_prompt=_voice_prompt,
        emit_every_frames=EMIT_EVERY,
        decode_window_frames=DECODE_WINDOW,
        overlap_samples=0,
    ):
        yield np.asarray(chunk, dtype=np.float32)


# ── Endpoints ────────────────────────────────────────────────────────────────
@app.post("/v1/audio/speech")
async def audio_speech(req: SpeechRequest) -> Response:
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    async with _tts_lock:
        chunks = await asyncio.to_thread(lambda: list(_stream_chunks(req.input)))

    if not chunks:
        raise HTTPException(status_code=500, detail="No audio generated")

    wav = np.concatenate(chunks)
    buf = io.BytesIO()
    sf.write(buf, wav, SAMPLE_RATE, format="WAV")
    return Response(content=buf.getvalue(), media_type="audio/wav")


@app.post("/v1/audio/speech/stream")
async def audio_speech_stream(req: SpeechRequest) -> StreamingResponse:
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    print(f"[TTS] Streaming {len(req.input)} chars", flush=True)

    async def mp3_stream():
        async with _tts_lock:
            ff = subprocess.Popen(
                [
                    "ffmpeg", "-loglevel", "error",
                    "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
                    "-f", "mp3", "-b:a", MP3_BITRATE, "pipe:1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )

            # Feed PCM into ffmpeg in a background thread, read MP3 out here.
            def feed():
                try:
                    for chunk in _stream_chunks(req.input):
                        ff.stdin.write(chunk.tobytes())
                        ff.stdin.flush()
                finally:
                    try:
                        ff.stdin.close()
                    except Exception:
                        pass

            feeder = asyncio.create_task(asyncio.to_thread(feed))

            try:
                while True:
                    mp3_chunk = await asyncio.to_thread(ff.stdout.read, 4096)
                    if not mp3_chunk:
                        break
                    yield mp3_chunk
            finally:
                await feeder
                ff.wait()

    return StreamingResponse(mp3_stream(), media_type="audio/mpeg")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_loaded": _model is not None}