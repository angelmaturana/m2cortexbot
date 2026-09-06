M2Cortex — Autonomous Multimodal "Second Brain" & Ledger Engine
===============================================================

Python 3.11+ Groq Llama 3.1 / 3.2 Turso libSQL Telegram API Serverless Edge

A production-ready, event-driven personal knowledge engine and financial ledger accessible via Telegram. **M2Cortex** converts unstructured multimodal inputs (voice notes, document photos, text streams) into structured knowledge and deterministic database operations.

Engineered with a **zero-hallucination financial core** by decoupling AI intent extraction from numerical computation, executing exact mathematical aggregations directly on an edge-distributed SQLite engine (Turso/libSQL).

🏛 System Architecture
----------------------

The system ingests user interactions via Telegram, normalizes multimodal media through specialized Groq LPU models, extracts structured JSON representations via an NLU engine, and executes deterministic SQL operations or Retrieval-Augmented Generation (RAG) pipelines.

## 🏛 System Architecture

```text
[Multimodal Ingestion (Telegram)] ---> (Groq Processing Engine: Whisper v3 / Llama 3.2 Vision)
                                          |
                                          v
                              [Llama 3.1 70B NLU Engine]
                                          |
                                          v
                                 {Intent Router}
                                   /    |    \
                                  /     |     \
    ("QUERY_BALANCE")            /      |      \            ("QUERY_HISTORY")
           |                    /       |       \                 |
           v                   /        |        \                v
[Deterministic SQL Aggregation]         |         \       [RAG Fetch & LLM Synthesis]
    SUM(monto) WHERE entity             |          \        Context Injection
           |                            |           \             |
           +-------------------> [Turso DB] <-------+-------------+
                                (libSQL / HTTPS)

✨ Key Architectural Highlights
------------------------------

*   **Multimodal Processing Pipeline:** Ingests voice messages using `Whisper Large v3`, images using `Llama 3.2 90B Vision`, and text streams via `Llama 3.1 70B Versatile` within sub-second inference windows hosted on Groq's LPU hardware acceleration.
*   **Deterministic Dual-Sign Ledger Core:** Eliminates AI arithmetic hallucination. Financial records strictly enforce signed float conversion (+ for assets/income, - for liabilities/expenses). Balance queries bypass the LLM entirely, delegating aggregations (`SUM(monto)`) directly to SQLite.
*   **Edge-Distributed Stateless Persistence:** Built on **Turso (libSQL)** using HTTPS transport protocols to bypass WebSocket handshake degradations commonly found across ephemeral container deployments.
*   **Retrieval-Augmented Generation (RAG):** Dynamically extracts structured historic notes by entity tags and feeds them into an LLM context window to answer natural language queries regarding personal history, ideas, or complex records.
*   **Timezone-Aware Temporal Anchor:** Programmatic injection of `Europe/Madrid` (`ZoneInfo`) context guarantees that relative time references (_"tomorrow at 9 AM"_, _"last Tuesday"_) map to exact ISO-8601 timestamps.
*   **Embedded Keep-Alive Server:** Runs a concurrent background worker thread alongside an `HTTPServer` instance to prevent platform hibernation on free-tier containerized environments (Render).

🛠 Tech Stack
-------------

Component

Technology

Role

**Language**

Python 3.11+

Asynchronous execution via `asyncio` and `threading`

**LLM / Vision / ASR**

Groq Cloud Platform

Llama 3.1 70B, Llama 3.2 90B Vision, Whisper Large v3

**Database**

Turso (libSQL)

SQLite-compatible edge database over HTTPS protocol

**Messaging API**

Telegram Bot API

`python-telegram-bot` (v20.x Async architecture)

**Hosting Engine**

Render / Docker

Containerized deployment with active keep-alive worker

🗄 Database Schema (`boveda_memorias`)
--------------------------------------

The primary data store is completely domain-agnostic, supporting unstructured knowledge, scheduled calendar actions, and financial transaction records within a unified schema.

    CREATE TABLE IF NOT EXISTS boveda_memorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha_registro TEXT,       -- ISO-8601 Timestamp (Europe/Madrid)
        intent TEXT,               -- RECORD | EVENT | QUERY_BALANCE | QUERY_HISTORY
        categoria TEXT,            -- FINANCE | KNOWLEDGE | EVENT | GENERAL
        entidades TEXT,            -- Normalized comma-separated tags
        monto REAL,                -- Signed float (+ for receivables, - for liabilities)
        fecha_accion TEXT,         -- Scheduled action timestamp (YYYY-MM-DD HH:MM:SS)
        resumen TEXT,              -- LLM-extracted concise summary
        json_crudo TEXT            -- Full JSON payload for audit logging
    );

🔀 Intent Classification Payload Format
---------------------------------------

Every incoming raw message is processed by `cortex_ai.py` and converted into a strictly typed JSON contract before reaching the data access layer:

    {
      "intent": "RECORD | EVENT | QUERY_BALANCE | QUERY_HISTORY",
      "categoria": "FINANCE | KNOWLEDGE | EVENT | GENERAL",
      "entidades_principales": ["entity_name"],
      "monto_calculado": -20.0,
      "fecha_accion": "2026-09-10 10:00:00",
      "resumen": "Concise summary of the record or financial transaction."
    }

🚀 Local Development & Setup
----------------------------

### 1\. Prerequisites

*   Python 3.11 or higher
*   Groq API Key
*   Telegram Bot Token
*   Turso Database URL & Auth Token

### 2\. Environment Setup

    TELEGRAM_TOKEN=123456789:AAA-YourTelegramBotToken
    GROQ_API_KEY=gsk_YourGroqApiKey
    
    GROQ_MODEL_ID_TEXT=llama-3.1-70b-versatile
    GROQ_MODEL_ID_AUDIO=whisper-large-v3
    GROQ_MODEL_ID_IMAGE=llama-3.2-90b-vision-preview
    
    TURSO_DATABASE_URL=https://your-database-name.turso.io
    TURSO_AUTH_TOKEN=ey...YourTursoToken
    
    PORT=8080
    RENDER_EXTERNAL_URL=https://your-app-name.onrender.com

> **Note:** Ensure `TURSO_DATABASE_URL` starts with `https://` instead of `libsql://` when running in serverless/containerized hosting environments to prevent WebSocket proxy issues.

📂 Repository Structure
-----------------------

    ├── main.py           # Telegram bot handler, async routing, and health server
    ├── cortex_ai.py       # Groq API client (Whisper, Vision, Structured NLU Output, RAG)
    ├── turso_db.py       # Turso libSQL client, schema initialization, and SQL queries
    ├── requirements.txt  # Python dependency manifest
    └── README.md         # Architecture and project documentation