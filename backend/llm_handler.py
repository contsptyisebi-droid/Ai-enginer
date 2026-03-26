"""
LLM handler: supports OpenAI (GPT-4o) and Anthropic (Claude) with live
telemetry context injection.

Provider selection:
  - Set LLM_PROVIDER=anthropic (or openai) explicitly, **or**
  - The handler auto-detects: if ANTHROPIC_API_KEY is set it uses Claude,
    otherwise it falls back to OpenAI.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# ─── API keys ─────────────────────────────────────────────────────────────────
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ─── Provider selection ───────────────────────────────────────────────────────
_explicit_provider = os.getenv("LLM_PROVIDER", "").lower()
if _explicit_provider in ("anthropic", "claude"):
    LLM_PROVIDER = "anthropic"
elif _explicit_provider in ("openai", "gpt"):
    LLM_PROVIDER = "openai"
else:
    # Auto-detect: prefer Anthropic when its key is present
    LLM_PROVIDER = "anthropic" if ANTHROPIC_API_KEY else "openai"

# ─── Model defaults ──────────────────────────────────────────────────────────
_DEFAULT_MODELS = {
    "openai":    "gpt-4o",
    "anthropic": "claude-3-5-haiku-20241022",
}
LLM_MODEL = os.getenv("LLM_MODEL", _DEFAULT_MODELS[LLM_PROVIDER])

SYSTEM_PROMPT = (
    "You are an F1 Race Engineer talking to the Driver via radio. "
    "You will receive the Driver's voice message along with live telemetry data in brackets. "
    "Analyze the telemetry data to answer the Driver's question accurately. "
    "Use F1 jargon (box box, copy, dirty air, tire deg, graining, blistering, undercut, overcut, ERS, DRS). "
    "Keep responses extremely short, punchy, and urgent — maximum 3 sentences. "
    "Never act like a polite AI assistant. "
    "If the telemetry shows a critical issue (engine blown, severe damage, >80% tyre wear) mention it immediately."
)


# ─── OpenAI backend ──────────────────────────────────────────────────────────
async def _openai_chat(user_content: str) -> str:
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


# ─── Anthropic backend ───────────────────────────────────────────────────────
async def _anthropic_chat(user_content: str) -> str:
    payload = {
        "model": LLM_MODEL,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.7,
        "max_tokens":  120,
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
    Send the driver's transcribed message + live telemetry snapshot to the LLM.
    Returns the engineer's text reply.
    """
    if LLM_PROVIDER == "anthropic" and not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set")
    if LLM_PROVIDER == "openai" and not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")

    user_content = (
        f"{driver_message}\n\n"
        f"[LIVE TELEMETRY]\n{telemetry_context}"
    )

    logger.info("LLM provider=%s  model=%s", LLM_PROVIDER, LLM_MODEL)

    if LLM_PROVIDER == "anthropic":
        return await _anthropic_chat(user_content)
    return await _openai_chat(user_content)
