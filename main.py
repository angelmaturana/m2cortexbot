import asyncio
import logging
import os
import threading
import time
import requests
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("M2Cortex")

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import cortex_ai
import notion_db

# --- SERVIDOR WEB DE SALUD Y KEEP ALIVE (Para evitar suspensión en Render) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"M2Cortex Brain Router is Running 24/7.")
    def log_message(self, format, *args): return

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"🌐 Servidor Web activo en puerto {port}")
    server.serve_forever()

def keep_alive_worker():
    time.sleep(30)
    target_url = os.getenv("RENDER_EXTERNAL_URL", "https://m2cortexbot.onrender.com")
    while True:
        try:
            requests.get(target_url, timeout=10)
        except Exception as e: 
            logger.debug(f"Ping de mantenimiento ignorado: {e}")
        time.sleep(600)

async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return

    chat_id = update.message.chat_id
    user_message = update.message
    
    # Rechazo cortés de Vídeos y Documentos para blindar la memoria RAM
    if user_message.video or user_message.video_note or user_message.document:
        await context.bot.send_message(
            chat_id=chat_id, 
            text="⚠️ *Formato no soportado.* El nuevo cerebro procesa texto, fotos y audios/notas de voz de forma ultra-rápida. No envíes vídeos ni documentos.",
            parse_mode="Markdown"
        )
        return

    await context.bot.send_message(chat_id=chat_id, text="🧠 Analizando...")

    text_input = user_message.text or user_message.caption
    image_bytes = None
    audio_bytes = None
    mime_type = None

    try:
        if user_message.photo:
            photo_file = await user_message.photo[-1].get_file()
            image_bytes = bytes(await photo_file.download_as_bytearray())

        elif user_message.voice or user_message.audio:
            file_obj = user_message.voice or user_message.audio
            voice_file = await file_obj.get_file()
            audio_bytes = bytes(await voice_file.download_as_bytearray())
            mime_type = file_obj.mime_type or "audio/ogg"

        if not text_input and not image_bytes and not audio_bytes:
            return

        # 1. Procesamiento Central
        parsed_json = cortex_ai.process_and_classify(text_input, image_bytes, audio_bytes, mime_type)
        intent = parsed_json.get("intent", "RECORD")

        if intent == "QUERY":
            await context.bot.send_message(chat_id=chat_id, text="🔎 Buscando en tus memorias de Notion...")
            # Búsqueda ampliada a 100 recuerdos en lugar de 50
            recent_records = notion_db.query_notion_db(parsed_json.get("query_filters"), parsed_json.get("master_category"), 100)
            
            if recent_records and recent_records[0].startswith("ERROR_NOTION_API:"):
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Error en Notion:\n`{recent_records[0]}`", parse_mode="Markdown")
                return

            if not recent_records:
                await context.bot.send_message(chat_id=chat_id, text="No he encontrado recuerdos relacionados.")
                return
                
            records_text = "\n".join(recent_records)
            tz_madrid = ZoneInfo("Europe/Madrid")
            now_str = datetime.now(tz_madrid).strftime("%Y-%m-%d %H:%M:%S (%Z)")
            user_raw_context = parsed_json.get("raw_context", "Consulta.")
            
            rag_prompt = f"""Fecha actual España: {now_str}\n\nPregunta / Contexto original:\n"{user_raw_context}"\n\nHISTORIAL DE NOTION:\n{records_text}\n\nResponde directamente basándote SOLO en los datos de Notion."""
            
            final_answer = cortex_ai.generate_rag_answer(rag_prompt)
            await context.bot.send_message(chat_id=chat_id, text=f"💡 {final_answer}")

        else:
            await context.bot.send_message(chat_id=chat_id, text="💾 Guardando en tu base de datos...")
            notion_db.save_to_notion(parsed_json)

            meta = parsed_json.get("general_metadata", {})
            spec = parsed_json.get("specific_data", {})

            intent_icon = "📅" if intent == "EVENT" else "✅"
            reply_lines = [
                f"{intent_icon} *Registrado en Notion*",
                f"📌 *Título:* {meta.get('title')}",
                f"🏷️ *Categoría:* `{parsed_json.get('master_category')}`",
                f"📝 *Resumen:* {meta.get('executive_summary')}"
            ]

            loc = meta.get("location")
            if loc and str(loc).lower() != "null": reply_lines.append(f"📍 *Ubicación:* {loc}")
            
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
                if action_end and str(action_end).lower() != "null": time_str += f" a {str(action_end)}"
                reply_lines.append(f"⏰ *Fecha Acción:* `{time_str}`")

            await context.bot.send_message(chat_id=chat_id, text="\n".join(reply_lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error procesando mensaje: {e}", exc_info=True)
        error_str = str(e)
        
        # Interceptor de límite de cuota (Error 429)
        if "429" in error_str and "rate_limit_exceeded" in error_str.lower():
            limit_match = re.search(r"Limit (\d+)", error_str)
            used_match = re.search(r"Used (\d+)", error_str)
            time_match = re.search(r"try again in ([0-9a-zA-Z\.]+)", error_str)
            
            limit_val = f"{int(limit_match.group(1)):,}".replace(",", ".") if limit_match else "N/A"
            used_val = f"{int(used_match.group(1)):,}".replace(",", ".") if used_match else "N/A"
            time_val = time_match.group(1) if time_match else "unos minutos"
            
            # Formateo dinámico de tiempo: limpia milisegundos y traduce sin importar magnitud
            time_val = re.sub(r"\.\d+s", " seg", time_val)
            time_val = time_val.replace("h", " horas, ").replace("m", " min y ")
                
            error_msg = (
                "⚠️ *Límite de Inteligencia Alcanzado*\n"
                "El modelo principal está descansando para evitar la saturación de los servidores.\n\n"
                f"📊 *Consumo diario:* {used_val} / {limit_val} tokens\n"
                f"⏳ *Tiempo de espera:* {time_val}\n\n"
                "_Consejo: Inténtalo de nuevo en un rato, o cambia el modelo en tu archivo .env por uno más ligero._"
            )
            await context.bot.send_message(chat_id=chat_id, text=error_msg, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Error en M2Cortex: {error_str}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 M2Cortex activo (Groq Engine). Envíame texto, audios o fotos.")

def main():
    threading.Thread(target=start_health_server, daemon=True).start()
    threading.Thread(target=keep_alive_worker, daemon=True).start()

    logger.info("🚀 Iniciando M2Cortex Groq Engine...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()