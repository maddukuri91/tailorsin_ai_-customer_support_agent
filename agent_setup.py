"""
Builds the LangChain agent once at startup. Import `agent` from this module
wherever you need to invoke it.

NOTE: paste in all your existing @tool definitions (get_client_type,
get_company_faq, register_client, book_appointment, human_handover,
get_customer_addresses, add_customer_address, place_order, get_order_status,
modify_order, cancel_order, alteration_pickup, delete_customer_address,
drop_off_fabric) below the TOOLS placeholder, or import them from a
separate tools.py module.
"""
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_ollama import ChatOllama
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

from config import DATABASE_URL, OLLAMA_MODEL

# --- LLM ---
llm = ChatOllama(model=OLLAMA_MODEL)

# --- Tools ---
from tools import tools

SYSTEM_PROMPT = """You are a helpful customer support AI assistant for tailorsin.com.
Always show your reasoning before calling a tool and before answering customer queries.
Never guess an address, date, time, or order detail on the customer's behalf — always ask.
Never cancel or delete anything without explicit customer confirmation."""

# --- Persistent checkpointer (conversation history, durable across restarts) ---
_checkpointer_cm = PostgresSaver.from_conn_string(DATABASE_URL)
checkpointer = _checkpointer_cm.__enter__()  # keep open for app lifetime
checkpointer.setup()  # creates tables if they don't exist yet — safe to call every startup

# --- Persistent store (long-term customer facts, independent of any one thread) ---
_store_cm = PostgresStore.from_conn_string(DATABASE_URL)
store = _store_cm.__enter__()
store.setup()

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
            trigger=("tokens", 3000),
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