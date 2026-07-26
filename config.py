"""
Central configuration. Reads from environment variables so secrets never
live in source code. For local dev, copy .env.example to .env and fill in
real values — python-dotenv loads it automatically below.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # no-op in production if you set real env vars instead of a .env file

# --- WhatsApp Cloud API ---
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "tailorsin_verify")  # used for webhook handshake

# --- Telegram Bot API ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# --- Database (for checkpointer + long-term store) ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/tailorsin_bot")

# --- Ollama ---
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

# --- Human handoff ---
# once a thread is handed off, the bot stops auto-responding until cleared
HANDOFF_TTL_SECONDS = 60 * 60 * 6  # 6 hours