"""Boucle de lecture d'écran + logique d'envoi, partagée entre la version console
(dofus_hdv_scanner.py) et l'interface graphique (gui.py)."""
import re
import threading
import time

import mss
import pytesseract
from PIL import Image

from community_api import CommunityApiClient
from dofusdb_client import resolve_resource_id

PRICE_PATTERN = re.compile(r"(\d[\d\s.,]*\d|\d)")


def grab_text(sct: "mss.mss", region: dict) -> str:
    shot = sct.grab(region)
    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    return pytesseract.image_to_string(image, config="--psm 7").strip()


def parse_amount_kamas(raw_text: str) -> int | None:
    match = PRICE_PATTERN.search(raw_text)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else None


class ScanLoop(threading.Thread):
    """Tourne dans un thread séparé. `log(str)` est appelé pour chaque ligne à afficher
    (thread appelant = ce thread ScanLoop, jamais le thread principal)."""

    def __init__(self, config: dict, device_id: str, log):
        super().__init__(daemon=True)
        self.config = config
        self.device_id = device_id
        self.log = log
        self.client = CommunityApiClient(config["api_url"], config["api_key"])

        self._paused = threading.Event()
        self._dry_run = threading.Event()
        if config.get("dry_run", True):
            self._dry_run.set()
        self._stop = threading.Event()

        self._last_seen: tuple[str, int] | None = None
        self._last_submitted_at: dict[str, float] = {}

    def stop(self) -> None:
        self._stop.set()

    def set_paused(self, value: bool) -> None:
        self._paused.set() if value else self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def set_dry_run(self, value: bool) -> None:
        self._dry_run.set() if value else self._dry_run.clear()

    def is_dry_run(self) -> bool:
        return self._dry_run.is_set()

    def run(self) -> None:
        cooldown = float(self.config.get("submission_cooldown_seconds", 15))
        outlier_ratio = float(self.config.get("outlier_ratio_warning", 3.0))
        poll = float(self.config.get("poll_interval_seconds", 1.0))
        pytesseract.pytesseract.tesseract_cmd = self.config["tesseract_path"]

        try:
            with mss.mss() as sct:
                while not self._stop.is_set():
                    if self._paused.is_set():
                        time.sleep(0.3)
                        continue
                    self._tick(sct, cooldown, outlier_ratio)
                    time.sleep(poll)
        except Exception as exc:  # noqa: BLE001 - on veut logger toute erreur inattendue dans l'UI
            self.log(f"[erreur] {exc}")
        self.log("Scan arrêté.")

    def _tick(self, sct, cooldown: float, outlier_ratio: float) -> None:
        name_text = grab_text(sct, self.config["name_region"])
        price_text = grab_text(sct, self.config["price_region"])
        amount = parse_amount_kamas(price_text)

        if not name_text or amount is None:
            return

        current = (name_text, amount)
        if current == self._last_seen:
            return
        self._last_seen = current

        self.log(f"Lu : '{name_text}' — {amount} kamas")

        resource_id = resolve_resource_id(name_text)
        if resource_id is None:
            self.log("  -> ressource non reconnue avec certitude, ignoré.")
            return

        now = time.monotonic()
        if now - self._last_submitted_at.get(resource_id, 0) < cooldown:
            self.log("  -> même ressource envoyée récemment, ignoré (cooldown).")
            return

        median = self.client.get_median(resource_id, self.config["server_id"])
        if median and median.get("medianAmountKamas"):
            ratio = amount / median["medianAmountKamas"] if median["medianAmountKamas"] else None
            if ratio and (ratio > outlier_ratio or ratio < 1 / outlier_ratio):
                self.log(
                    f"  -> ATTENTION: {amount} très différent de la médiane actuelle "
                    f"({median['medianAmountKamas']}), probable erreur de lecture: ignoré."
                )
                return

        if self._dry_run.is_set():
            self.log(f"  -> [dry-run] aurait envoyé resourceId={resource_id} amountKamas={amount}")
            return

        ok = self.client.submit_price(resource_id, self.config["server_id"], amount, self.device_id)
        self.log("  -> envoyé avec succès." if ok else "  -> ÉCHEC de l'envoi.")
        if ok:
            self._last_submitted_at[resource_id] = now
