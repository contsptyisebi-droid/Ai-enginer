"""
LLM handler: uses Anthropic (Claude) with live telemetry context injection.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# ─── API key ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ─── Model ────────────────────────────────────────────────────────────────────
LLM_MODEL = os.getenv("LLM_MODEL", "claude-3-5-haiku-20241022")

# ─── Startup validation ──────────────────────────────────────────────────────
logger.info("LLM config: provider=anthropic  model=%s", LLM_MODEL)
if not ANTHROPIC_API_KEY:
    logger.warning(
        "ANTHROPIC_API_KEY is not set. "
        "Set it in your .env file or export it as an environment variable."
    )

SYSTEM_PROMPT = (
    "You are a no-nonsense F1 Pit Wall Race Engineer speaking to YOUR driver over team radio. "
    "You receive the driver's voice message plus a real-time telemetry snapshot in [LIVE TELEMETRY] tags. "
    "Rules: "
    "1) Analyse the telemetry data to give accurate, data-driven answers. "
    "2) Use authentic F1 radio language: 'copy', 'box box', 'stay out', 'push push', 'delta positive/negative', "
    "'deg is high', 'graining', 'blistering', 'undercut', 'overcut', 'we are Plan B'. "
    "3) Be concise – maximum 2-3 short sentences, like a real pit wall message. "
    "4) If telemetry shows a CRITICAL issue (engine blown/seized, tyre wear >80%, DRS/ERS fault, heavy damage) "
    "report it IMMEDIATELY, even if the driver asked about something else. "
    "5) Never break character. Never say you are an AI. You ARE the race engineer."
)


# ─── Anthropic backend ───────────────────────────────────────────────────────
async def _anthropic_chat(user_content: str) -> str:
    payload = {
        "model": LLM_MODEL,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.7,
        "max_tokens":  300,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        # Anthropic returns content as a list of blocks
        return data["content"][0]["text"].strip()


# ─── Public entry point ──────────────────────────────────────────────────────
async def get_engineer_response(driver_message: str, telemetry_context: str) -> str:
    """
    Send the driver's transcribed message + live telemetry snapshot to Claude.
    Returns the engineer's text reply.
    """
    if not ANTHROPIC_API_KEY:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file "
            "(e.g. ANTHROPIC_API_KEY=sk-ant-...) and restart the server."
        )

    user_content = (
        f"{driver_message}\n\n"
        f"[LIVE TELEMETRY]\n{telemetry_context}"
    )

    logger.info("LLM provider=anthropic  model=%s", LLM_MODEL)

    return await _anthropic_chat(user_content)
