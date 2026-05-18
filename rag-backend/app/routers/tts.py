"""
POST /api/audio/speech — OpenAI-compatible TTS endpoint (streaming MP3).

OWUI is configured with AUDIO_TTS_ENGINE=openai and AUDIO_TTS_OPENAI_API_BASE_URL
pointing here. It sends the standard OpenAI /audio/speech body; we proxy it to
Qwen3-TTS running on RunPod (SSH tunnel port 8006) and stream MP3 bytes back
sentence-by-sentence so audio starts playing before full synthesis is done.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.dependencies import get_tts_client
from app.services.tts_client import TTSClient

router = APIRouter()


class SpeechRequest(BaseModel):
    model: str = "qwen3-tts"
    input: str
    voice: str = "de-DE"


@router.post("/audio/speech")
async def audio_speech(
    req: SpeechRequest,
    tts_client: TTSClient | None = Depends(get_tts_client),
) -> StreamingResponse:
    if tts_client is None:
        raise HTTPException(status_code=503, detail="TTS not configured (set TTS_URL)")

    print(f"[TTS] Streaming {len(req.input)} chars", flush=True)
    return StreamingResponse(
        tts_client.stream(req.input),
        media_type="audio/mpeg",
    )
