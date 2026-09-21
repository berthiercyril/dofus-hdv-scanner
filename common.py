"""Fonctions partagées entre calibrate.py et dofus_hdv_scanner.py."""
import json
import re
import unicodedata
import uuid
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.json")


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")


def slugify_server_name(name: str) -> str:
    """Reproduit exactement slugifyServerName() de src/services/dofusdb-mappers.ts,
    pour que server_id corresponde à ce que le site web utilise déjà."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", name) if unicodedata.category(c) != "Mn"
    )
    lowered = stripped.lower().strip()
    dashed = re.sub(r"[^a-z0-9]+", "-", lowered)
    return dashed.strip("-")


def get_or_create_device_id(config: dict) -> str:
    if not config.get("device_id"):
        config["device_id"] = str(uuid.uuid4())
        save_config(config)
    return config["device_id"]
