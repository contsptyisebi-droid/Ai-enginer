"""
Scuderia AI – FastAPI backend
Serves the frontend, handles voice API calls, and starts the UDP telemetry listener.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.audio_handler import synthesize_speech, transcribe_audio
from backend.llm_handler import get_engineer_response
from backend.telemetry import TelemetryListener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Globals ──────────────────────────────────────────────────────────────────
# UDP_BIND_ADDRESS can be set to "127.0.0.1" to restrict telemetry to localhost
# (when the game runs on the same machine). Leave empty to accept from LAN.
UDP_BIND_ADDRESS = os.getenv("UDP_BIND_ADDRESS", "127.0.0.1")
telemetry = TelemetryListener(bind_address=UDP_BIND_ADDRESS)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


# ─── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    telemetry.start()
    logger.info("Telemetry UDP listener started")
    yield
    telemetry.stop()
    logger.info("Telemetry UDP listener stopped")


# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Scuderia AI – F1 Race Engineer", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── API routes ───────────────────────────────────────────────────────────────
@app.post("/api/voice")
async def voice_endpoint(request: Request, audio: UploadFile = File(...)):
    """
    Accepts a multipart audio file from the PTT button.
    1. Transcribes via Whisper
    2. Merges with live telemetry snapshot
    3. Queries GPT-4o
    4. Synthesises TTS reply
    Returns: MP3 audio bytes + metadata headers
    """
    audio_bytes   = await audio.read()
    content_type  = audio.content_type or "audio/webm"

    try:
        # Step 1 – STT
        driver_text = await transcribe_audio(audio_bytes, content_type)
        logger.info("Driver: %s", driver_text)

        # Step 2 – Telemetry snapshot
        state   = telemetry.get_state_snapshot()
        context = state.to_context_string()

        # Step 3 – LLM
        reply_text = await get_engineer_response(driver_text, context)
        logger.info("Engineer: %s", reply_text)

        # Step 4 – TTS
        mp3_bytes = await synthesize_speech(reply_text)

        return Response(
            content=mp3_bytes,
            media_type="audio/mpeg",
            headers={
                "X-Driver-Text":   driver_text,
                "X-Engineer-Text": reply_text,
            },
        )

    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Error processing voice request")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@app.get("/api/telemetry")
async def telemetry_endpoint():
    """Return the current telemetry state as JSON (for debugging)."""
    state = telemetry.get_state_snapshot()
    return JSONResponse(content={
        "position":             state.position,
        "current_lap":          state.current_lap,
        "tyre_name":            state.tyre_name,
        "tyre_age_laps":        state.tyre_age_laps,
        "tyre_wear":            state.tyre_wear,
        "tyre_surface_temp":    state.tyre_surface_temp,
        "fuel_in_tank":         state.fuel_in_tank,
        "fuel_remaining_laps":  state.fuel_remaining_laps,
        "fuel_mix":             state.fuel_mix,
        "ers_store_pct":        round(state.ers_store_energy / 4_000_000 * 100, 1),
        "ers_deploy_mode":      state.ers_deploy_mode,
        "speed_kmh":            state.speed_kmh,
        "gear":                 state.gear,
        "drs_active":           state.drs_active,
        "engine_temp":          state.engine_temp,
        "engine_dmg":           state.engine_dmg,
        "engine_blown":         state.engine_blown,
        "engine_seized":        state.engine_seized,
        "front_left_wing_dmg":  state.front_left_wing_dmg,
        "front_right_wing_dmg": state.front_right_wing_dmg,
        "rear_wing_dmg":        state.rear_wing_dmg,
        "floor_dmg":            state.floor_dmg,
        "diffuser_dmg":         state.diffuser_dmg,
        "sidepod_dmg":          state.sidepod_dmg,
        "gearbox_dmg":          state.gearbox_dmg,
        "drs_fault":            state.drs_fault,
        "ers_fault":            state.ers_fault,
        "penalties_sec":        state.penalties_sec,
        "total_warnings":       state.total_warnings,
        "pit_stops":            state.num_pit_stops,
        "fia_flag":             state.fia_flag,
        "delta_to_leader_ms":   state.delta_to_leader_ms,
        "delta_to_front_ms":    state.delta_to_front_ms,
        "context_string":       state.to_context_string(),
    })


# ─── Static frontend ──────────────────────────────────────────────────────────
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
