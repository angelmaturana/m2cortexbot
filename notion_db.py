import logging
import os
import requests
from datetime import datetime
from dotenv import load_dotenv
from notion_client import Client as NotionClient

load_dotenv()
logger = logging.getLogger("M2Cortex")

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

notion = NotionClient(auth=NOTION_API_KEY)

def save_to_notion(data: dict):
    """Guarda los datos estructurados en la tabla de Notion gestionando todas las columnas."""
    metadata = data.get("general_metadata", {})
    specific = data.get("specific_data", {})

    title = metadata.get("title", "Entrada sin título")
    category = data.get("master_category", "KNOWLEDGE")
    summary = metadata.get("executive_summary", "")
    tags = [t.replace("#", "").strip() for t in metadata.get("tags", []) if t.strip()]
    amount = float(specific.get("numeric_amount") or 0.0)

    # 1. Fecha de registro (Date)
    date_val = specific.get("detected_date")
    if not date_val or str(date_val).lower() == "null":
        date_val = datetime.now().astimezone().isoformat()

    properties = {
        "Name": {"title": [{"text": {"content": title[:100]}}]},
        "Category": {"select": {"name": category}},
        "Date": {"date": {"start": str(date_val)}},
        "Amount": {"number": amount},
        "Tags": {"multi_select": [{"name": tag[:100]} for tag in tags]},
        "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]}
    }

    # 2. Entidades (Entities)
    entities = metadata.get("entities", [])
    if entities and isinstance(entities, list):
        clean_entities = [e.strip()[:100] for e in entities if isinstance(e, str) and e.strip()]
        if clean_entities:
            properties["Entities"] = {"multi_select": [{"name": e} for e in clean_entities]}

    # 3. Estado (Status)
    status_val = specific.get("status")
    if status_val and status_val in ["Pendiente", "Completado", "Cancelado"]:
        properties["Status"] = {"select": {"name": status_val}}

    # 4. Fecha de Acción (Action Date)
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
    if tasks:
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

def query_notion_db(category_filter=None, limit=10):
    """Busca los últimos registros en Notion y formatea todos sus atributos."""
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    
    payload = {
        "page_size": limit
    }
    
    valid_categories = ["FINANCE", "HEALTH", "KNOWLEDGE", "INVENTORY", "DIARY", "CRM"]
    if category_filter in valid_categories and category_filter != "KNOWLEDGE":
        payload["filter"] = {
            "property": "Category",
            "select": {"equals": category_filter}
        }
        
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for page in data.get("results", []):
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
                
            results.append(record_text)
            
        return results
    except Exception as e:
        logger.error(f"Error crítico conectando directo a Notion: {e}")
        if hasattr(e, 'response') and e.response is not None:
            return [f"ERROR_NOTION_API: {e.response.text}"]
        return [f"ERROR_NOTION_API: {str(e)}"]