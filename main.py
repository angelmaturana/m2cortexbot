import asyncio
import json
import logging
import os
import threading
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
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
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # Límite estricto de Telegram (20 MB)

# Importar submódulos
from google.genai import types
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import cortex_ai
import notion_db

# 2. Servidor HTTP de Health Check para Render
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

# 3. Worker Autónomo Keep-Alive
def keep_alive_worker():
    time.sleep(30)
    target_url = os.getenv("RENDER_EXTERNAL_URL", "https://m2cortexbot.onrender.com")
    logger.info(f"💓 Keep-Alive Engine iniciado apuntando a: {target_url}")

    while True:
        try:
            res = requests.get(target_url, timeout=10)
            if res.status_code != 200:
                logger.warning(f"⚠️ Keep-Alive respondió código {res.status_code}")
        except Exception as e:
            pass
        time.sleep(600)

async def check_file_size_safe(file_obj, context, chat_id):
    """Verifica si el archivo sobrepasa el límite de 20MB de la API gratuita de Telegram."""
    file_size = getattr(file_obj, "file_size", 0)
    if file_size and file_size > MAX_FILE_SIZE_BYTES:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ *Archivo demasiado pesado*.\nEl archivo pesa {file_size / (1024*1024):.1f}MB y Telegram permite descargar un máximo de 20MB. Envíalo comprimido o más corto.",
            parse_mode="Markdown"
        )
        return False
    return True

async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.message.chat_id
    user_message = update.message
    
    await context.bot.send_message(chat_id=chat_id, text="🧠 Analizando...")
    contents = []

    try:
        # EXTRACTOR MULTIMEDIA CON PROTECCIÓN 20MB
        if user_message.text:
            contents.append(f"Input de usuario: {user_message.text}")

        elif user_message.photo:
            photo_file = await user_message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            contents.append(types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg"))
            if user_message.caption: contents.append(f"Contexto: {user_message.caption}")

        elif user_message.voice or user_message.audio:
            file_obj = user_message.voice or user_message.audio
            if not await check_file_size_safe(file_obj, context, chat_id): return
            voice_file = await file_obj.get_file()
            audio_bytes = await voice_file.download_as_bytearray()
            mime_type = file_obj.mime_type or ("audio/ogg" if user_message.voice else "audio/mpeg")
            contents.append(types.Part.from_bytes(data=bytes(audio_bytes), mime_type=mime_type))
            if user_message.caption: contents.append(f"Contexto: {user_message.caption}")

        elif user_message.video or user_message.video_note:
            video_obj = user_message.video or user_message.video_note
            if not await check_file_size_safe(video_obj, context, chat_id): return
            video_file = await video_obj.get_file()
            video_bytes = await video_file.download_as_bytearray()
            mime_type = getattr(video_obj, "mime_type", None) or "video/mp4"
            contents.append(types.Part.from_bytes(data=bytes(video_bytes), mime_type=mime_type))
            if user_message.caption: contents.append(f"Contexto: {user_message.caption}")

        elif user_message.document:
            doc_obj = user_message.document
            if not await check_file_size_safe(doc_obj, context, chat_id): return
            doc_file = await doc_obj.get_file()
            doc_bytes = await doc_file.download_as_bytearray()
            mime_type = doc_obj.mime_type or "application/octet-stream"
            contents.append(types.Part.from_bytes(data=bytes(doc_bytes), mime_type=mime_type))
            if user_message.caption: contents.append(f"Contexto: {user_message.caption}")

        if not contents:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ No he detectado contenido procesable.")
            return

        # 1ª LLAMADA (CLASIFICACIÓN)
        json_config = types.GenerateContentConfig(
            system_instruction=cortex_ai.get_classifier_prompt(),
            response_mime_type="application/json",
            temperature=0.1,
        )
        response = cortex_ai.call_gemini_with_retry(contents, config=json_config)
        parsed_json = json.loads(response.text.strip())
        
        intent = parsed_json.get("intent", "RECORD")

        if intent == "QUERY":
            await context.bot.send_message(chat_id=chat_id, text="🔎 Buscando en tus memorias de Notion...")
            
            category = parsed_json.get("master_category")
            query_filters = parsed_json.get("query_filters")
            
            recent_records = notion_db.query_notion_db(query_filters, category, 50)
            
            if recent_records and recent_records[0].startswith("ERROR_NOTION_API:"):
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Error en Notion:\n`{recent_records[0]}`", parse_mode="Markdown")
                return

            if not recent_records:
                await context.bot.send_message(chat_id=chat_id, text="No he encontrado recuerdos relacionados.")
                return
                
            records_text = "\n".join(recent_records)
            tz_madrid = ZoneInfo("Europe/Madrid")
            now_str = datetime.now(tz_madrid).strftime("%Y-%m-%d %H:%M:%S (%Z)")
            
            # 2ª LLAMADA RAG (AHORRO MASIVO DE TOKENS)
            # En lugar de reenviar el vídeo completo de 20MB, pasamos el resumen analizado en la primera fase
            user_raw_context = parsed_json.get("raw_context", "Consulta de base de datos.")
            
            rag_prompt = f"""
Fecha actual España: {now_str}
El usuario busca información basada en el siguiente análisis de su consulta (texto, audio o vídeo original ya transcrito):
"{user_raw_context}"

HISTORIAL EXTRAÍDO DE NOTION:
{records_text}

INSTRUCCIONES: Responde directamente y con precisión al usuario basándote en los datos de Notion.
"""
            # Enviamos solo texto a la 2ª fase, ahorrando un 50% del coste de tokens TPM.
            final_answer = cortex_ai.call_gemini_with_retry([rag_prompt])
            await context.bot.send_message(chat_id=chat_id, text=f"💡 {final_answer.text}")

        else:
            await context.bot.send_message(chat_id=chat_id, text="💾 Guardando en tu base de datos...")
            notion_db.save_to_notion(parsed_json)

            meta = parsed_json.get("general_metadata", {})
            spec = parsed_json.get("specific_data", {})

            # FORMATO DE RESPUESTA TELEGRAM
            intent_icon = "📅" if intent == "EVENT" else "✅"
            reply_lines = [
                f"{intent_icon} *Registrado en Notion*",
                f"📌 *Título:* {meta.get('title')}",
                f"🏷️ *Categoría:* `{parsed_json.get('master_category')}`",
                f"📝 *Resumen:* {meta.get('executive_summary')}"
            ]

            loc = meta.get("location")
            if loc and str(loc).lower() != "null":
                reply_lines.append(f"📍 *Ubicación:* {loc}")

            amount_val = float(spec.get("numeric_amount") or 0.0)
            if amount_val > 0: reply_lines.append(f"💰 *Importe:* {amount_val} €")

            if spec.get("transaction_type"): reply_lines.append(f"💳 *Tipo Transacción:* `{spec.get('transaction_type')}`")

            entities = meta.get("entities")
            if entities and isinstance(entities, list) and len(entities) > 0:
                reply_lines.append(f"👤 *Entidades:* {', '.join(entities)}")

            if spec.get("status"): reply_lines.append(f"📌 *Estado:* `{spec.get('status')}`")

            action_start = spec.get("action_date")
            action_end = spec.get("action_date_end")
            if action_start and str(action_start).lower() != "null":
                time_str = str(action_start)
                if action_end and str(action_end).lower() != "null":
                    time_str += f" a {str(action_end)}"
                reply_lines.append(f"⏰ *Fecha Acción:* `{time_str}`")

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

    ping_thread = threading.Thread(target=keep_alive_worker, daemon=True)
    ping_thread.start()

    logger.info("🚀 Iniciando M2Cortex Engine Modularizado...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_message))
    
    logger.info("🤖 M2Cortex escuchando en Telegram...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()