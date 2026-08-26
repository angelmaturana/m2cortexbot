import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
import threading
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

# 2. Inicializar Clientes de API
gemini_client = genai.Client(api_key=GEMINI_API_KEY)
notion = NotionClient(auth=NOTION_API_KEY)

# 3. Servidor HTTP de Keep-Alive para Render
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"M2Cortex Brain Router is Running 24/7.")

    def log_message(self, format, *args):
        return  # Silenciar logs ruidosos del health check

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

    # Validación de fecha
    date_val = specific.get("detected_date")
    if not date_val or date_val.lower() == "null":
        date_val = datetime.now().strftime("%Y-%m-%d")

    # Propiedades de la base de datos
    properties = {
        "Name": {"title": [{"text": {"content": title[:100]}}]},
        "Category": {"select": {"name": category}},
        "Date": {"date": {"start": date_val}},
        "Amount": {"number": amount},
        "Tags": {"multi_select": [{"name": tag[:100]} for tag in tags]},
        "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]}
    }

    # Bloques de contenido interno
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

    # Añadir tareas pendientes como checkboxes si existen
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

    # Creación de la página
    notion.pages.create(
        parent={"database_id": NOTION_DATABASE_ID},
        properties=properties,
        children=children
    )

async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.message.chat_id
    user_message = update.message
    await context.bot.send_message(chat_id=chat_id, text="🧠 M2Cortex procesando y sincronizando con Notion...")

    contents = []

    try:
        # 1. Extracción de Payload (Texto / Imagen / Audio)
        if user_message.text:
            contents.append(f"Input de usuario: {user_message.text}")

        elif user_message.photo:
            photo_file = await user_message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            contents.append(
                types.Part.from_bytes(
                    data=bytes(photo_bytes),
                    mime_type="image/jpeg"
                )
            )
            if user_message.caption:
                contents.append(f"Contexto añadido: {user_message.caption}")

        elif user_message.voice or user_message.audio:
            file_obj = user_message.voice or user_message.audio
            voice_file = await file_obj.get_file()
            audio_bytes = await voice_file.download_as_bytearray()
            mime_type = file_obj.mime_type or ("audio/ogg" if user_message.voice else "audio/mpeg")
            contents.append(
                types.Part.from_bytes(
                    data=bytes(audio_bytes),
                    mime_type=mime_type
                )
            )
            if user_message.caption:
                contents.append(f"Contexto añadido: {user_message.caption}")

        # 2. Ejecución con el nuevo SDK de Gemini usando la interfaz de Chat
        chat = gemini_client.chats.create(
            model="gemini-3.6-flash",
            config=types.GenerateContentConfig(
                system_instruction=PROMPT_CLASSIFIER,
                response_mime_type="application/json",
                temperature=0.1,
            )
        )
        
        response = chat.send_message(contents)

        parsed_json = json.loads(response.text.strip())

        # 3. Persistencia en Notion
        save_to_notion(parsed_json)

        # 4. Respuesta estructurada al usuario
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
    await update.message.reply_text("👋 M2Cortex activo. Envíame fotos, notas de voz o textos para estructurarlos en Notion.")

def main():
    # Iniciar servidor web en segundo plano para Render
    web_thread = threading.Thread(target=start_health_server, daemon=True)
    web_thread.start()

    # Iniciar Bot de Telegram
    logger.info("🚀 Iniciando M2Cortex Engine...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_message))
    
    logger.info("🤖 M2Cortex escuchando en Telegram...")
    app.run_polling()

if __name__ == "__main__":
    main()