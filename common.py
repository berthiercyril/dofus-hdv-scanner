"""Fonctions partagées entre calibrate.py et dofus_hdv_scanner.py."""
import json
import re
import sys
import unicodedata
import uuid
from pathlib import Path

# Une fois compilé en .exe (PyInstaller), __file__ pointe vers le dossier temporaire
# d'extraction (_MEIxxxxx), pas vers le dossier où se trouve l'exécutable : il faut donc
# se baser sur sys.executable dans ce cas pour retrouver config.json à côté de l'exe.
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent

CONFIG_PATH = APP_DIR / "config.json"


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
