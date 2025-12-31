import httpx

BASE = "https://api.euskadi.eus/procurements"

async def fetch_json(url: str):
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()

def notices_url(contract_type_id, page):
    return (
        f"{BASE}/contracting-notices?"
        f"contract-type-id={contract_type_id}"
        f"&publication-date.gt=2025-01-01"
        f"&publication-date.lt=2025-12-31"
        f"&orderBy=lastPublicationDate"
        f"&orderType=DESC"
        f"&currentPage={page}"
        f"&itemsOfPage=50"
        f"&lang=SPANISH"
    )

def contracts_url(contract_type_id, page):
    return (
        f"{BASE}/contracts?"
        f"contract-type-id={contract_type_id}"
        f"&award-date.gt=2025-01-01"
        f"&award-date.lt=2025-12-31"
        f"&orderBy=awardDate"
        f"&orderType=DESC"
        f"&currentPage={page}"
        f"&itemsOfPage=50"
        f"&lang=SPANISH"
    )
