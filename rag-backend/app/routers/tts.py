"""
POST /api/audio/speech — OpenAI-compatible TTS endpoint

OWUI is configured with AUDIO_TTS_ENGINE=openai and AUDIO_TTS_OPENAI_API_BASE_URL
pointing here. It sends the standard OpenAI /audio/speech body; we proxy it to
Qwen3-TTS running on RunPod via vLLM-Omni (SSH tunnel port 8006).

Returns raw MP3 bytes so OWUI can play audio directly in the chat.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
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
) -> Response:
    if tts_client is None:
        raise HTTPException(status_code=503, detail="TTS not configured (set TTS_URL)")

    print(f"[TTS] Synthesizing {len(req.input)} chars")
    audio_bytes = await asyncio.to_thread(tts_client.synthesize, req.input)
    print(f"[TTS] Done — {len(audio_bytes)} bytes")

    return Response(content=audio_bytes, media_type="audio/mpeg")
