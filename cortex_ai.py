import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
logger = logging.getLogger("M2Cortex")

# --- SISTEMA DE ROTACIÓN Y BALANCEO DE 6 LLAVES GEMINI ---
API_KEYS = []
for key_name in [
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
    "GEMINI_API_KEY_4",
    "GEMINI_API_KEY_5",
    "GEMINI_API_KEY_6"
]:
    val = os.getenv(key_name)
    if val and val.strip():
        API_KEYS.append(val.strip())

if not API_KEYS:
    logger.error("❌ CRÍTICO: No se ha encontrado ninguna GEMINI_API_KEY en las variables de entorno.")

CURRENT_KEY_INDEX = 0

def get_next_gemini_client():
    """Balancea la carga proactivamente entre todas las llaves disponibles."""
    global CURRENT_KEY_INDEX
    key = API_KEYS[CURRENT_KEY_INDEX]
    active_idx = CURRENT_KEY_INDEX + 1
    CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(API_KEYS)
    return genai.Client(api_key=key), active_idx

def call_gemini_with_retry(contents, config=None):
    """Llamadas a Gemini 3.6 Flash con Load Balancing circular y Backoff."""
    max_retries = len(API_KEYS) * 2
    
    for attempt in range(max_retries):
        client, key_num = get_next_gemini_client()
        try:
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
                sleep_time = 1.0 + (attempt * 0.5)
                logger.warning(
                    f"⚠️ Llave {key_num} saturada o no disponible. "
                    f"Pausa de seguridad de {sleep_time:.1f}s y probando siguiente..."
                )
                time.sleep(sleep_time)
            else:
                raise e
                
    raise Exception("🛑 Todas las llaves de Gemini están temporalmente saturadas. Espera unos segundos.")

def get_classifier_prompt():
    """Prompt multimodal con reglas estrictas de extracción de filtros y fechas."""
    tz_madrid = ZoneInfo("Europe/Madrid")
    now = datetime.now(tz_madrid)
    dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    dia_semana = dias[now.weekday()]
    now_str = f"{dia_semana}, {now.strftime('%Y-%m-%d %H:%M:%S (%Z)')}"
    today_iso = now.strftime("%Y-%m-%d")
    
    return f"""Eres M2Cortex, un motor avanzado de enrutamiento de datos, memoria cognitiva e indexación financiera.
Analiza la entrada proporcionada (texto, foto, audio o vídeo) y clasifícala.

CONTEXTO TEMPORAL EXACTO (HORA LOCAL ESPAÑOLA):
- Fecha y hora actual del sistema: {now_str}
- Fecha ISO de hoy: {today_iso}
- Día de la semana actual: {dia_semana}

REGLAS OBLIGATORIAS:
1. INTENT: 
   - Usa "EVENT" estrictamente si el mensaje describe una cita, reunión, viaje o evento programable en calendario.
   - Usa "QUERY" si el usuario hace una pregunta sobre su historial de Notion o pide recordar datos/matrículas/nombres.
   - Usa "RECORD" para guardar inventario, gastos, notas, deudas o información general.
2. Identifica nombres de personas, objetos clave (ej. "coche", "matrícula") o entidades en 'entities'.
3. TIPADO FINANCIERO ('transaction_type'): "Gasto", "Ingreso", "Me Deben", "Debo" o null.
4. FECHAS (Formato estricto YYYY-MM-DDTHH:MM:SS):
   - 'action_date': Fecha y hora de inicio de la alarma, evento o compromiso futuro. Asume 09:00 si no hay hora específica.
   - 'action_date_end': Fecha y hora de finalización del evento.
5. UBICACIÓN ('location'): Si se menciona un lugar para un evento/nota, extráelo aquí. Si no, null.
6. RESUMEN: Desglose completo con motivos, cifras y acuerdos.
7. SI INTENT ES 'QUERY':
   - Si pregunta por gastos de hoy: pon 'category': "FINANCE", 'transaction_type': "Gasto", 'date_start': "{today_iso}".
   - Si pregunta por un objeto (ej. "matrícula de coche") pon palabras clave en 'entities'.
   - IMPORTANTE: Si la pregunta NO incluye un marco temporal, asigna SIEMPRE null real a 'date_start' y 'date_end'. No uses cadenas de texto de relleno.

Devuelve la respuesta estructurada estrictamente con el siguiente esquema JSON:
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
    "executive_summary": "Resumen detallado",
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
