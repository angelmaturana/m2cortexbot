import logging
import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from notion_client import Client as NotionClient

load_dotenv()
logger = logging.getLogger("M2Cortex")

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

notion = NotionClient(auth=NOTION_API_KEY)

def save_to_notion(data: dict):
    """Guarda los datos estructurados en Notion integrando Transaction Type."""
    metadata = data.get("general_metadata", {})
    specific = data.get("specific_data", {})

    title = metadata.get("title", "Entrada sin título")
    category = data.get("master_category", "KNOWLEDGE")
    summary = metadata.get("executive_summary", "")
    tags = [t.replace("#", "").strip() for t in metadata.get("tags", []) if isinstance(t, str) and t.strip()]
    amount = float(specific.get("numeric_amount") or 0.0)

    # 1. Fecha de registro (Europe/Madrid)
    date_val = specific.get("detected_date")
    if not date_val or str(date_val).lower() == "null":
        tz_madrid = ZoneInfo("Europe/Madrid")
        date_val = datetime.now(tz_madrid).isoformat()

    properties = {
        "Name": {"title": [{"text": {"content": title[:100]}}]},
        "Category": {"select": {"name": category}},
        "Date": {"date": {"start": str(date_val)}},
        "Amount": {"number": amount},
        "Tags": {"multi_select": [{"name": tag[:100]} for tag in tags]},
        "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]}
    }

    # 2. Tipado de Transacción (Transaction Type)
    tx_type = specific.get("transaction_type")
    if tx_type and tx_type in ["Gasto", "Ingreso", "Me Deben", "Debo"]:
        properties["Transaction Type"] = {"select": {"name": tx_type}}

    # 3. Entidades (Entities)
    entities = metadata.get("entities", [])
    if entities and isinstance(entities, list):
        clean_entities = [e.strip()[:100] for e in entities if isinstance(e, str) and e.strip()]
        if clean_entities:
            properties["Entities"] = {"multi_select": [{"name": e} for e in clean_entities]}

    # 4. Estado (Status)
    status_val = specific.get("status")
    if status_val and status_val in ["Pendiente", "Completado", "Cancelado"]:
        properties["Status"] = {"select": {"name": status_val}}

    # 5. Fecha de Acción (Action Date)
    action_date_val = specific.get("action_date")
    if action_date_val and str(action_date_val).lower() != "null":
        properties["Action Date"] = {"date": {"start": str(action_date_val)}}

    children = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🔍 Detalle y Contexto"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": data.get("raw_context", "Sin contexto adicional.")}}]}
        }
    ]

    tasks = specific.get("hidden_tasks", [])
    if tasks and isinstance(tasks, list):
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "✅ Tareas Detectadas"}}]}
        })
        for task in tasks:
            children.append({
                "object": "block",
                "type": "to_do",
                "to_do": {
                    "rich_text": [{"type": "text", "text": {"content": str(task)}}],
                    "checked": False
                }
            })

    notion.pages.create(
        parent={"database_id": NOTION_DATABASE_ID},
        properties=properties,
        children=children
    )

def _build_notion_filter(query_filters: dict = None, category_fallback: str = None):
    """Construye el árbol de filtros combinados para la API de Notion."""
    and_conditions = []
    
    # Filtro Categoría
    cat = None
    if query_filters and query_filters.get("category"):
        cat = query_filters.get("category")
    elif category_fallback and category_fallback != "KNOWLEDGE":
        cat = category_fallback
        
    valid_categories = ["FINANCE", "HEALTH", "KNOWLEDGE", "INVENTORY", "DIARY", "CRM"]
    if cat in valid_categories and cat != "KNOWLEDGE":
        and_conditions.append({
            "property": "Category",
            "select": {"equals": cat}
        })

    if query_filters:
        # Filtro Transaction Type
        tx_type = query_filters.get("transaction_type")
        if tx_type in ["Gasto", "Ingreso", "Me Deben", "Debo"]:
            and_conditions.append({
                "property": "Transaction Type",
                "select": {"equals": tx_type}
            })

        # Filtro Entidades
        entities = query_filters.get("entities", [])
        if entities and isinstance(entities, list):
            for ent in entities:
                if isinstance(ent, str) and ent.strip():
                    and_conditions.append({
                        "property": "Entities",
                        "multi_select": {"contains": ent.strip()}
                    })

        # Filtro Estado
        status = query_filters.get("status")
        if status in ["Pendiente", "Completado", "Cancelado"]:
            and_conditions.append({
                "property": "Status",
                "select": {"equals": status}
            })

        # Filtros Fechas
        date_start = query_filters.get("date_start")
        if date_start and str(date_start).lower() != "null":
            and_conditions.append({
                "property": "Date",
                "date": {"on_or_after": str(date_start)}
            })

        date_end = query_filters.get("date_end")
        if date_end and str(date_end).lower() != "null":
            and_conditions.append({
                "property": "Date",
                "date": {"on_or_before": str(date_end)}
            })

    if len(and_conditions) == 1:
        return and_conditions[0]
    elif len(and_conditions) > 1:
        return {"and": and_conditions}
    return None

def query_notion_db(query_filters: dict = None, category_filter: str = None, max_records: int = 50):
    """Consulta Notion con paginación automática y extracción de Transaction Type."""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    
    filter_obj = _build_notion_filter(query_filters, category_filter)
    
    raw_pages = []
    has_more = True
    start_cursor = None

    try:
        while has_more and len(raw_pages) < max_records:
            page_size = min(50, max_records - len(raw_pages))
            payload = {
                "page_size": page_size,
                "sorts": [{"property": "Date", "direction": "descending"}]
            }
            if filter_obj:
                payload["filter"] = filter_obj
            if start_cursor:
                payload["start_cursor"] = start_cursor

            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            results = data.get("results", [])
            raw_pages.extend(results)
            
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        # Fallback de recuperación amplia si el filtro no arrojó resultados
        if not raw_pages and filter_obj is not None:
            logger.info("Filtro específico sin coincidencias. Ejecutando consulta amplia de recuperación...")
            fallback_payload = {
                "page_size": 15,
                "sorts": [{"property": "Date", "direction": "descending"}]
            }
            if category_filter and category_filter != "KNOWLEDGE":
                fallback_payload["filter"] = {"property": "Category", "select": {"equals": category_filter}}
                
            fallback_res = requests.post(url, json=fallback_payload, headers=headers)
            fallback_res.raise_for_status()
            raw_pages = fallback_res.json().get("results", [])

        # Formateo cronológico (antiguos primero -> recientes al final)
        formatted_results = []
        for page in reversed(raw_pages):
            props = page.get("properties", {})
            
            try:
                title = props.get("Name", {}).get("title", [{"plain_text": "Sin título"}])[0].get("plain_text", "Sin título")
            except Exception:
                title = "Sin título"
                
            try:
                summary = props.get("Summary", {}).get("rich_text", [{"plain_text": "Sin resumen"}])[0].get("plain_text", "Sin resumen")
            except Exception:
                summary = "Sin resumen"
                
            try:
                amount = props.get("Amount", {}).get("number", 0) or 0
            except Exception:
                amount = 0
            
            try:
                date_obj = props.get("Date", {}).get("date")
                date_str = date_obj.get("start") if date_obj else "Sin fecha"
            except Exception:
                date_str = "Sin fecha"
            
            record_text = f"- [{date_str}] {title}: {summary}"
            if amount > 0:
                record_text += f" | Importe: {amount}€"
                
            tx_type_obj = props.get("Transaction Type", {}).get("select")
            if tx_type_obj and tx_type_obj.get("name"):
                record_text += f" | Tipo Transacción: {tx_type_obj.get('name')}"
                
            entities_list = props.get("Entities", {}).get("multi_select", [])
            if entities_list:
                ent_names = [e.get("name") for e in entities_list if e.get("name")]
                if ent_names:
                    record_text += f" | Entidades: {', '.join(ent_names)}"
                    
            status_obj = props.get("Status", {}).get("select")
            if status_obj and status_obj.get("name"):
                record_text += f" | Estado: {status_obj.get('name')}"
                
            action_date_obj = props.get("Action Date", {}).get("date")
            if action_date_obj and action_date_obj.get("start"):
                record_text += f" | Fecha de Acción: {action_date_obj.get('start')}"
                
            formatted_results.append(record_text)
            
        return formatted_results

    except Exception as e:
        logger.error(f"Error crítico conectando a Notion: {e}")
        if hasattr(e, 'response') and e.response is not None:
            return [f"ERROR_NOTION_API: {e.response.text}"]
        return [f"ERROR_NOTION_API: {str(e)}"]