"""
Builds the LangChain agent once at startup. Import `agent` from this module
wherever you need to invoke it.

LangSmith tracing is configured via environment variables at import time.
The LLM provider is selected via the LLM_PROVIDER config — currently supports
"ollama" (default) and "groq".

NOTE: paste in all your existing @tool definitions (get_client_type,
get_company_faq, register_client, book_appointment, human_handover,
get_customer_addresses, add_customer_address, place_order, get_order_status,
modify_order, cancel_order, alteration_pickup, delete_customer_address,
drop_off_fabric) below the TOOLS placeholder, or import them from a
separate tools.py module.
"""
import logging
import os

logger = logging.getLogger("tailorsin_bot")

# ---------------------------------------------------------------------------
# LangSmith / LangChain tracing — must be set BEFORE any langchain import
# ---------------------------------------------------------------------------
from config import (
    DATABASE_URL,
    OLLAMA_MODEL,
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_TEMPERATURE,
    LLM_PROVIDER,
    LANGSMITH_TRACING,
    LANGSMITH_ENDPOINT,
    LANGSMITH_API_KEY,
    LANGSMITH_PROJECT,
)

if LANGSMITH_TRACING and LANGSMITH_API_KEY:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_ENDPOINT"] = LANGSMITH_ENDPOINT
    os.environ["LANGSMITH_API_KEY"] = LANGSMITH_API_KEY
    os.environ["LANGSMITH_PROJECT"] = LANGSMITH_PROJECT
    # Legacy env vars (LangChain v1 compat)
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_ENDPOINT"] = LANGSMITH_ENDPOINT
    os.environ["LANGCHAIN_API_KEY"] = LANGSMITH_API_KEY
    os.environ["LANGCHAIN_PROJECT"] = LANGSMITH_PROJECT

# Now safe to import langchain modules — tracing is already configured
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_ollama import ChatOllama

try:
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.store.postgres import PostgresStore
except Exception as exc:  # pragma: no cover - import fallback for local/dev
    logger.warning("Postgres persistence unavailable, falling back to in-memory store: %s", exc)
    PostgresSaver = None
    PostgresStore = None

from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

# --- LLM ---
if LLM_PROVIDER == "groq" and GROQ_API_KEY:
    from langchain_groq import ChatGroq

    llm = ChatGroq(
        model=GROQ_MODEL,
        temperature=GROQ_TEMPERATURE,
        api_key=GROQ_API_KEY,
    )
else:
    # Default to Ollama
    llm = ChatOllama(model=OLLAMA_MODEL)

# --- Tools ---
from tools import tools

SYSTEM_PROMPT = """You are a helpful customer support AI assistant for tailorsin.com.
Always show your reasoning before calling a tool and before answering customer queries.
Never guess an address, date, time, or order detail on the customer's behalf — always ask.
Never cancel or delete anything without explicit customer confirmation.

CRITICAL RULES FOR REGISTRATION:
- NEVER invent or guess a customer's name, email, or mobile number. You must ask the customer directly for each piece of information.
- If you don't have the customer's name, ask: "What is your name?"
- If you don't have the customer's email, ask: "What email address should I use?"
- If you don't have the customer's mobile number, ask: "What is your mobile number?"
- Do NOT use placeholder values like "John Doe", "johndoe@example.com", "test@test.com", or any other made-up data."""

# --- Persistent checkpointer / store (prefer Postgres, fall back to memory) ---
if PostgresSaver is not None and PostgresStore is not None:
    try:
        _checkpointer_cm = PostgresSaver.from_conn_string(DATABASE_URL)
        checkpointer = _checkpointer_cm.__enter__()  # keep open for app lifetime
        checkpointer.setup()  # creates tables if they don't exist yet — safe to call every startup

        _store_cm = PostgresStore.from_conn_string(DATABASE_URL)
        store = _store_cm.__enter__()
        store.setup()
    except Exception as exc:  # pragma: no cover - runtime fallback
        logger.warning("Postgres connection failed, using in-memory state: %s", exc)
        checkpointer = MemorySaver()
        store = InMemoryStore()
else:
    checkpointer = MemorySaver()
    store = InMemoryStore()

agent = create_agent(
    llm,
    tools,
    checkpointer=checkpointer,
    store=store,
    system_prompt=SYSTEM_PROMPT,
    middleware=[
        # Once a thread's message history exceeds ~3000 tokens, older messages
        # get compressed into a running summary instead of being sent to the
        # LLM verbatim. Keeps long-running WhatsApp/Telegram threads (customers
        # messaging on and off over days/weeks) from silently growing the
        # context window and slowing down every response.
        SummarizationMiddleware(
            model=llm,
            trigger=("tokens", 8000),
            keep=("messages", 12),  # always keep the last 12 messages verbatim, summarize anything older
        ),
    ],
)


def get_thread_id(platform: str, user_identifier: str) -> str:
    """
    Build a stable thread_id from platform + user identifier (mobile number
    for WhatsApp, numeric user_id for Telegram). Prefixing by platform avoids
    any collision between the two ID spaces.
    """
    return f"{platform}:{user_identifier}"