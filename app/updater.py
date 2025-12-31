from datetime import datetime
from sqlalchemy.orm import Session
from .models import Notice, Contract, Meta
from .euskadi_client import fetch_json, notices_url, contracts_url

def set_meta(db: Session, key, value):
    row = db.get(Meta, key)
    if not row:
        row = Meta(key=key, value=value)
        db.add(row)
    else:
        row.value = value

def get_meta(db: Session, key, default="—"):
    row = db.get(Meta, key)
    return row.value if row else default

async def refresh_all(db: Session):
    for contract_type in (1, 2):
        page = 1
        while True:
            data = await fetch_json(notices_url(contract_type, page))
            for item in data.get("items", []):
                n = db.get(Notice, item["id"]) or Notice(id=item["id"])
                n.object = item.get("object")
                n.last_publication_date = item.get("lastPublicationDate")
                n.first_publication_date = item.get("firstPublicationDate")
                n.contract_type_id = item.get("contractType", {}).get("id")
                n.procedure_status_id = item.get("contractProcedureStatus", {}).get("id")
                n.budget_without_vat = item.get("budgetWithoutVAT")
                n.main_entity_of_page = item.get("mainEntityOfPage")
                n.contracting_authority_name = item.get("contractingAuthority", {}).get("name")
                db.add(n)

            db.commit()
            if page >= data.get("totalPages", 0):
                break
            page += 1

    set_meta(db, "last_update_human", datetime.now().strftime("%Y-%m-%d %H:%M"))
    db.commit()
