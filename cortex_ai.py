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
    """Prompt multimodal con reglas para Eventos, Fechas de fin y Ubicación."""
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
   - Usa "QUERY" si el usuario hace una pregunta sobre su historial de Notion o pide calcular datos.
   - Usa "RECORD" para guardar gastos, notas, deudas o información general.
2. Identifica nombres de personas o entidades en 'entities'.
3. TIPADO FINANCIERO ('transaction_type'): "Gasto", "Ingreso", "Me Deben", "Debo" o null.
4. FECHAS (Formato estricto YYYY-MM-DDTHH:MM:SS):
   - 'action_date': Fecha y hora de inicio de la alarma, evento o compromiso futuro. Asume 09:00 si no hay hora específica.
   - 'action_date_end': Fecha y hora de finalización del evento. Si se intuye duración (ej. "de 10 a 12" o "durante 2 horas"), calcúlala en base al inicio. Si no hay fin claro, asigna null.
5. UBICACIÓN ('location'): Si se menciona un lugar, calle, local o ciudad para un evento/nota, extráelo aquí. Si no, null.
6. RESUMEN: Desglose completo (3 a 6 frases densas) con motivos, cifras y acuerdos.

Devuelve la respuesta estructurada estrictamente con el siguiente esquema JSON:
{{
  "intent": "RECORD" | "EVENT" | "QUERY",
  "master_category": "FINANCE" | "HEALTH" | "KNOWLEDGE" | "INVENTORY" | "DIARY" | "CRM",
  "query_filters": {{
    "entities": ["EntidadBuscada"],
    "category": "FINANCE" | "HEALTH" | "KNOWLEDGE" | "INVENTORY" | "DIARY" | "CRM" | null,
    "transaction_type": "Gasto" | "Ingreso" | "Me Deben" | "Debo" | null,
    "status": "Pendiente" | "Completado" | "Cancelado" | null,
    "date_start": "YYYY-MM-DD or null",
    "date_end": "YYYY-MM-DD or null"
  }},
  "general_metadata": {{
    "title": "Título descriptivo en español (3 a 5 palabras)",
    "executive_summary": "Explicación detallada de hasta 1500 caracteres con contexto y acuerdos",
    "location": "Ubicación detectada o null",
    "tags": ["Etiqueta1", "Etiqueta2"],
    "entities": ["PersonaOEntidad1", "PersonaOEntidad2"],
    "sentiment": "Positive" | "Neutral" | "Negative"
  }},
  "specific_data": {{
    "numeric_amount": 0.00,
    "transaction_type": "Gasto" | "Ingreso" | "Me Deben" | "Debo" | null,
    "detected_date": "YYYY-MM-DDTHH:MM:SS or null",
    "action_date": "YYYY-MM-DDTHH:MM:SS or null",
    "action_date_end": "YYYY-MM-DDTHH:MM:SS or null",
    "status": "Pendiente" | "Completado" | "Cancelado" | null,
    "hidden_tasks": ["Tarea detectada en español"]
  }},
  "raw_context": "Transcripción completa o descripción detallada en español de lo observado"
}}"""