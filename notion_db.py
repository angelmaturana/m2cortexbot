import logging
import os
import re
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

def _is_valid_iso_date(d):
    """Evita que una fecha alucinada bloquee la búsqueda en Notion."""
    if not d or not isinstance(d, str):
        return False
    return bool(re.match(r'^\d{4}-\d{2}-\d{2}', d.strip()))

def save_to_notion(data: dict):
    metadata = data.get("general_metadata", {})
    specific = data.get("specific_data", {})

    title = metadata.get("title", "Entrada sin título")
    category = data.get("master_category", "KNOWLEDGE")
    summary = metadata.get("executive_summary", "")
    tags = [t.replace("#", "").strip() for t in metadata.get("tags", []) if isinstance(t, str) and t.strip()]
    amount = float(specific.get("numeric_amount") or 0.0)

    tz_madrid = ZoneInfo("Europe/Madrid")
    date_val = specific.get("detected_date")
    
    if not date_val or str(date_val).lower() == "null":
        date_val = datetime.now(tz_madrid).isoformat()
    elif len(str(date_val)) == 10 and "T" not in str(date_val):
        current_time = datetime.now(tz_madrid).strftime("%H:%M:%S")
        date_val = f"{date_val}T{current_time}"

    properties = {
        "Name": {"title": [{"text": {"content": title[:100]}}]},
        "Category": {"select": {"name": category}},
        "Date": {"date": {"start": str(date_val)}},
        "Amount": {"number": amount},
        "Tags": {"multi_select": [{"name": tag[:100]} for tag in tags]},
        "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]}
    }

    tx_type = specific.get("transaction_type")
    if tx_type and tx_type in ["Gasto", "Ingreso", "Me Deben", "Debo"]:
        properties["Transaction Type"] = {"select": {"name": tx_type}}

    entities = metadata.get("entities", [])
    if entities and isinstance(entities, list):
        clean_entities = [e.strip()[:100] for e in entities if isinstance(e, str) and e.strip()]
        if clean_entities:
            properties["Entities"] = {"multi_select": [{"name": e} for e in clean_entities]}

    status_val = specific.get("status")
    if status_val and status_val in ["Pendiente", "Completado", "Cancelado"]:
        properties["Status"] = {"select": {"name": status_val}}

    action_date_val = specific.get("action_date")
    action_date_end = specific.get("action_date_end")
    if action_date_val and str(action_date_val).lower() != "null":
        date_dict = {"start": str(action_date_val)}
        if action_date_end and str(action_date_end).lower() != "null":
            date_dict["end"] = str(action_date_end)
        properties["Action Date"] = {"date": date_dict}

    children = []
    location = metadata.get("location")
    if location and str(location).lower() != "null":
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"📍 Ubicación: {location}"}}]}
        })

    raw_ctx = str(data.get("raw_context") or "Sin contexto adicional.")
    children.extend([
        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🔍 Detalle y Contexto"}}]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": raw_ctx[:2000]}}]}}
    ])

    tasks = specific.get("hidden_tasks", [])
    if tasks and isinstance(tasks, list):
        children.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "✅ Tareas Detectadas"}}]}})
        for task in tasks:
            children.append({
                "object": "block",
                "type": "to_do",
                "to_do": {"rich_text": [{"type": "text", "text": {"content": str(task)[:2000]}}], "checked": False}
            })

    notion.pages.create(parent={"database_id": NOTION_DATABASE_ID}, properties=properties, children=children)

def _build_notion_filter(query_filters: dict = None, category_fallback: str = None):
    and_conditions = []
    
    cat = None
    if query_filters and query_filters.get("category"):
        cat = query_filters.get("category")
    elif category_fallback and category_fallback != "KNOWLEDGE":
        cat = category_fallback
        
    valid_categories = ["FINANCE", "HEALTH", "KNOWLEDGE", "INVENTORY", "DIARY", "CRM"]
    if cat in valid_categories and cat != "KNOWLEDGE":
        and_conditions.append({"property": "Category", "select": {"equals": cat}})

    if query_filters:
        tx_type = query_filters.get("transaction_type")
        if tx_type in ["Gasto", "Ingreso", "Me Deben", "Debo"]:
            and_conditions.append({"property": "Transaction Type", "select": {"equals": tx_type}})

        entities = query_filters.get("entities", [])
        if entities and isinstance(entities, list):
            for ent in entities:
                if isinstance(ent, str) and ent.strip():
                    and_conditions.append({"property": "Entities", "multi_select": {"contains": ent.strip()}})

        status = query_filters.get("status")
        if status in ["Pendiente", "Completado", "Cancelado"]:
            and_conditions.append({"property": "Status", "select": {"equals": status}})

        date_start = query_filters.get("date_start")
        if _is_valid_iso_date(date_start):
            and_conditions.append({"property": "Date", "date": {"on_or_after": date_start.strip()}})

        date_end = query_filters.get("date_end")
        if _is_valid_iso_date(date_end):
            and_conditions.append({"property": "Date", "date": {"on_or_before": date_end.strip()}})

    if len(and_conditions) == 1: return and_conditions[0]
    elif len(and_conditions) > 1: return {"and": and_conditions}
    return None

def query_notion_db(query_filters: dict = None, category_filter: str = None, max_records: int = 50):
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    
    filter_obj = _build_notion_filter(query_filters, category_filter)
    raw_pages, has_more, start_cursor = [], True, None

    try:
        while has_more and len(raw_pages) < max_records:
            page_size = min(50, max_records - len(raw_pages))
            payload = {"page_size": page_size, "sorts": [{"property": "Date", "direction": "descending"}]}
            if filter_obj: payload["filter"] = filter_obj
            if start_cursor: payload["start_cursor"] = start_cursor

            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            raw_pages.extend(data.get("results", []))
            has_more, start_cursor = data.get("has_more", False), data.get("next_cursor")

        if not raw_pages and filter_obj is not None:
            logger.info("Filtro sin coincidencias. Consulta amplia...")
            fallback_payload = {"page_size": 15, "sorts": [{"property": "Date", "direction": "descending"}]}
            if category_filter and category_filter != "KNOWLEDGE":
                fallback_payload["filter"] = {"property": "Category", "select": {"equals": category_filter}}
            fallback_res = requests.post(url, json=fallback_payload, headers=headers)
            fallback_res.raise_for_status()
            raw_pages = fallback_res.json().get("results", [])

        formatted_results = []
        for page in reversed(raw_pages):
            props = page.get("properties", {})
            try: title = props.get("Name", {}).get("title", [{"plain_text": "Sin título"}])[0].get("plain_text", "Sin título")
            except: title = "Sin título"
            try: summary = props.get("Summary", {}).get("rich_text", [{"plain_text": "Sin resumen"}])[0].get("plain_text", "Sin resumen")
            except: summary = "Sin resumen"
            try: amount = props.get("Amount", {}).get("number", 0) or 0
            except: amount = 0
            try: date_str = props.get("Date", {}).get("date").get("start")
            except: date_str = "Sin fecha"
            
            record_text = f"- [{date_str}] {title}: {summary}"
            if amount > 0: record_text += f" | Importe: {amount}€"
            
            tx_type_obj = props.get("Transaction Type", {}).get("select")
            if tx_type_obj and tx_type_obj.get("name"): record_text += f" | Tipo: {tx_type_obj.get('name')}"
            
            action_date_obj = props.get("Action Date", {}).get("date")
            if action_date_obj and action_date_obj.get("start"):
                record_text += f" | Acción: {action_date_obj.get('start')}"
                
            formatted_results.append(record_text)
            
        return formatted_results
    except Exception as e:
        logger.error(f"Error crítico conectando a Notion: {e}")
        err_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try: err_msg = e.response.json().get("message", e.response.text)
            except: err_msg = e.response.text
        return [f"ERROR_NOTION_API: {err_msg}"]
