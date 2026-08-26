import asyncio
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

# Configuración de Logging GLOBAL
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("M2Cortex")

# 1. Cargar variables de entorno principales
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Importar los submódulos (IA y Base de Datos)
from google.genai import types
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import cortex_ai
import notion_db

# 2. Servidor HTTP de Keep-Alive para Render
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

async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.message.chat_id
    user_message = update.message
    
    await context.bot.send_message(chat_id=chat_id, text="🧠 Analizando...")

    contents = []

    try:
        # Extraer contenido multimodal
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

        # 1ª Llamada a Gemini usando el módulo cortex_ai
        json_config = types.GenerateContentConfig(
            system_instruction=cortex_ai.PROMPT_CLASSIFIER,
            response_mime_type="application/json",
            temperature=0.1,
        )
        response = cortex_ai.call_gemini_with_retry(contents, config=json_config)
        parsed_json = json.loads(response.text.strip())
        
        intent = parsed_json.get("intent", "RECORD")

        if intent == "QUERY":
            await context.bot.send_message(chat_id=chat_id, text="🔎 Buscando en tus memorias de Notion...")
            
            category = parsed_json.get("master_category")
            recent_records = notion_db.query_notion_db(category_filter=category)
            
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
            
            # 2ª Llamada a Gemini para RAG
            final_answer = cortex_ai.call_gemini_with_retry([rag_prompt] + contents)
            await context.bot.send_message(chat_id=chat_id, text=f"💡 {final_answer.text}")

        else:
            await context.bot.send_message(chat_id=chat_id, text="💾 Guardando en tu base de datos...")
            notion_db.save_to_notion(parsed_json)

            meta = parsed_json.get("general_metadata", {})
            spec = parsed_json.get("specific_data", {})

            reply_lines = [
                "✅ *Registrado en Notion*",
                f"📌 *Título:* {meta.get('title')}",
                f"🏷️ *Categoría:* `{parsed_json.get('master_category')}`",
                f"📝 *Resumen:* {meta.get('executive_summary')}"
            ]

            # Conversión segura del importe numérico contra valores None o null
            amount_val = float(spec.get("numeric_amount") or 0.0)
            if amount_val > 0:
                reply_lines.append(f"💰 *Importe:* {amount_val} €")

            # Validación segura de entidades (lista no vacía)
            entities = meta.get("entities")
            if entities and isinstance(entities, list) and len(entities) > 0:
                reply_lines.append(f"👤 *Entidades:* {', '.join(entities)}")

            # Validación de estado
            if spec.get("status"):
                reply_lines.append(f"📌 *Estado:* `{spec.get('status')}`")

            # Validación de fecha de acción
            action_date = spec.get("action_date")
            if action_date and str(action_date).lower() != "null":
                reply_lines.append(f"⏰ *Fecha Acción:* `{action_date}`")

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

    logger.info("🚀 Iniciando M2Cortex Engine Modularizado...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_message))
    
    logger.info("🤖 M2Cortex escuchando en Telegram...")
    app.run_polling()

if __name__ == "__main__":
    main()