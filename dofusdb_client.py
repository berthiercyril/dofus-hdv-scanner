"""Résolution d'un nom de ressource lu par OCR vers l'identifiant DofusDB utilisé comme
resourceId par l'API communautaire de Dofus-Craft (voir src/services/dofusdb-api.ts)."""
import re
import unicodedata

import requests

API_URL = "https://api.dofusdb.fr"
_cache: dict[str, str | None] = {}


def _normalize(value: str) -> str:
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", stripped).strip().lower()


def resolve_resource_id(ocr_name: str, timeout: float = 5.0) -> str | None:
    """Retourne l'id DofusDB (resourceId) correspondant au nom lu, ou None si aucune
    correspondance fiable (une seule ressource au nom exactement identique, insensible aux
    accents/casse) n'est trouvée. Ne devine jamais en cas d'ambiguïté."""
    key = _normalize(ocr_name)
    if not key:
        return None
    if key in _cache:
        return _cache[key]

    params = {
        "$limit": 10,
        "name.fr[$regex]": f"^{re.escape(ocr_name.strip())}$",
        "name.fr[$options]": "i",
    }
    try:
        response = requests.get(f"{API_URL}/items", params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json().get("data", [])
    except (requests.RequestException, ValueError):
        return None

    matches = [item for item in data if _normalize(item.get("name", {}).get("fr", "")) == key]
    resource_id = str(matches[0]["id"]) if len(matches) == 1 else None
    _cache[key] = resource_id
    return resource_id
