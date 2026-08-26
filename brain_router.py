import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
import threading
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from notion_client import Client as NotionClient
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Configuración de Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("M2Cortex")

# 1. Cargar variables de entorno
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

# --- SISTEMA DE ROTACIÓN DE LLAVES GEMINI (SOLUCIÓN PÍCARA) ---
API_KEYS = []
for key_name in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"]:
    val = os.getenv(key_name)
    if val and val.strip():
        API_KEYS.append(val.strip())

if not API_KEYS:
    logger.error("❌ CRÍTICO: No se ha encontrado ninguna GEMINI_API_KEY en las variables de entorno.")

CURRENT_KEY_INDEX = 0

def get_gemini_client():
    """Devuelve el cliente de Gemini apuntando a la llave actual."""
    return genai.Client(api_key=API_KEYS[CURRENT_KEY_INDEX])

def rotate_key():
    """Rota a la siguiente API Key disponible."""
    global CURRENT_KEY_INDEX
    CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(API_KEYS)
    logger.warning(f"🔄 Rotando a la API Key de Gemini: Llave {CURRENT_KEY_INDEX + 1} de {len(API_KEYS)}")

def call_gemini_with_retry(contents, config=None):
    """Envuelve la petición a Gemini. Si salta límite 429, rota la llave y reintenta."""
    max_retries = len(API_KEYS)
    
    for attempt in range(max_retries):
        try:
            client = get_gemini_client()
            chat = client.chats.create(model="gemini-3.6-flash", config=config)
            response = chat.send_message(contents)
            return response
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "Quota" in error_str:
                logger.warning(f"⚠️ Límite agotado en la llave {CURRENT_KEY_INDEX + 1}. Intentando con la siguiente...")
                rotate_key()
            else:
                # Si el error es otro distinto (ej. fallo de conexión), lo dejamos saltar
                raise e
                
    # Si damos la vuelta completa y todas fallan
    raise Exception("🛑 Todas las llaves de Gemini están al límite. Dame unos 30 segundos de respiro antes de volver a preguntar.")
# --------------------------------------------------------------

# 2. Inicializar Cliente de Notion
notion = NotionClient(auth=NOTION_API_KEY)

# 3. Servidor HTTP de Keep-Alive para Render
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"M2Cortex Brain Router is Running 24/7.")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"🌐 Servidor Web de salud activo en puerto {port}")
    server.serve_forever()

# 4. Prompt Maestro de Clasificación
PROMPT_CLASSIFIER = """
Eres M2Cortex, un motor avanzado de enrutamiento de datos y memoria cognitiva.
Analiza la entrada proporcionada (texto, foto o audio) y clasifícala.

REGLA DE IDIOMA OBLIGATORIA:
Todo el contenido generado (título, resumen, etiquetas, tareas y contexto) DEBE estar redactado estrictamente en ESPAÑOL.

Devuelve la respuesta estructurada estrictamente con el siguiente esquema JSON:
{
  "intent": "RECORD" | "EVENT" | "QUERY",
  "master_category": "FINANCE" | "HEALTH" | "KNOWLEDGE" | "INVENTORY" | "DIARY" | "CRM",
  "general_metadata": {
    "title": "Título descriptivo en español (3 a 5 palabras)",
    "executive_summary": "Resumen ejecutivo en español (1 a 2 líneas)",
    "tags": ["Etiqueta1", "Etiqueta2"],
    "sentiment": "Positive" | "Neutral" | "Negative"
  },
  "specific_data": {
    "numeric_amount": 0.00,
    "detected_date": "YYYY-MM-DD or null",
    "hidden_tasks": ["Tarea detectada en español"]
  },
  "raw_context": "Transcripción completa o descripción visual detallada en español de lo observado"
}
"""

def save_to_notion(data: dict):
    """Guarda los datos estructurados en la tabla de Notion."""
    metadata = data.get("general_metadata", {})
    specific = data.get("specific_data", {})

    title = metadata.get("title", "Entrada sin título")
    category = data.get("master_category", "KNOWLEDGE")
    summary = metadata.get("executive_summary", "")
    tags = [t.replace("#", "").strip() for t in metadata.get("tags", []) if t.strip()]
    amount = float(specific.get("numeric_amount") or 0.0)

    date_val = specific.get("detected_date")
    if not date_val or date_val.lower() == "null":
        # Guarda fecha y hora exacta con zona horaria (formato ISO 8601)
        date_val = datetime.now().astimezone().isoformat()

    properties = {
        "Name": {"title": [{"text": {"content": title[:100]}}]},
        "Category": {"select": {"name": category}},
        "Date": {"date": {"start": date_val}},
        "Amount": {"number": amount},
        "Tags": {"multi_select": [{"name": tag[:100]} for tag in tags]},
        "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]}
    }

    children = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🔍 Detalle y Contexto"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": data.get("raw_context", "Sin contexto adicional.")}}]}
        }
    ]

    tasks = specific.get("hidden_tasks", [])
    if tasks:
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "✅ Tareas Detectadas"}}]}
        })
        for task in tasks:
            children.append({
                "object": "block",
                "type": "to_do",
                "to_do": {
                    "rich_text": [{"type": "text", "text": {"content": task}}],
                    "checked": False
                }
            })

    notion.pages.create(
        parent={"database_id": NOTION_DATABASE_ID},
        properties=properties,
        children=children
    )

def query_notion_db(category_filter=None, limit=10):
    """Busca los últimos registros en Notion comunicándose directamente con la API."""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    
    payload = {
        "page_size": limit
    }
    
    valid_categories = ["FINANCE", "HEALTH", "KNOWLEDGE", "INVENTORY", "DIARY", "CRM"]
    if category_filter in valid_categories and category_filter != "KNOWLEDGE":
        payload["filter"] = {
            "property": "Category",
            "select": {"equals": category_filter}
        }
        
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for page in data.get("results", []):
            props = page.get("properties", {})
            
            try:
                title = props.get("Name", {}).get("title", [{"plain_text": "Sin título"}])[0].get("plain_text", "Sin título")
            except Exception:
                title = "Sin título"
                
            try:
                summary = props.get("Summary", {}).get("rich_text", [{"plain_text": "Sin resumen"}])[0].get("plain_text", "Sin resumen")
            except Exception:
                summary = "Sin resumen"
                
            try:
                amount = props.get("Amount", {}).get("number", 0) or 0
            except Exception:
                amount = 0
            
            try:
                date_obj = props.get("Date", {}).get("date")
                date_str = date_obj.get("start") if date_obj else "Sin fecha"
            except Exception:
                date_str = "Sin fecha"
            
            record_text = f"- [{date_str}] {title}: {summary}"
            if amount > 0:
                record_text += f" (Importe: {amount}€)"
            results.append(record_text)
            
        return results
    except Exception as e:
        logger.error(f"Error crítico conectando directo a Notion: {e}")
        if hasattr(e, 'response') and e.response is not None:
            return [f"ERROR_NOTION_API: {e.response.text}"]
        return [f"ERROR_NOTION_API: {str(e)}"]

async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.message.chat_id
    user_message = update.message
    
    await context.bot.send_message(chat_id=chat_id, text="🧠 Analizando...")

    contents = []

    try:
        if user_message.text:
            contents.append(f"Input de usuario: {user_message.text}")

        elif user_message.photo:
            photo_file = await user_message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            contents.append(
                types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg")
            )
            if user_message.caption:
                contents.append(f"Contexto añadido: {user_message.caption}")

        elif user_message.voice or user_message.audio:
            file_obj = user_message.voice or user_message.audio
            voice_file = await file_obj.get_file()
            audio_bytes = await voice_file.download_as_bytearray()
            mime_type = file_obj.mime_type or ("audio/ogg" if user_message.voice else "audio/mpeg")
            contents.append(
                types.Part.from_bytes(data=bytes(audio_bytes), mime_type=mime_type)
            )
            if user_message.caption:
                contents.append(f"Contexto añadido: {user_message.caption}")

        # 1ª Llamada a Gemini (Clasificación con Rotación Segura)
        json_config = types.GenerateContentConfig(
            system_instruction=PROMPT_CLASSIFIER,
            response_mime_type="application/json",
            temperature=0.1,
        )
        response = call_gemini_with_retry(contents, config=json_config)
        parsed_json = json.loads(response.text.strip())
        
        intent = parsed_json.get("intent", "RECORD")

        if intent == "QUERY":
            await context.bot.send_message(chat_id=chat_id, text="🔎 Buscando en tus memorias de Notion...")
            
            category = parsed_json.get("master_category")
            recent_records = query_notion_db(category_filter=category)
            
            if recent_records and recent_records[0].startswith("ERROR_NOTION_API:"):
                error_msg = recent_records[0]
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Ups, error al leer Notion:\n`{error_msg}`", parse_mode="Markdown")
                return

            if not recent_records:
                await context.bot.send_message(chat_id=chat_id, text="No he encontrado recuerdos relacionados recientes.")
                return
                
            records_text = "\n".join(recent_records)
            rag_prompt = f"""
            El usuario te ha hecho una pregunta. Aquí tienes sus registros más recientes extraídos de Notion:
            
            {records_text}
            
            Responde a su pregunta de forma conversacional y útil basándote ÚNICAMENTE en estos datos. Sé directo y natural.
            """
            
            # 2ª Llamada a Gemini (Respuesta RAG con Rotación Segura)
            final_answer = call_gemini_with_retry([rag_prompt] + contents)
            await context.bot.send_message(chat_id=chat_id, text=f"💡 {final_answer.text}")

        else:
            await context.bot.send_message(chat_id=chat_id, text="💾 Guardando en tu base de datos...")
            save_to_notion(parsed_json)

            meta = parsed_json.get("general_metadata", {})
            spec = parsed_json.get("specific_data", {})

            reply_lines = [
                "✅ *Registrado en Notion*",
                f"📌 *Título:* {meta.get('title')}",
                f"🏷️ *Categoría:* `{parsed_json.get('master_category')}`",
                f"📝 *Resumen:* {meta.get('executive_summary')}"
            ]

            if spec.get("numeric_amount", 0) > 0:
                reply_lines.append(f"💰 *Importe:* {spec.get('numeric_amount')} €")

            await context.bot.send_message(
                chat_id=chat_id,
                text="\n".join(reply_lines),
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Error procesando mensaje: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error en M2Cortex: {str(e)}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 M2Cortex activo. Envíame datos para guardar o pregúntame por tus recuerdos.")

def main():
    web_thread = threading.Thread(target=start_health_server, daemon=True)
    web_thread.start()

    logger.info("🚀 Iniciando M2Cortex Engine con Rotación de Llaves...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_message))
    
    logger.info("🤖 M2Cortex escuchando en Telegram...")
    app.run_polling()

if __name__ == "__main__":
    main()