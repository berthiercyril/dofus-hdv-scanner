"""Boucle de lecture d'écran + logique d'envoi, partagée entre la version console
(dofus_hdv_scanner.py) et l'interface graphique (gui.py)."""
import hashlib
import queue
import re
import threading
import time

import mss
import pytesseract
from PIL import Image, ImageOps

from community_api import CommunityApiClient
from dofusdb_client import _levenshtein, resolve_resource_id

# La police du jeu est petite et crénelée : sans agrandissement ni renforcement du contraste,
# Tesseract confond facilement des lettres proches (ex: "i" lu comme "l" dans "Aile" -> "Alle").
_OCR_UPSCALE = 3


def _preprocess_for_ocr(image: Image.Image) -> Image.Image:
    grayscale = image.convert("L")
    upscaled = grayscale.resize(
        (grayscale.width * _OCR_UPSCALE, grayscale.height * _OCR_UPSCALE), Image.LANCZOS
    )
    return ImageOps.autocontrast(upscaled)


def grab_text(sct: "mss.mss", region: dict, psm: int = 7) -> str:
    shot = sct.grab(region)
    image = _preprocess_for_ocr(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"))
    text = pytesseract.image_to_string(image, config=f"--psm {psm}")
    # --psm 6 (bloc de texte) peut renvoyer plusieurs lignes : un nom d'équipement long passe
    # parfois à la ligne dans l'HDV ("Jugement de" / "Thanatena"), on les recolle.
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


# Seuil de luminosité (0-255, niveaux de gris) au-dessus duquel un pixel est considéré comme du
# texte : les chiffres de prix (saumon/blanc) sont bien plus clairs que le fond des lignes de lots.
_TEXT_BRIGHTNESS = 110


def _text_bands(image: Image.Image) -> list[tuple[int, int]]:
    """Bandes horizontales (y début, y fin) contenant du texte clair, de haut en bas."""
    gray = image.convert("L")
    width, height = gray.size
    pixels = gray.load()
    bands, start = [], None
    for y in range(height):
        bright = sum(1 for x in range(width) if pixels[x, y] > _TEXT_BRIGHTNESS)
        if bright >= 3 and start is None:
            start = y
        elif bright < 3 and start is not None:
            bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, height - 1))
    return [(top, bottom) for top, bottom in bands if bottom - top >= 4]


def grab_first_price(sct: "mss.mss", region: dict, extra_height: int) -> tuple[int | None, str]:
    """Lit le PREMIER prix (le plus bas) de la liste des lots d'un équipement, même quand la
    liste est décalée vers le bas : un objet de panoplie affiche une ligne « Panoplie des ... »
    en plus dans le panneau de détail, qui descend toute la liste d'environ une ligne de texte.

    On capture donc la zone calibrée (sur un objet SANS panoplie) prolongée de `extra_height`
    pixels vers le bas, on repère les bandes de texte clair, et on garde la première qui contient
    un nombre (une éventuelle bande sans chiffre, comme le bas de l'en-tête « Prix », est sautée).
    Renvoie (montant ou None, texte brut lu pour le journal)."""
    extended = dict(region, height=region["height"] + extra_height)
    shot = sct.grab(extended)
    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    texts = []
    for top, bottom in _text_bands(image):
        band = image.crop((0, max(0, top - 4), image.width, min(image.height, bottom + 5)))
        text = pytesseract.image_to_string(_preprocess_for_ocr(band), config="--psm 7").strip()
        texts.append(text)
        amount = parse_amount_kamas(text)
        if amount is not None:
            return amount, text
    return None, " | ".join(texts)


_LOT_SIZES = {1, 10, 100, 1000}


def _mask_kamas_icon(image: Image.Image) -> Image.Image:
    """Efface (en noir, comme le fond) les pixels jaunes du logo kamas affiché après chaque
    prix : Tesseract le lisait comme un chiffre de plus. Les chiffres du prix sont blancs, donc
    épargnés (leur composante bleue reste élevée)."""
    image = image.copy()
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b = pixels[x, y]
            if r > 120 and g > 90 and b < 90 and r - b > 70:
                pixels[x, y] = (0, 0, 0)
    return image


def grab_resource_lots(sct: "mss.mss", region: dict) -> dict[int, int]:
    """Lit le tableau des lots d'une ressource : {taille du lot: prix total}. Chaque ligne de
    texte de la zone est découpée en mots par Tesseract : le premier mot (le plus à gauche) est
    la taille du lot (1, 10, 100 ou 1000, éventuellement précédée d'un « x »), les chiffres des
    mots suivants forment le prix (le séparateur de milliers est un espace, d'où plusieurs mots).
    Une ligne illisible ou dont le lot n'est pas reconnu est ignorée."""
    shot = sct.grab(region)
    image = _mask_kamas_icon(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"))
    # Un seul appel Tesseract pour tout le tableau (~0,18 s au lieu de ~0,55 s pour un appel par
    # ligne). --psm 4 (colonne de texte) lit bien le lot « 1 », que --psm 6 prenait pour un « i ».
    lots = _parse_lot_lines(_ocr_lines(image, psm=4))
    if lots:
        return lots
    # Rien de reconnu (ex : lignes collées) : repli sur la lecture ligne par ligne, plus lente.
    for top, bottom in _text_bands(image):
        band = image.crop((0, max(0, top - 4), image.width, min(image.height, bottom + 5)))
        for words in _ocr_lines(band, psm=7):
            for lot, total in _parse_lot_lines([words]).items():
                lots.setdefault(lot, total)
    return lots


def _ocr_lines(image: Image.Image, psm: int) -> list[list[str]]:
    """Mots reconnus par Tesseract, regroupés par ligne (de haut en bas, de gauche à droite)."""
    data = pytesseract.image_to_data(
        _preprocess_for_ocr(image), config=f"--psm {psm}", output_type=pytesseract.Output.DICT
    )
    lines: dict[tuple[int, int, int], list[tuple[int, str]]] = {}
    for index, word in enumerate(data["text"]):
        if word.strip():
            key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
            lines.setdefault(key, []).append((data["left"][index], word))
    return [[word for _, word in sorted(line)] for line in lines.values()]


def _parse_lot_lines(lines: list[list[str]]) -> dict[int, int]:
    lots: dict[int, int] = {}
    for words in lines:
        # L'icône de la ressource, à gauche, peut produire un mot parasite : la taille du lot est
        # le premier mot qui vaut exactement 1, 10, 100 ou 1000.
        for index, word in enumerate(words[:-1]):
            lot_digits = re.sub(r"[^\d]", "", word)
            price_start = index + 1
            # Le lot de 1000 peut être affiché avec un séparateur de milliers (« 1 000 ») : sans
            # ce recollage, il serait lu comme un lot de 1 au prix de « 000 » + prix réel.
            if lot_digits == "1" and re.sub(r"[^\d]", "", words[price_start]) == "000":
                lot_digits, price_start = "1000", price_start + 1
            if lot_digits and int(lot_digits) in _LOT_SIZES:
                total = parse_amount_kamas(" ".join(words[price_start:]))
                if total:
                    lots.setdefault(int(lot_digits), total)
                break
    return lots


# Écart max (en rapport) entre le prix à l'unité d'un lot et la médiane des autres lots pour qu'il
# soit jugé cohérent. Au-delà, c'est une erreur d'OCR (chiffre en trop / manquant, x10) ou un lot
# mis en vente à un prix absurde.
_LOT_OUTLIER_RATIO = 3


def _median(values: list[float]) -> float:
    values = sorted(values)
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def pick_unit_price(lots: dict[int, int]) -> float | None:
    """Choisit le prix à l'unité à envoyer parmi les lots lus ({taille: prix total}). Plus le lot
    est gros, plus son prix à l'unité est proche du vrai prix du marché (le lot de 1 est souvent
    fantaisiste) : on retient donc le PLUS GROS lot dont le prix à l'unité est cohérent avec les
    autres, c'est-à-dire à moins de `_LOT_OUTLIER_RATIO` fois la médiane des autres lots (dans un
    sens comme dans l'autre). L'appli affiche le dernier relevé tel quel, sans confirmation : ce
    garde-fou évite d'envoyer un prix aberrant. Si aucun lot n'est cohérent : repli sur la médiane
    (ou, avec deux lots seulement, sur le plus bas des deux).
    Ex : 4 / 90 / 2 500 -> 4, 9, 25 à l'unité : 25 (lot de 100) > 3 x 6,5 est écarté,
    9 (lot de 10) est cohérent avec 4 -> 9. Avec 4 / 70 / 600 -> 4, 7, 6 -> 6 (lot de 100)."""
    units = {size: lots[size] / size for size in lots}
    if not units:
        return None
    for size in sorted(units, reverse=True):
        others = [unit for other, unit in units.items() if other != size]
        if not others:
            return units[size]
        reference = _median(others)
        if reference / _LOT_OUTLIER_RATIO <= units[size] <= reference * _LOT_OUTLIER_RATIO:
            return units[size]
    # Deux lots seulement et incohérents : leur moyenne ne vaudrait rien, on garde le plus bas.
    return _median(list(units.values())) if len(units) > 2 else min(units.values())


# Réglages propres à chaque type de fenêtre HDV. En mode "item", la zone PRIX doit couvrir
# uniquement le prix de la PREMIÈRE ligne de la liste des lots (le moins cher, affiché en haut).
MODES = {
    "resource": {
        "name_region": "name_region",
        "price_region": "price_region",
        "name_psm": 7,
        "label": "ressource",
        # Comme pour les items, le nom peut se rafraîchir avant le tableau des lots au clic :
        # deux lectures identiques sont exigées. À écran inchangé, la seconde réutilise la
        # première sans OCR : elle ne coûte qu'un intervalle de sondage (0,1 s).
        "stable_reads": 2,
        "max_poll_seconds": 0.1,
    },
    "item": {
        "name_region": "item_name_region",
        "price_region": "item_price_region",
        "name_psm": 6,
        "label": "objet",
        # Au changement d'objet, le nom et la liste des prix ne se rafraîchissent pas forcément
        # au même instant : on exige deux lectures identiques d'affilée avant de traiter, pour ne
        # pas associer le prix de l'objet précédent au nouveau nom (erreur coûteuse sur des
        # montants à 8-9 chiffres).
        "stable_reads": 2,
        # Avec l'intervalle par défaut de 1 s, le clic auto (un objet toutes les ~1-1,5 s) ne
        # laissait souvent le temps que d'une seule lecture par objet, jamais confirmée. L'OCR
        # n'étant relancé que si l'écran change, on peut sonder très souvent.
        "max_poll_seconds": 0.1,
    },
}


def _same_name(a: str, b: str) -> bool:
    """Deux lectures OCR du même nom peuvent différer d'une lettre ou deux ("Juagement" /
    "Jugement") : ce n'est pas un changement d'objet. Tolérance réduite sur les noms courts, où
    deux lettres d'écart peuvent suffire à désigner un autre objet."""
    tolerance = 2 if min(len(a), len(b)) >= 12 else 1
    return _levenshtein(a.lower(), b.lower()) <= tolerance


def parse_amount_kamas(raw_text: str) -> int | None:
    # On garde tous les chiffres du texte capturé, dans l'ordre, en ignorant tout le reste
    # (séparateurs de milliers, icône kamas, artefacts d'OCR...). Un simple re.search sur un
    # motif "chiffres contigus" s'arrête au premier fragment si l'OCR insère un caractère
    # parasite entre deux groupes de chiffres (ex: "280" lu comme "2" + bruit + "80" -> ne
    # récupérait que "2").
    digits = re.sub(r"[^\d]", "", raw_text)
    return int(digits) if digits else None


class ScanLoop(threading.Thread):
    """Tourne dans un thread séparé. `log(str)` est appelé pour chaque ligne à afficher
    (thread appelant = ce thread ScanLoop, jamais le thread principal)."""

    def __init__(self, config: dict, device_id: str, log, confirm=None, mode: str = "resource"):
        super().__init__(daemon=True)
        self.config = config
        self.mode = mode
        self.settings = MODES[mode]
        self.device_id = device_id
        self.log = log
        # `confirm(message) -> bool` est appelé (et attendu de façon bloquante) quand un prix
        # s'écarte trop de la médiane communautaire, pour demander une validation avant envoi.
        # Par défaut (aucune IU capable de demander) : on refuse et on ignore, comme avant.
        self.confirm = confirm or (lambda message: False)
        self.client = CommunityApiClient(config["api_url"], config["api_key"])

        self._paused = threading.Event()
        self._dry_run = threading.Event()
        if config.get("dry_run", True):
            self._dry_run.set()
        self._stop = threading.Event()

        self._last_seen: tuple[str, int] | None = None
        self._candidate: tuple[str, int] | None = None
        self._candidate_count = 0
        self._unreadable_name: str | None = None
        self._last_submitted_at: dict[str, float] = {}
        self._unique_submissions: set[tuple[str, int]] = set()
        self._submissions: "queue.Queue[tuple[str, int]]" = queue.Queue()
        self._last_frame_key: bytes | None = None
        self._last_frame_reading: tuple[str, int | None, str] = ("", None, "")

    def stop(self) -> None:
        self._stop.set()

    def unique_submission_count(self) -> int:
        """Nombre de lignes uniques (resourceId, montant) envoyées avec succès à Cloudflare
        depuis le démarrage de cette boucle, doublons exclus."""
        return len(self._unique_submissions)

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
        if self.settings["max_poll_seconds"] is not None:
            poll = min(poll, self.settings["max_poll_seconds"])
        pytesseract.pytesseract.tesseract_cmd = self.config["tesseract_path"]
        threading.Thread(
            target=self._submission_worker, args=(cooldown, outlier_ratio), daemon=True
        ).start()

        try:
            with mss.mss() as sct:
                while not self._stop.is_set():
                    if self._paused.is_set():
                        time.sleep(0.3)
                        continue
                    self._tick(sct)
                    time.sleep(poll)
        except Exception as exc:  # noqa: BLE001 - on veut logger toute erreur inattendue dans l'UI
            self.log(f"[erreur] {exc}")
        self._stop.set()
        self.log("Scan arrêté.")

    def _same_reading(self, a: tuple[str, int], b: tuple[str, int]) -> bool:
        # Tolérance aux variations d'OCR du nom uniquement en mode "item" (où la stabilité est
        # exigée) : en mode ressource, deux noms proches restent deux ressources distinctes.
        if self.mode != "item":
            return a == b
        return a[1] == b[1] and _same_name(a[0], b[0])

    def _grab_bulk_unit_price(self, sct) -> tuple[int | None, str]:
        """Prix à l'unité retenu parmi les lots en vente (voir `pick_unit_price`). Une seule zone calibrée couvre tout le tableau des lots (colonne Lot + colonne Prix) :
        chaque ligne est lue, et la taille du lot est reconnue dans son premier mot."""
        lots = grab_resource_lots(sct, self.config[self.settings["price_region"]])
        text = " | ".join(f"x{lot}: {total}" for lot, total in sorted(lots.items()))
        unit = pick_unit_price(lots)
        return (max(1, round(unit)) if unit is not None else None), text

    def _frame_key(self, sct) -> bytes:
        """Empreinte des pixels des zones NOM et PRIX : capture d'écran seule (quelques ms),
        sans OCR."""
        settings = self.settings
        price_region = self.config[settings["price_region"]]
        if self.mode == "item":
            extra = int(self.config.get("item_price_extra_height", 24))
            price_region = dict(price_region, height=price_region["height"] + extra)
        shots = (sct.grab(self.config[settings["name_region"]]), sct.grab(price_region))
        return hashlib.blake2b(b"".join(shot.bgra for shot in shots), digest_size=16).digest()

    def _read_screen(self, sct) -> tuple[str, int | None, str]:
        settings = self.settings
        name_text = grab_text(sct, self.config[settings["name_region"]], psm=settings["name_psm"])
        if self.mode == "item":
            amount, price_text = grab_first_price(
                sct,
                self.config[settings["price_region"]],
                int(self.config.get("item_price_extra_height", 24)),
            )
        else:
            amount, price_text = self._grab_bulk_unit_price(sct)
        return name_text, amount, price_text

    def _tick(self, sct) -> None:
        settings = self.settings
        # L'OCR (~0,1-0,2 s par zone) n'est relancé que si l'image a changé : à écran
        # identique, la lecture précédente est réutilisée (et compte comme lecture confirmée).
        # On peut ainsi sonder l'écran très souvent sans coût, et réagir dès le clic.
        frame_key = self._frame_key(sct)
        if frame_key != self._last_frame_key:
            self._last_frame_key = frame_key
            self._last_frame_reading = self._read_screen(sct)
        name_text, amount, price_text = self._last_frame_reading

        if not name_text or amount is None:
            # Lecture ratée (OCR vide, rafraîchissement en cours) : on l'ignore sans oublier la
            # lecture en attente de confirmation. En mode "item", on signale une fois par nom un
            # prix illisible, pour distinguer un objet sans lot en vente (zone vide) d'une
            # erreur d'OCR ou d'une zone mal calibrée (texte parasite).
            if self.mode == "item" and name_text and name_text != self._unreadable_name:
                self._unreadable_name = name_text
                self.log(f"Prix illisible pour '{name_text}' (texte lu : {price_text!r}).")
            return

        current = (name_text, amount)
        if self._candidate is not None and self._same_reading(current, self._candidate):
            self._candidate_count += 1
        else:
            # Objet remplacé à l'écran avant d'avoir été confirmé : on le signale, sinon il
            # disparaît sans trace. Un simple changement de prix pour le même nom (prix pas
            # encore rafraîchi au changement d'objet) est normal et n'est pas signalé.
            previous = self._candidate
            if (
                previous is not None
                and self._candidate_count < settings["stable_reads"]
                and not _same_name(previous[0], name_text)
                and (self._last_seen is None or not _same_name(previous[0], self._last_seen[0]))
            ):
                self.log(f"Lecture instable ignorée : '{previous[0]}' — {previous[1]} kamas (lue une seule fois).")
            self._candidate, self._candidate_count = current, 1
        if self._candidate_count < settings["stable_reads"]:
            return

        if self._last_seen is not None and self._same_reading(current, self._last_seen):
            return
        self._last_seen = current

        if self.mode == "item":
            self.log(f"Lu : '{name_text}' — {amount} kamas")
        else:
            self.log(f"Lu : '{name_text}' — {amount} kamas/u (lots lus : {price_text})")
        # Identification DofusDB, contrôle d'écart et envoi = appels réseau (plusieurs centaines
        # de ms) : faits par le thread d'envoi pour que la lecture de la ressource suivante
        # démarre immédiatement.
        self._submissions.put((name_text, amount))

    def _submission_worker(self, cooldown: float, outlier_ratio: float) -> None:
        while not self._stop.is_set():
            try:
                name_text, amount = self._submissions.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                self._submit(name_text, amount, cooldown, outlier_ratio)
            except Exception as exc:  # noqa: BLE001 - une erreur d'envoi ne doit pas tuer le thread
                self.log(f"[erreur envoi '{name_text}'] {exc}")

    def _submit(self, name_text: str, amount: int, cooldown: float, outlier_ratio: float) -> None:
        label = self.settings["label"]
        resource_id = resolve_resource_id(name_text, kind=self.mode)
        if resource_id is None:
            self.log(f"  -> '{name_text}' : {label} non reconnu(e) avec certitude, ignoré.")
            return

        now = time.monotonic()
        if now - self._last_submitted_at.get(resource_id, 0) < cooldown:
            self.log(f"  -> '{name_text}' : même {label} envoyé(e) récemment, ignoré (cooldown).")
            return

        # Référence pour détecter une erreur de lecture : médiane communautaire pour une
        # ressource, dernier prix envoyé pour un item (le worker ne calcule pas de médiane item).
        if self.mode == "item":
            latest = self.client.get_latest_item_price(resource_id, self.config["server_id"])
            reference = latest.get("amountKamas") if latest else None
            reference_label = "du dernier prix enregistré"
        else:
            median = self.client.get_median(resource_id, self.config["server_id"])
            reference = median.get("medianAmountKamas") if median else None
            reference_label = "de la médiane actuelle"
        if reference:
            ratio = amount / reference
            if ratio > outlier_ratio or ratio < 1 / outlier_ratio:
                message = (
                    f"{name_text} : {amount} très différent {reference_label} "
                    f"({reference}), probable erreur de lecture."
                )
                self.log(f"  -> ATTENTION: {message}")
                if self._dry_run.is_set():
                    self.log("  -> [dry-run] demanderait confirmation avant envoi.")
                elif not self.confirm(message):
                    self.log("  -> envoi annulé (non confirmé).")
                    return
                else:
                    self.log("  -> envoi confirmé malgré l'écart, poursuite.")

        if self._dry_run.is_set():
            self.log(
                f"  -> '{name_text}' : [dry-run] aurait envoyé resourceId={resource_id} "
                f"amountKamas={amount} kind={self.mode}"
            )
            return

        ok = self.client.submit_price(
            resource_id, self.config["server_id"], amount, self.device_id, kind=self.mode
        )
        self.log(f"  -> '{name_text}' : " + ("envoyé avec succès." if ok else "ÉCHEC de l'envoi."))
        if ok:
            self._last_submitted_at[resource_id] = now
            self._unique_submissions.add((resource_id, amount))
