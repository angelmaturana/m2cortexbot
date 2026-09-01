import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import libsql_client

logger = logging.getLogger("M2Cortex-DB")

TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

if not TURSO_URL or not TURSO_TOKEN:
    logger.error("❌ CRÍTICO: Faltan credenciales de Turso en .env")

def _get_client():
    return libsql_client.create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN)

def init_db():
    """Crea la tabla agnóstica de almacenamiento si es el primer despliegue."""
    query = """
    CREATE TABLE IF NOT EXISTS boveda_memorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha_registro TEXT,
        intent TEXT,
        categoria TEXT,
        entidades TEXT,
        monto REAL,
        fecha_accion TEXT,
        resumen TEXT,
        json_crudo TEXT
    );
    """
    try:
        with _get_client() as client:
            client.execute(query)
            logger.info("✅ Bóveda de Memorias en Turso inicializada y lista.")
    except Exception as e:
        logger.error(f"Error inicializando Turso: {e}")

def save_record(data: dict):
    """Inserta cualquier tipo de memoria, evento o transacción en la bóveda."""
    tz_madrid = ZoneInfo("Europe/Madrid")
    fecha_actual = datetime.now(tz_madrid).isoformat()
    
    entidades_lista = data.get("entidades_principales", [])
    entidades_str = ", ".join(entidades_lista) if isinstance(entidades_lista, list) else ""
    
    monto = float(data.get("monto_calculado") or 0.0)
    
    query = """
    INSERT INTO boveda_memorias 
    (fecha_registro, intent, categoria, entidades, monto, fecha_accion, resumen, json_crudo)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    args = [
        fecha_actual,
        data.get("intent", "RECORD"),
        data.get("categoria", "GENERAL"),
        entidades_str,
        monto,
        data.get("fecha_accion"),
        data.get("resumen", "Sin resumen"),
        json.dumps(data)
    ]
    
    try:
        with _get_client() as client:
            client.execute(query, args)
    except Exception as e:
        logger.error(f"Error guardando en Turso: {e}")
        raise e

def get_balance(entidad: str) -> float:
    """Calcula matemáticamente el saldo exacto mediante agregación SQL."""
    if not entidad:
        return 0.0
        
    query = "SELECT SUM(monto) as saldo FROM boveda_memorias WHERE entidades LIKE ?"
    try:
        with _get_client() as client:
            result = client.execute(query, [f"%{entidad}%"])
            saldo = result.rows[0][0]
            return float(saldo) if saldo is not None else 0.0
    except Exception as e:
        logger.error(f"Error calculando saldo en Turso: {e}")
        return 0.0

def query_history(entidad: str = None, limite: int = 50) -> list:
    """Extrae el historial crudo para el motor de razonamiento RAG."""
    query = "SELECT fecha_registro, monto, resumen FROM boveda_memorias"
    args = []
    
    if entidad:
        query += " WHERE entidades LIKE ?"
        args.append(f"%{entidad}%")
        
    query += f" ORDER BY fecha_registro DESC LIMIT {limite}"
    
    resultados = []
    try:
        with _get_client() as client:
            result = client.execute(query, args)
            for row in result.rows:
                fecha = row[0][:16].replace("T", " ")
                monto = float(row[1])
                resumen = row[2]
                
                texto = f"- [{fecha}] {resumen}"
                if monto != 0.0:
                    texto += f" | Implicación numérica: {monto}"
                resultados.append(texto)
        return resultados
    except Exception as e:
        logger.error(f"Error buscando historial en Turso: {e}")
        return [f"ERROR_TURSO: {e}"]