# 🏎 Scuderia AI – F1 Race Engineer

A voice-interactive web application that acts as your **live F1 Pit Wall Race Engineer** while you play **EA Sports F1 25**.

Talk to your AI engineer via a Push-to-Talk radio interface. The engineer answers your questions using **real-time telemetry data** pulled directly from the game's UDP stream.

---

## Features

| Feature | Details |
|---|---|
| **Live Telemetry** | UDP listener on port 20777 decodes F1 25 packets (tyre wear, temps, fuel, ERS, damage, position) |
| **Push-to-Talk Radio** | Hold the button (or Space bar) to speak |
| **Speech-to-Text** | OpenAI Whisper transcribes your voice |
| **Context-aware LLM** | GPT-4o receives your question + live telemetry snapshot |
| **Text-to-Speech** | OpenAI TTS generates the engineer's reply in a deep authoritative voice |
| **Radio Effect** | Web Audio API bandpass filter + distortion gives authentic two-way radio sound |
| **Radio Beeps** | Synthesised open/close beeps on every transmission |
| **Damage Panel** | Wing, floor, diffuser, gearbox, engine damage at a glance |

---

## Project Structure

```
Ai-enginer/
├── backend/
│   ├── __init__.py
│   ├── main.py            # FastAPI app – API routes & static file serving
│   ├── telemetry.py       # UDP listener + F1 25 packet decoder
│   ├── audio_handler.py   # Whisper STT + OpenAI TTS
│   └── llm_handler.py     # GPT-4o with telemetry context injection
├── frontend/
│   ├── index.html         # Main UI
│   ├── css/style.css      # F1-themed dark UI
│   └── js/app.js          # PTT logic, Web Audio API, telemetry polling
├── requirements.txt
├── .env.example
└── README.md
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- An [OpenAI API key](https://platform.openai.com/api-keys)
- EA Sports F1 25 with UDP telemetry enabled

### 2. Enable UDP Telemetry in F1 25

In-game: **Settings → Telemetry Settings**
- **UDP Telemetry**: On
- **UDP Broadcast Mode**: Off
- **UDP IP Address**: 127.0.0.1
- **UDP Port**: 20777
- **UDP Send Rate**: 60Hz (recommended)
- **UDP Format**: 2025

### 3. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
# Edit .env and set your OPENAI_API_KEY
```

### 5. Run the application

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Open your browser at **http://localhost:8000**

---

## Usage

1. Launch the app and open `http://localhost:8000`
2. Start an F1 25 race – the **TELEMETRY** dot turns green when data is received
3. **Hold** the PTT button (or **Space bar**) and speak your question
4. **Release** to send – your engineer replies instantly with radio crackle

### Example questions

- *"Are my tyres okay?"*
- *"How's my fuel?"*
- *"Should I box this lap?"*
- *"What's my gap to the leader?"*
- *"How's the car feeling – any damage?"*

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `POST /api/voice` | multipart/form-data | Accepts audio blob, returns MP3 reply |
| `GET /api/telemetry` | – | Returns current telemetry state as JSON |

---

## Tech Stack

- **Backend**: Python · FastAPI · httpx
- **AI**: OpenAI Whisper (STT) · GPT-4o (LLM) · OpenAI TTS
- **Telemetry**: Custom UDP decoder for F1 25 packet format
- **Frontend**: Vanilla JS · Web Audio API · CSS Grid

