"""
LLM handler: GPT-4o with live telemetry context injection.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL      = os.getenv("LLM_MODEL", "gpt-4o")

SYSTEM_PROMPT = (
    "You are an F1 Race Engineer talking to the Driver via radio. "
    "You will receive the Driver's voice message along with live telemetry data in brackets. "
    "Analyze the telemetry data to answer the Driver's question accurately. "
    "Use F1 jargon (box box, copy, dirty air, tire deg, graining, blistering, undercut, overcut, ERS, DRS). "
    "Keep responses extremely short, punchy, and urgent — maximum 3 sentences. "
    "Never act like a polite AI assistant. "
    "If the telemetry shows a critical issue (engine blown, severe damage, >80% tyre wear) mention it immediately."
)


async def get_engineer_response(driver_message: str, telemetry_context: str) -> str:
    """
    Send the driver's transcribed message + live telemetry snapshot to the LLM.
    Returns the engineer's text reply.
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")

    user_content = (
        f"{driver_message}\n\n"
        f"[LIVE TELEMETRY]\n{telemetry_context}"
    )

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.7,
        "max_tokens":  120,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type":  "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
