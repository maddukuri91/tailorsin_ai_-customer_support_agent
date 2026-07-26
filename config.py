"""
Central configuration. Reads from environment variables so secrets never
live in source code. For local dev, copy .env.example to .env and fill in
real values — python-dotenv loads it automatically below.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # no-op in production if you set real env vars instead of a .env file

# The bot talks to WhatsApp/Telegram/CRM directly. Clear any inherited proxy
# settings here so local runs do not fail on environments missing SOCKS support.
for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_proxy_var, None)

# --- WhatsApp Cloud API ---
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "tailorsin_verify")  # used for webhook handshake

# --- Telegram Bot API ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# --- Database (for checkpointer + long-term store) ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/tailorsin_bot")

# --- Ollama (default local LLM) ---
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

# --- Groq (cloud LLM alternative) ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "0.3"))

# --- LLM provider selection ---
# Set to "ollama" or "groq" to pick which backend to use
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()

# --- LangSmith / LangChain tracing ---
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY", "")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "tailorsin_agentic_chatbot")

# --- Human handoff ---
# once a thread is handed off, the bot stops auto-responding until cleared
HANDOFF_TTL_SECONDS = 60 * 60 * 6  # 6 hours