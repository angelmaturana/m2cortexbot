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
import turso_db

# --- SERVIDOR WEB DE SALUD Y KEEP ALIVE ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"M2Cortex Universal Vault is Running 24/7 on Turso.")
    def log_message(self, format, *args): return

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

def keep_alive_worker():
    time.sleep(30)
    target_url = os.getenv("RENDER_EXTERNAL_URL", "https://localhost")
    while True:
        try:
            requests.get(target_url, timeout=10)
        except Exception: 
            pass
        time.sleep(600)

async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    chat_id = update.message.chat_id
    user_message = update.message
    
    if user_message.video or user_message.video_note or user_message.document:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ *Formato no soportado.* Solo texto, fotos o audios.", parse_mode="Markdown")
        return

    await context.bot.send_message(chat_id=chat_id, text="🧠 Procesando entrada en el Segundo Cerebro...")

    text_input = user_message.text or user_message.caption
    image_bytes, audio_bytes, mime_type = None, None, None

    try:
        if user_message.photo:
            photo_file = await user_message.photo[-1].get_file()
            image_bytes = bytes(await photo_file.download_as_bytearray())
        elif user_message.voice or user_message.audio:
            file_obj = user_message.voice or user_message.audio
            voice_file = await file_obj.get_file()
            audio_bytes = bytes(await voice_file.download_as_bytearray())
            mime_type = file_obj.mime_type or "audio/ogg"

        if not text_input and not image_bytes and not audio_bytes: return

        # 1. Extracción de Datos Multimodal
        parsed_json = cortex_ai.process_and_classify(text_input, image_bytes, audio_bytes, mime_type)
        intent = parsed_json.get("intent", "RECORD")
        entidades = parsed_json.get("entidades_principales", [])
        entidad_clave = entidades[0] if entidades else None

        # 2. ENRUTAMIENTO MATEMÁTICO PURO (Deudas y Cálculos)
        if intent == "QUERY_BALANCE":
            if not entidad_clave:
                await context.bot.send_message(chat_id=chat_id, text="No detecto el nombre de la entidad/persona en la consulta.")
                return
            
            saldo = turso_db.get_balance(entidad_clave)
            if saldo < 0:
                msg = f"💸 **Deuda pendiente con {entidad_clave.title()}:** **{abs(saldo)} €** (A pagar)."
            elif saldo > 0:
                msg = f"💰 **Saldo a tu favor de {entidad_clave.title()}:** **{saldo} €** (Por cobrar)."
            else:
                msg = f"✅ La contabilidad con **{entidad_clave.title()}** está equilibrada (Balance: 0 €)."
                
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

        # 3. ENRUTAMIENTO DE BÚSQUEDA DE MEMORIA UNIVERSAL (RAG)
        elif intent == "QUERY_HISTORY":
            await context.bot.send_message(chat_id=chat_id, text="🔎 Accediendo a la Bóveda de Memorias...")
            historial = turso_db.query_history(entidad_clave)
            
            if not historial:
                await context.bot.send_message(chat_id=chat_id, text="No encuentro registros previos relacionados con tu búsqueda.")
                return
                
            records_text = "\n".join(historial)
            now_str = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d %H:%M")
            rag_prompt = f"Fecha actual: {now_str}\nConsulta del usuario: {text_input}\n\nMEMORIAS EXTRAÍDAS:\n{records_text}\n\nSintetiza una respuesta clara y directa basándote ÚNICAMENTE en la información extraída de la bóveda."
            
            final_answer = cortex_ai.generate_rag_answer(rag_prompt)
            await context.bot.send_message(chat_id=chat_id, text=f"💡 {final_answer}")

        # 4. GUARDADO DE CONOCIMIENTO / EVENTOS / TRANSACCIONES
        else:
            turso_db.save_record(parsed_json)
            
            icon = "📅" if intent == "EVENT" else "🧠"
            monto = parsed_json.get('monto_calculado', 0.0)
            
            reply_lines = [
                f"{icon} *Indexado en la Bóveda*",
                f"📝 *Dato:* {parsed_json.get('resumen')}"
            ]
            if entidades: reply_lines.append(f"🏷️ *Etiquetas:* {', '.join(entidades).title()}")
            if monto != 0.0: reply_lines.append(f"📊 *Registro Numérico:* {monto}")
            if parsed_json.get("fecha_accion"): reply_lines.append(f"⏰ *Programado:* {parsed_json.get('fecha_accion')}")

            await context.bot.send_message(chat_id=chat_id, text="\n".join(reply_lines), parse_mode="Markdown")

    except Exception as e:
        error_str = str(e)
        if "429" in error_str and "rate_limit_exceeded" in error_str.lower():
            model_val = re.search(r"model `([^`]+)`", error_str)
            model_name = model_val.group(1) if model_val else "Groq Engine"
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Límite cognitivo alcanzado en `{model_name}`. Espera unos segundos.", parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Error en el Segundo Cerebro: {error_str}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    turso_db.init_db()
    await update.message.reply_text("👋 Segundo Cerebro Inicializado. Listo para registrar conocimiento, finanzas y eventos.")

def main():
    turso_db.init_db()
    threading.Thread(target=start_health_server, daemon=True).start()
    threading.Thread(target=keep_alive_worker, daemon=True).start()

    logger.info("🚀 Iniciando M2Cortex Bóveda Universal...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()