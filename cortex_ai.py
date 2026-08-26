import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
logger = logging.getLogger("M2Cortex")

# --- SISTEMA DE ROTACIÓN DE 6 LLAVES GEMINI ---
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

def get_gemini_client():
    """Devuelve el cliente de Gemini apuntando a la llave activa."""
    return genai.Client(api_key=API_KEYS[CURRENT_KEY_INDEX])

def rotate_key():
    """Rota cíclicamente a la siguiente API Key disponible."""
    global CURRENT_KEY_INDEX
    CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(API_KEYS)
    logger.warning(f"🔄 Rotando a la API Key de Gemini: Llave {CURRENT_KEY_INDEX + 1} de {len(API_KEYS)}")

def call_gemini_with_retry(contents, config=None):
    """Envuelve la llamada a Gemini 3.6 Flash con rotación automática ante fallos de cuota o clave."""
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

def get_classifier_prompt():
    """Genera el prompt inyectando fecha, tipado de transacción, filtros RAG y soporte multimodal."""
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
1. Todo el contenido generado DEBE estar redactado estrictamente en ESPAÑOL.
2. Identifica nombres de personas, contactos, clientes o entidades y colócalos en 'entities'.
3. TIPADO FINANCIERO ('transaction_type'):
   - "Gasto": Compras, consumos, facturas pagadas o salidas directas de dinero.
   - "Ingreso": Cobros directos recibidos, salarios o entradas de dinero.
   - "Me Deben": Préstamos realizados a terceros o saldos pendientes a favor del usuario.
   - "Debo": Deudas o compromisos de pago que el usuario asume ante un tercero.
   - null: Si la entrada no es una operación económica ni involucra dinero.
4. ESTADO ('status'):
   - "Pendiente": Para deudas activas ("Me Deben" o "Debo"), tareas no terminadas o alarmas.
   - "Completado": Para gastos liquidados, ingresos recibidos o tareas ya ejecutadas.
   - null: Entradas informativas neutras sin ciclo de vida.
5. FECHAS:
   - 'detected_date': Si el evento ocurrió en una fecha/hora pasada o específica diferente al momento actual (ej. "ayer a las 20:00", "el 12 de agosto a las 10:00"), calcúlala en formato ISO 8601 completo (YYYY-MM-DDTHH:MM:SS). Si el evento ocurre en el momento actual o no se especifica hora/fecha pasada, asigna estrictamente null para registrar la marca de tiempo exacta del sistema.
   - 'action_date': Si el mensaje especifica una acción/alarma futura (ej. "mañana a las 11:30"), calcúlala en base a la 'Fecha y hora actual del sistema' en formato ISO 8601 (YYYY-MM-DDTHH:MM:SS). Si no se indica hora, asume 09:00:00. Si no hay acción futura, asigna null.
6. RESUMEN DETALLADO ('executive_summary'): Desglose completo (3 a 6 frases densas, máximo 1.500 caracteres) con motivos, cifras, acuerdos y estado.
7. SI INTENT ES 'QUERY':
   - Configura 'query_filters' con precisión:
     - Si pregunta por deudas por cobrar: 'transaction_type': "Me Deben", 'status': "Pendiente".
     - Si pregunta por gastos de hoy: 'category': "FINANCE", 'transaction_type': "Gasto", 'date_start': "{today_iso}".
     - Si pregunta por alguien específico: pon su nombre en 'entities'.

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
    "tags": ["Etiqueta1", "Etiqueta2"],
    "entities": ["PersonaOEntidad1", "PersonaOEntidad2"],
    "sentiment": "Positive" | "Neutral" | "Negative"
  }},
  "specific_data": {{
    "numeric_amount": 0.00,
    "transaction_type": "Gasto" | "Ingreso" | "Me Deben" | "Debo" | null,
    "detected_date": "YYYY-MM-DDTHH:MM:SS or null",
    "action_date": "YYYY-MM-DDTHH:MM:SS or null",
    "status": "Pendiente" | "Completado" | "Cancelado" | null,
    "hidden_tasks": ["Tarea detectada en español"]
  }},
  "raw_context": "Transcripción completa o descripción visual detallada en español de lo observado"
}}"""