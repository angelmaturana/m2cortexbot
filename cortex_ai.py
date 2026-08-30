import logging
import os
import json
import base64
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
logger = logging.getLogger("M2Cortex")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Modelos configurables desde el .env con fallbacks por defecto
GROQ_MODEL_ID_TEXT = os.getenv("GROQ_MODEL_ID_TEXT", "groq/compound")
GROQ_MODEL_ID_AUDIO = os.getenv("GROQ_MODEL_ID_AUDIO", "whisper-large-v3")
GROQ_MODEL_ID_IMAGE = os.getenv("GROQ_MODEL_ID_IMAGE", "llama-3.2-90b-vision-preview")

if not GROQ_API_KEY:
    logger.error("❌ CRÍTICO: No se ha encontrado GROQ_API_KEY en las variables de entorno.")

client = Groq(api_key=GROQ_API_KEY)

def _transcribe_audio(audio_bytes, mime_type="audio/ogg"):
    """Usa el modelo de audio configurado para transcribir audios a texto."""
    ext = "ogg" if "ogg" in mime_type else "mp3"
    filename = f"audio.{ext}"
    try:
        completion = client.audio.transcriptions.create(
            file=(filename, audio_bytes),
            model=GROQ_MODEL_ID_AUDIO,
            response_format="text",
            language="es"
        )
        return completion
    except Exception as e:
        logger.error(f"Error transcribiendo audio con {GROQ_MODEL_ID_AUDIO}: {e}")
        raise e

def _describe_image(image_bytes):
    """Usa el modelo de visión configurado para extraer el contexto visual de la foto."""
    b64_img = base64.b64encode(image_bytes).decode('utf-8')
    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL_ID_IMAGE,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe los detalles clave de esta imagen (ej. cantidades, nombres, objetos, conceptos, recibos) de forma concisa y en español para poder registrarlos en una base de datos de inventario o finanzas."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                    ]
                }
            ],
            temperature=0.1
        )
        return completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Error analizando imagen con {GROQ_MODEL_ID_IMAGE}: {e}")
        raise e

def _get_classifier_prompt():
    tz_madrid = ZoneInfo("Europe/Madrid")
    now = datetime.now(tz_madrid)
    dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    dia_semana = dias[now.weekday()]
    now_str = f"{dia_semana}, {now.strftime('%Y-%m-%d %H:%M:%S (%Z)')}"
    today_iso = now.strftime("%Y-%m-%d")
    
    return f"""Eres M2Cortex, un motor avanzado de enrutamiento de datos, memoria cognitiva e indexación financiera.

CONTEXTO TEMPORAL EXACTO (HORA LOCAL ESPAÑOLA):
- Fecha y hora actual del sistema: {now_str}
- Fecha ISO de hoy: {today_iso}
- Día de la semana actual: {dia_semana}

REGLAS OBLIGATORIAS:
1. INTENT: 
   - Usa "EVENT" estrictamente si el mensaje describe una cita, reunión, viaje o evento programable en calendario.
   - Usa "QUERY" si el usuario hace una pregunta sobre su historial, base de datos o pide recordar algo.
   - Usa "RECORD" para guardar inventario, gastos, notas, deudas o información general.
2. Identifica nombres de personas, objetos clave o entidades en 'entities'.
3. TIPADO FINANCIERO ('transaction_type'): "Gasto", "Ingreso", "Me Deben", "Debo" o null.
4. FECHAS (Formato estricto YYYY-MM-DDTHH:MM:SS):
   - 'action_date': Fecha y hora de inicio de la alarma, evento o compromiso futuro. Asume 09:00 si no hay hora.
   - 'action_date_end': Fecha y hora de finalización del evento.
5. UBICACIÓN ('location'): Si se menciona un lugar para un evento/nota, extráelo aquí. Si no, null.
6. RESUMEN DETALLADO ('executive_summary'): Actúa como un notario riguroso. PROHIBIDO usar lenguaje "meta" (ej. "El usuario indica que..."). Escribe directamente los hechos. NO OMITAS NINGÚN DETALLE: incluye todos los nombres propios exactos, todos los apodos, todas las fechas exactas, parentescos, marcas, modelos y cifras mencionadas. El resumen debe contener el 100% del valor informativo del mensaje original sin dejarse nada atrás.
7. SI INTENT ES 'QUERY':
   - Si pregunta por gastos de hoy: pon 'category': "FINANCE", 'transaction_type': "Gasto", 'date_start': "{today_iso}".
   - Si pregunta por un objeto pon palabras clave en 'entities'.
   - IMPORTANTE: Si la pregunta NO incluye un marco temporal, asigna SIEMPRE null real a 'date_start' y 'date_end'.

Devuelve un JSON estrictamente con esta estructura:
{{
  "intent": "RECORD",
  "master_category": "INVENTORY",
  "query_filters": {{
    "entities": [],
    "category": null,
    "transaction_type": null,
    "status": null,
    "date_start": null,
    "date_end": null
  }},
  "general_metadata": {{
    "title": "Título descriptivo",
    "executive_summary": "Redacción directa, densa e hiperdetallada incluyendo el 100% de nombres, apodos, cifras y fechas exactas sin omitir nada.",
    "location": null,
    "tags": [],
    "entities": [],
    "sentiment": "Neutral"
  }},
  "specific_data": {{
    "numeric_amount": 0.00,
    "transaction_type": null,
    "detected_date": null,
    "action_date": null,
    "action_date_end": null,
    "status": null,
    "hidden_tasks": []
  }},
  "raw_context": "Transcripción completa"
}}"""

def process_and_classify(text_input=None, image_bytes=None, audio_bytes=None, mime_type=None):
    """Orquesta los agentes de Groq y devuelve el JSON estructurado."""
    context_parts = []
    
    if text_input:
        context_parts.append(f"Texto del usuario: {text_input}")
        
    if audio_bytes:
        transcription = _transcribe_audio(audio_bytes, mime_type)
        context_parts.append(f"Transcripción de nota de voz: {transcription}")
        
    if image_bytes:
        description = _describe_image(image_bytes)
        context_parts.append(f"Descripción visual de la imagen adjunta: {description}")
        
    final_context = "\n".join(context_parts)
    
    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL_ID_TEXT,
            messages=[
                {"role": "system", "content": _get_classifier_prompt()},
                {"role": "user", "content": final_context}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        parsed = json.loads(completion.choices[0].message.content)
        parsed["raw_context"] = final_context
        return parsed
    except Exception as e:
        logger.error(f"Error clasificando con modelo {GROQ_MODEL_ID_TEXT}: {e}")
        raise e

def generate_rag_answer(prompt_text):
    """Genera la respuesta final al usuario basándose en datos de Notion."""
    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL_ID_TEXT,
            messages=[{"role": "user", "content": prompt_text}],
            temperature=0.3
        )
        return completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Error generando respuesta RAG con {GROQ_MODEL_ID_TEXT}: {e}")
        return "Lo siento, hubo un error procesando la respuesta final."