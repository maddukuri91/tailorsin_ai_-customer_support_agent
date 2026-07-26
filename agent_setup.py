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
from types import SimpleNamespace

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
try:
    from langchain.agents import create_agent as _create_agent
except Exception:  # pragma: no cover - compatibility fallback for older langchain installs
    _create_agent = None

try:
    from langchain.agents import initialize_agent, AgentType
except Exception:  # pragma: no cover - compatibility fallback
    initialize_agent = None
    AgentType = None

try:
    from langchain.agents.middleware import SummarizationMiddleware
except Exception:  # pragma: no cover - compatibility fallback
    class SummarizationMiddleware:  # type: ignore[override]
        def __init__(self, *args, **kwargs):
            pass

try:
    from langchain_ollama import ChatOllama
except Exception:  # pragma: no cover - optional dependency fallback
    class ChatOllama:  # type: ignore[override]
        def __init__(self, *args, **kwargs):
            pass

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
Keep replies short, clear, and action-focused. Use tools when needed, but avoid unnecessary back-and-forth.
Never guess an address, date, time, or order detail on the customer's behalf — always ask.
Never cancel or delete anything without explicit customer confirmation.

CRITICAL RULES FOR REGISTRATION:
- NEVER invent or guess a customer's name, email, or mobile number. You must ask the customer directly for each piece of information.
- If you don't have the customer's name, ask: "What is your name?"
- If you don't have the customer's email, ask: "What email address should I use?"
- If you don't have the customer's mobile number, ask: "What is your mobile number?"
- Do NOT use placeholder values like "John Doe", "johndoe@example.com", "test@test.com", or any other made-up data.
- If the customer has already shared their mobile number, use it automatically and do not ask again."""

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

class CompatAgent:
    """Small adapter that normalizes invoke() results for both old and new LangChain agent APIs."""

    def __init__(self, executor):
        self._executor = executor

    def invoke(self, input_data, config=None):
        result = self._executor.invoke(input_data, config=config)
        if isinstance(result, dict) and "messages" in result:
            return result
        if isinstance(result, dict) and "output" in result:
            return {"messages": [result["output"]]}
        return {"messages": [str(result)]}


class FallbackAgent:
    """Simple fallback used when a compatible LangChain agent factory is unavailable."""

    def invoke(self, input_data, config=None):
        messages = input_data.get("messages", []) if isinstance(input_data, dict) else []
        last_message = messages[-1] if messages else {}
        if isinstance(last_message, dict):
            user_text = last_message.get("content", "")
        else:
            user_text = getattr(last_message, "content", str(last_message))
        reply_text = f"Thanks for your message: {user_text}" if user_text else "How can I assist you today?"
        return {"messages": [SimpleNamespace(content=reply_text)]}


def _build_agent():
    if _create_agent is not None:
        return _create_agent(
            llm,
            tools,
            checkpointer=checkpointer,
            store=store,
            system_prompt=SYSTEM_PROMPT,
            middleware=[
                SummarizationMiddleware(
                    model=llm,
                    trigger=("tokens", 8000),
                    keep=("messages", 12),
                ),
            ],
        )

    if initialize_agent is not None and AgentType is not None:
        try:
            executor = initialize_agent(
                tools,
                llm,
                agent=AgentType.CHAT_ZERO_SHOT_REACT_DESCRIPTION,
                verbose=False,
            )
            return CompatAgent(executor)
        except Exception as exc:  # pragma: no cover - fallback for incompatible tool signatures
            logger.warning("LangChain initialize_agent fallback failed: %s", exc)

    return FallbackAgent()


agent = _build_agent()


def _normalize_mobile(mobile: str | None) -> str | None:
    if not mobile:
        return None
    digits = "".join(ch for ch in str(mobile) if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 12 and digits.startswith("91"):
        return digits
    if len(digits) > 10:
        return digits[-10:]
    return digits


def get_thread_id(platform: str, user_identifier: str, customer_mobile: str | None = None) -> str:
    """
    Build a stable thread_id from platform + user identifier.

    For Telegram conversations, prefer the customer's mobile number when it is
    known so memory and state remain consistent across turns and restarts.
    """
    if platform == "telegram":
        mobile = _normalize_mobile(customer_mobile)
        if mobile:
            return f"{platform}:{mobile}"
    return f"{platform}:{user_identifier}"