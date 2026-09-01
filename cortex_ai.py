import logging
import os
import json
import base64
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
logger = logging.getLogger("M2Cortex-AI")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL_ID_TEXT = os.getenv("GROQ_MODEL_ID_TEXT", "llama-3.1-70b-versatile")
GROQ_MODEL_ID_AUDIO = os.getenv("GROQ_MODEL_ID_AUDIO", "whisper-large-v3")
GROQ_MODEL_ID_IMAGE = os.getenv("GROQ_MODEL_ID_IMAGE", "llama-3.2-90b-vision-preview")

client = Groq(api_key=GROQ_API_KEY)

def _transcribe_audio(audio_bytes, mime_type="audio/ogg"):
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
        logger.error(f"Error en Audio con {GROQ_MODEL_ID_AUDIO}: {e}")
        raise e

def _describe_image(image_bytes):
    b64_img = base64.b64encode(image_bytes).decode('utf-8')
    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL_ID_IMAGE,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe cantidades, nombres, objetos, textos y conceptos clave de esta imagen para indexarla en un segundo cerebro digital."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                    ]
                }
            ],
            temperature=0.1
        )
        return completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Error en Imagen con {GROQ_MODEL_ID_IMAGE}: {e}")
        raise e

def _get_classifier_prompt():
    tz_madrid = ZoneInfo("Europe/Madrid")
    now = datetime.now(tz_madrid)
    now_str = f"{now.strftime('%Y-%m-%d %H:%M:%S (%Z)')}"
    
    return f"""Eres M2Cortex, mi segundo cerebro personal omnisciente y bóveda de memoria universal.
Fecha actual: {now_str}.

TUS TAREAS:
1. INTENT: 
  - "RECORD": Guardar conocimiento, ideas, apuntes, gastos, resúmenes de libros o reflexiones personales.
  - "EVENT": Citas, reuniones, fechas límite o eventos programados.
  - "QUERY_BALANCE": El usuario pregunta EXPRESAMENTE por un cálculo numérico (cuánto debe alguien, balances matemáticos).
  - "QUERY_HISTORY": El usuario hace una pregunta sobre CUALQUIER recuerdo guardado (ideas pasadas, historial médico, notas, qué pasó un día concreto).
  
2. ENTIDADES: Extrae nombres de personas, proyectos o temas clave SIEMPRE en minúsculas (ej. "marcos", "proyecto alpha", "medicina").

3. REGLA LÓGICA DE SIGNOS ('monto_calculado'): Solo aplica si el recuerdo implica dinero o transacciones.
  - POSITIVO (+): Dinero a favor del usuario (alguien le debe dinero, cobros, ingresos). 
  - NEGATIVO (-): Dinero en contra del usuario (el usuario debe dinero, presta dinero, paga un gasto).
  - CERO (0.0): Si es una nota personal, idea, evento o conocimiento sin dinero implicado.

4. FECHAS: Formato YYYY-MM-DD HH:MM:SS. 'fecha_accion' es obligatoria si es un EVENT.

Devuelve ESTRICTAMENTE este JSON plano:
{{
  "intent": "RECORD",
  "categoria": "KNOWLEDGE",
  "entidades_principales": ["tema_o_persona"],
  "monto_calculado": 0.0,
  "fecha_accion": null,
  "resumen": "Redacción notarial directa con los hechos, ideas clave o datos relevantes extraídos."
}}"""

def process_and_classify(text_input=None, image_bytes=None, audio_bytes=None, mime_type=None):
    context_parts = []
    if text_input: context_parts.append(f"Texto: {text_input}")
    if audio_bytes: context_parts.append(f"Audio: {_transcribe_audio(audio_bytes, mime_type)}")
    if image_bytes: context_parts.append(f"Visual: {_describe_image(image_bytes)}")
        
    final_context = "\n".join(context_parts)
    
    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL_ID_TEXT,
            messages=[
                {"role": "system", "content": _get_classifier_prompt()},
                {"role": "user", "content": final_context}
            ],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        return json.loads(completion.choices[0].message.content)
    except Exception as e:
        logger.error(f"Error clasificando JSON: {e}")
        raise e

def generate_rag_answer(prompt_text):
    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL_ID_TEXT,
            messages=[{"role": "user", "content": prompt_text}],
            temperature=0.2
        )
        return completion.choices[0].message.content
    except Exception as e:
        return "Lo siento, hubo un error conectando con los recuerdos almacenados."