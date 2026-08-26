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

# 3. Worker Autónomo Keep-Alive (Evita suspensión en Render)
def keep_alive_worker():
    """Envía un ping periódico cada 10 minutos a la URL pública de Render."""
    time.sleep(30)
    target_url = os.getenv("RENDER_EXTERNAL_URL", "https://m2cortexbot.onrender.com")
    logger.info(f"💓 Keep-Alive Engine iniciado apuntando a: {target_url}")

    while True:
        try:
            res = requests.get(target_url, timeout=10)
            if res.status_code == 200:
                logger.info("💓 Keep-Alive Ping exitoso (200 OK) -> Servidor activo 24/7")
            else:
                logger.warning(f"⚠️ Keep-Alive respondió código {res.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Fluctuación temporal en Keep-Alive Ping: {e}")

        time.sleep(600)

async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.message.chat_id
    user_message = update.message
    
    await context.bot.send_message(chat_id=chat_id, text="🧠 Analizando...")

    contents = []

    try:
        # 1. Texto plano
        if user_message.text:
            contents.append(f"Input de usuario: {user_message.text}")

        # 2. Imágenes / Fotos
        elif user_message.photo:
            photo_file = await user_message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            contents.append(
                types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg")
            )
            if user_message.caption:
                contents.append(f"Contexto añadido: {user_message.caption}")

        # 3. Audios y Notas de Voz
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

        # 4. Vídeos y Vídeos circulares (video_note)
        elif user_message.video or user_message.video_note:
            video_obj = user_message.video or user_message.video_note
            video_file = await video_obj.get_file()
            video_bytes = await video_file.download_as_bytearray()
            mime_type = getattr(video_obj, "mime_type", None) or "video/mp4"
            contents.append(
                types.Part.from_bytes(data=bytes(video_bytes), mime_type=mime_type)
            )
            if user_message.caption:
                contents.append(f"Contexto añadido: {user_message.caption}")

        # 5. Archivos / Documentos adjuntos
        elif user_message.document:
            doc_obj = user_message.document
            doc_file = await doc_obj.get_file()
            doc_bytes = await doc_file.download_as_bytearray()
            mime_type = doc_obj.mime_type or "application/octet-stream"
            contents.append(
                types.Part.from_bytes(data=bytes(doc_bytes), mime_type=mime_type)
            )
            if user_message.caption:
                contents.append(f"Contexto añadido: {user_message.caption}")

        # Guardia defensiva: evitar llamar a Gemini si el mensaje no trajo contenido soportado
        if not contents:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ No he detectado contenido procesable (texto, imagen, audio o vídeo)."
            )
            return

        # 1ª Llamada a Gemini con prompt enriquecido
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
            
            recent_records = notion_db.query_notion_db(
                query_filters=query_filters,
                category_filter=category,
                max_records=50
            )
            
            if recent_records and recent_records[0].startswith("ERROR_NOTION_API:"):
                error_msg = recent_records[0]
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Ups, error al leer Notion:\n`{error_msg}`", parse_mode="Markdown")
                return

            if not recent_records:
                await context.bot.send_message(chat_id=chat_id, text="No he encontrado recuerdos relacionados.")
                return
                
            records_text = "\n".join(recent_records)
            tz_madrid = ZoneInfo("Europe/Madrid")
            now_str = datetime.now(tz_madrid).strftime("%Y-%m-%d %H:%M:%S (%Z)")
            
            rag_prompt = f"""
Fecha y hora actual en España: {now_str}
El usuario te ha hecho una pregunta. Aquí tienes el historial cronológico extraído de Notion:

{records_text}

INSTRUCCIONES DE RESPUESTA:
- Los registros están ordenados cronológicamente (los eventos más recientes aparecen al final).
- Si hay varios eventos sobre un mismo asunto o persona (ej. deudas, cobros o citas), los registros más recientes actualizan y prevalecen sobre los anteriores.
- Distingue claramente entre 'Gasto', 'Ingreso', 'Me Deben' (saldo a cobrar) y 'Debo' (saldo por pagar).
- Responde de forma natural, directa, concisa y útil basándote ÚNICAMENTE en estos datos.
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

            amount_val = float(spec.get("numeric_amount") or 0.0)
            if amount_val > 0:
                reply_lines.append(f"💰 *Importe:* {amount_val} €")

            if spec.get("transaction_type"):
                reply_lines.append(f"💳 *Tipo Transacción:* `{spec.get('transaction_type')}`")

            entities = meta.get("entities")
            if entities and isinstance(entities, list) and len(entities) > 0:
                reply_lines.append(f"👤 *Entidades:* {', '.join(entities)}")

            if spec.get("status"):
                reply_lines.append(f"📌 *Estado:* `{spec.get('status')}`")

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
    # 1. Servidor Web de salud para Render
    web_thread = threading.Thread(target=start_health_server, daemon=True)
    web_thread.start()

    # 2. Worker Keep-Alive 24/7
    ping_thread = threading.Thread(target=keep_alive_worker, daemon=True)
    ping_thread.start()

    # 3. Arranque del bot de Telegram
    logger.info("🚀 Iniciando M2Cortex Engine Modularizado...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_message))
    
    logger.info("🤖 M2Cortex escuchando en Telegram...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()