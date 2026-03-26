"""
Audio handler: Speech-to-Text (OpenAI Whisper) and Text-to-Speech (OpenAI TTS).
"""

import logging
import os
import tempfile

import httpx

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

WHISPER_MODEL = "whisper-1"
TTS_MODEL     = "tts-1"
TTS_VOICE     = os.getenv("TTS_VOICE", "onyx")   # onyx sounds authoritative – good for race engineer


async def transcribe_audio(audio_bytes: bytes, content_type: str = "audio/webm") -> str:
    """Send raw audio bytes to OpenAI Whisper and return the transcription."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")

    # Determine file extension from content type
    ext_map = {
        "audio/webm":  "webm",
        "audio/ogg":   "ogg",
        "audio/mp4":   "mp4",
        "audio/mpeg":  "mp3",
        "audio/wav":   "wav",
        "audio/flac":  "flac",
    }
    ext = ext_map.get(content_type.split(";")[0].strip(), "webm")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={
                "file": (f"audio.{ext}", audio_bytes, content_type),
                "model": (None, WHISPER_MODEL),
            },
        )
        response.raise_for_status()
        return response.json().get("text", "").strip()


async def synthesize_speech(text: str) -> bytes:
    """Convert text to speech using OpenAI TTS. Returns raw MP3 bytes."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": TTS_MODEL,
                "input": text,
                "voice": TTS_VOICE,
                "response_format": "mp3",
            },
        )
        response.raise_for_status()
        return response.content
