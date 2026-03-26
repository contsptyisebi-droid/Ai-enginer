"""
Audio handler: Speech-to-Text and Text-to-Speech.

Supports two provider modes controlled by environment variables:

  STT_PROVIDER  – "openai" (default) uses the OpenAI Whisper API.
                  "local"  uses faster-whisper (whisper.cpp via CTranslate2)
                           to run transcription entirely on your machine.

  TTS_PROVIDER  – "openai"   (default) uses the OpenAI TTS API.
                  "edge-tts"  uses Microsoft Edge TTS (free, no API key).
"""

import asyncio
import io
import logging
import os
import tempfile

import httpx

logger = logging.getLogger(__name__)

# ─── Provider selection ───────────────────────────────────────────────────────
STT_PROVIDER = (os.getenv("STT_PROVIDER") or "openai").lower()   # "openai" | "local"
TTS_PROVIDER = (os.getenv("TTS_PROVIDER") or "openai").lower()   # "openai" | "edge-tts"

# ─── OpenAI settings (used when provider is "openai") ────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
WHISPER_MODEL  = "whisper-1"
TTS_MODEL      = "tts-1"
TTS_VOICE      = os.getenv("TTS_VOICE", "onyx")

# ─── Local STT settings (faster-whisper / whisper.cpp) ────────────────────────
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")  # tiny, base, small, medium, large-v3
WHISPER_DEVICE     = os.getenv("WHISPER_DEVICE", "auto")      # auto, cpu, cuda

# ─── Edge-TTS settings ───────────────────────────────────────────────────────
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "en-GB-RyanNeural")  # deep British voice

# ─── Startup validation ──────────────────────────────────────────────────────
_openai_needed = STT_PROVIDER == "openai" or TTS_PROVIDER == "openai"

if _openai_needed and not OPENAI_API_KEY:
    logger.warning(
        "OPENAI_API_KEY is not set – OpenAI-based STT/TTS will not work. "
        "Add it to your .env file, or switch to local providers "
        "(STT_PROVIDER=local, TTS_PROVIDER=edge-tts)."
    )

logger.info(
    "Audio config: STT_PROVIDER=%s  TTS_PROVIDER=%s",
    STT_PROVIDER,
    TTS_PROVIDER,
)

# ─── Lazy-loaded local model singletons ───────────────────────────────────────
_whisper_model = None


def _get_whisper_model():
    """Lazily load the faster-whisper model (downloads on first use)."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        logger.info(
            "Loading local Whisper model: size=%s  device=%s",
            WHISPER_MODEL_SIZE,
            WHISPER_DEVICE,
        )
        _whisper_model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=WHISPER_DEVICE,
            compute_type="int8",
        )
        logger.info("Local Whisper model loaded successfully")
    return _whisper_model


# ── STT helpers ───────────────────────────────────────────────────────────────

async def _transcribe_openai(audio_bytes: bytes, content_type: str) -> str:
    """Transcribe via the OpenAI Whisper API."""
    if not OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY is not set – required for OpenAI Whisper speech-to-text. "
            "Add it to your .env, or set STT_PROVIDER=local to use faster-whisper."
        )

    ext_map = {
        "audio/webm": "webm",
        "audio/ogg":  "ogg",
        "audio/mp4":  "mp4",
        "audio/mpeg": "mp3",
        "audio/wav":  "wav",
        "audio/flac": "flac",
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


def _transcribe_local_sync(audio_bytes: bytes, content_type: str) -> str:
    """Run faster-whisper transcription (blocking). Called via asyncio executor."""
    model = _get_whisper_model()

    ext_map = {
        "audio/webm": ".webm",
        "audio/ogg":  ".ogg",
        "audio/mp4":  ".mp4",
        "audio/mpeg": ".mp3",
        "audio/wav":  ".wav",
        "audio/flac": ".flac",
    }
    suffix = ext_map.get(content_type.split(";")[0].strip(), ".webm")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        segments, _info = model.transcribe(tmp.name, beam_size=5)
        return " ".join(seg.text.strip() for seg in segments).strip()


async def _transcribe_local(audio_bytes: bytes, content_type: str) -> str:
    """Transcribe using the local faster-whisper model (non-blocking wrapper)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _transcribe_local_sync, audio_bytes, content_type
    )


# ── TTS helpers ───────────────────────────────────────────────────────────────

async def _synthesize_openai(text: str) -> bytes:
    """Synthesise speech via the OpenAI TTS API. Returns MP3 bytes."""
    if not OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY is not set – required for OpenAI TTS. "
            "Add it to your .env, or set TTS_PROVIDER=edge-tts."
        )

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


async def _synthesize_edge_tts(text: str) -> bytes:
    """Synthesise speech using Microsoft Edge TTS. Returns MP3 bytes."""
    import edge_tts

    communicate = edge_tts.Communicate(text, EDGE_TTS_VOICE)
    audio_buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_buffer.write(chunk["data"])
    return audio_buffer.getvalue()


# ─── Public API (unchanged signatures) ───────────────────────────────────────

async def transcribe_audio(audio_bytes: bytes, content_type: str = "audio/webm") -> str:
    """Transcribe speech from raw audio bytes. Provider chosen by STT_PROVIDER."""
    if STT_PROVIDER == "local":
        return await _transcribe_local(audio_bytes, content_type)
    return await _transcribe_openai(audio_bytes, content_type)


async def synthesize_speech(text: str) -> bytes:
    """Convert text to speech. Returns MP3 bytes. Provider chosen by TTS_PROVIDER."""
    if TTS_PROVIDER == "edge-tts":
        return await _synthesize_edge_tts(text)
    return await _synthesize_openai(text)
