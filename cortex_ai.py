import logging
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
logger = logging.getLogger("M2Cortex")

# --- SISTEMA DE ROTACIÓN DE LLAVES GEMINI ---
API_KEYS = []
for key_name in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"]:
    val = os.getenv(key_name)
    if val and val.strip():
        API_KEYS.append(val.strip())

if not API_KEYS:
    logger.error("❌ CRÍTICO: No se ha encontrado ninguna GEMINI_API_KEY en las variables de entorno.")

CURRENT_KEY_INDEX = 0

def get_gemini_client():
    """Devuelve el cliente de Gemini apuntando a la llave activa."""
    return genai.Client(api_key=API_KEYS[CURRENT_KEY_INDEX])

def rotate_key():
    """Rota cíclicamente a la siguiente API Key disponible."""
    global CURRENT_KEY_INDEX
    CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(API_KEYS)
    logger.warning(f"🔄 Rotando a la API Key de Gemini: Llave {CURRENT_KEY_INDEX + 1} de {len(API_KEYS)}")

def call_gemini_with_retry(contents, config=None):
    """Envuelve la llamada a Gemini. Si salta límite o error de clave, rota y reintenta."""
    max_retries = len(API_KEYS)
    
    for attempt in range(max_retries):
        try:
            client = get_gemini_client()
            chat = client.chats.create(model="gemini-3.6-flash", config=config)
            response = chat.send_message(contents)
            return response
        except Exception as e:
            error_str = str(e)
            error_triggers = [
                "429", "RESOURCE_EXHAUSTED", "Quota",
                "401", "UNAUTHENTICATED",
                "400", "INVALID_ARGUMENT", "API_KEY_INVALID"
            ]
            
            if any(err in error_str for err in error_triggers):
                logger.warning(f"⚠️ Llave {CURRENT_KEY_INDEX + 1} falló o alcanzó límite. Rotando...")
                rotate_key()
            else:
                raise e
                
    raise Exception("🛑 Todas las llaves de Gemini han fallado (están al límite o son inválidas).")

# --- PROMPT MAESTRO DE CLASIFICACIÓN (FASE 3.5) ---
PROMPT_CLASSIFIER = """
Eres M2Cortex, un motor avanzado de enrutamiento de datos y memoria cognitiva.
Analiza la entrada proporcionada (texto, foto o audio) y clasifícala.

REGLAS OBLIGATORIAS:
1. Todo el contenido generado DEBE estar redactado estrictamente en ESPAÑOL.
2. Identifica nombres de personas, contactos, clientes o entidades y colócalos en 'entities'.
3. Si el mensaje describe una deuda activa, compromiso, recordatorio o algo no terminado, asigna 'status': "Pendiente". Si describe un pago liquidado o tarea finalizada, asigna 'status': "Completado". En cualquier otro caso sin estado claro, asigna null.
4. Si el mensaje especifica una fecha u hora futura para una acción/alarma (ej. "mañana a las 10", "el viernes"), calcúlala y asígnala en formato ISO 8601 en 'action_date'.

Devuelve la respuesta estructurada estrictamente con el siguiente esquema JSON:
{
  "intent": "RECORD" | "EVENT" | "QUERY",
  "master_category": "FINANCE" | "HEALTH" | "KNOWLEDGE" | "INVENTORY" | "DIARY" | "CRM",
  "general_metadata": {
    "title": "Título descriptivo en español (3 a 5 palabras)",
    "executive_summary": "Resumen ejecutivo en español (1 a 2 líneas)",
    "tags": ["Etiqueta1", "Etiqueta2"],
    "entities": ["PersonaOEntidad1", "PersonaOEntidad2"],
    "sentiment": "Positive" | "Neutral" | "Negative"
  },
  "specific_data": {
    "numeric_amount": 0.00,
    "detected_date": "YYYY-MM-DD or null",
    "action_date": "YYYY-MM-DDTHH:MM:SS or null",
    "status": "Pendiente" | "Completado" | "Cancelado" | null,
    "hidden_tasks": ["Tarea detectada en español"]
  },
  "raw_context": "Transcripción completa o descripción visual detallada en español de lo observado"
}
"""