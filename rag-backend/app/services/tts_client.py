from abc import ABC, abstractmethod

import httpx


class TTSClient(ABC):
    @abstractmethod
    def synthesize(self, text: str) -> bytes: ...


class Qwen3TTSClient(TTSClient):
    """
    Adapter for vLLM-Omni serving Qwen3-TTS-12Hz-1.7B-VoiceDesign.

    vllm-omni's /v1/audio/speech accepts an extended body beyond the OpenAI spec:
      task_type   — "VoiceDesign" uses natural-language voice instructions
      instructions — free-form German voice description

    OWUI sends the standard OpenAI format; this class handles the translation.
    """

    def __init__(self, base_url: str, voice_instructions: str):
        self._base_url = base_url.rstrip("/")
        self._voice_instructions = voice_instructions

    def synthesize(self, text: str) -> bytes:
        resp = httpx.post(
            f"{self._base_url}/v1/audio/speech",
            json={
                "input": text,
                "task_type": "VoiceDesign",
                "instructions": self._voice_instructions,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.content
