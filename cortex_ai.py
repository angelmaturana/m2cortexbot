import logging
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
logger = logging.getLogger("M2Cortex")

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
    """Envuelve la petición a Gemini. Si salta límite (429) o llave falsa (401), rota y reintenta."""
    max_retries = len(API_KEYS)
    
    for attempt in range(max_retries):
        try:
            client = get_gemini_client()
            chat = client.chats.create(model="gemini-3.6-flash", config=config)
            response = chat.send_message(contents)
            return response
        except Exception as e:
            error_str = str(e)
            # AQUÍ ESTÁ LA MEJORA: Ahora atrapamos el 401 (UNAUTHENTICATED) para que no rompa el bot
            if any(err in error_str for err in ["429", "RESOURCE_EXHAUSTED", "Quota", "401", "UNAUTHENTICATED"]):
                logger.warning(f"⚠️ Llave {CURRENT_KEY_INDEX + 1} agotada o INVÁLIDA (Error de API). Rotando a la siguiente...")
                rotate_key()
            else:
                raise e
                
    raise Exception("🛑 Todas las llaves de Gemini han fallado (están al límite o mal copiadas en Render).")

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