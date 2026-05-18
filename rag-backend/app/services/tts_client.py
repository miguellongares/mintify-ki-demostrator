from abc import ABC, abstractmethod
from typing import AsyncIterator

import httpx


class TTSClient(ABC):
    @abstractmethod
    def synthesize(self, text: str) -> bytes: ...

    @abstractmethod
    def stream(self, text: str) -> AsyncIterator[bytes]: ...


class Qwen3TTSClient(TTSClient):
    """
    Adapter for the Qwen3-TTS service in ../tts-service/ running on RunPod.

    The service exposes two endpoints:
      POST /v1/audio/speech          → full WAV (blocking)
      POST /v1/audio/speech/stream   → streaming MP3 (sentence-chunked)

    OWUI sends the standard OpenAI /audio/speech body; this class adds the
    voice-design `task_type` + `instructions` fields the upstream service needs.
    """

    def __init__(self, base_url: str, voice_instructions: str):
        self._base_url = base_url.rstrip("/")
        self._voice_instructions = voice_instructions

    def _payload(self, text: str) -> dict:
        return {
            "input": text,
            "task_type": "VoiceDesign",
            "instructions": self._voice_instructions,
        }

    def synthesize(self, text: str) -> bytes:
        resp = httpx.post(
            f"{self._base_url}/v1/audio/speech",
            json=self._payload(text),
            timeout=180.0,
        )
        resp.raise_for_status()
        return resp.content

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        client = httpx.AsyncClient(timeout=None)
        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/audio/speech/stream",
                json=self._payload(text),
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        yield chunk
        finally:
            await client.aclose()
