"""Résolution d'un nom de ressource lu par OCR vers l'identifiant DofusDB utilisé comme
resourceId par l'API communautaire de Dofus-Craft (voir src/services/dofusdb-api.ts)."""
import re
import unicodedata

import requests

API_URL = "https://api.dofusdb.fr"
_cache: dict[str, str | None] = {}
# Session persistante : réutilise la même connexion (TCP + TLS) entre les appels au lieu de
# renégocier à chaque requête, plus rapide quand on enchaîne beaucoup de résolutions de suite.
_session = requests.Session()


def _normalize(value: str) -> str:
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", stripped).strip().lower()


def _strip_ocr_noise(value: str) -> str:
    """Enlève les caractères parasites (virgule, point, guillemet...) que l'OCR ajoute parfois
    en début/fin de lecture (ex: ", Aile de Gruche"). Un nom de ressource commence et finit
    toujours par une lettre ou un chiffre, jamais par de la ponctuation isolée."""
    return value.strip(" \t\r\n,.;:!?'\"`|_-")


# Les noms DofusDB n'utilisent jamais le caractère ligaturé "œ"/"Æ", toujours les deux lettres
# séparées ("oe", "ae" - ex: "Dragoeuf", "Chaloeil"). Si l'OCR reconnaît la ligature du jeu comme
# ce caractère unique, aucune correspondance n'est possible tant qu'on ne l'éclate pas en amont.
_LIGATURES = {"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE"}


def _expand_ligatures(value: str) -> str:
    for ligature, expansion in _LIGATURES.items():
        value = value.replace(ligature, expansion)
    return value


# Variantes accentuées possibles pour chaque voyelle (accent grave/aigu/circonflexe/tréma sont
# visuellement proches en petite police : l'OCR se trompe parfois d'accent, ex. "ébéne" au lieu
# de "ébène"). Le nom stocké sur DofusDB est en une seule forme précise (ex. "è"), donc une
# requête qui exige l'accent exact lu par l'OCR peut échouer même quand le mot est le bon.
# "i"/"l" sont regroupés en plus : dans la police du jeu, un "I" majuscule (ex: OCR lit "I'" au
# lieu de "l'" dans "l'Hyperscampe") est visuellement identique à un "l" minuscule.
_ACCENT_VARIANTS = {
    "a": "aàâä",
    "e": "eéèêë",
    "i": "iîïl",
    "l": "ilîï",
    "o": "oôö",
    "u": "uùûü",
    "y": "yÿ",
    "c": "cç",
}


def _fuzzy_accent_pattern(value: str) -> str:
    """Construit un motif regex qui accepte les confusions d'accent et de i/l sur chaque lettre
    de `value`, sans tolérer d'autre différence. Nécessite que `value` soit déjà en forme NFC (un
    caractère Python par lettre accentuée), sinon la décomposition NFD la scinderait en deux
    caractères."""
    parts = []
    for char in value:
        base = "".join(
            c for c in unicodedata.normalize("NFD", char) if unicodedata.category(c) != "Mn"
        )
        variants = _ACCENT_VARIANTS.get(base.lower())
        parts.append(f"[{variants}]" if variants else re.escape(char))
    return "".join(parts)


def _levenshtein(a: str, b: str) -> int:
    """Distance d'édition (nombre minimal d'insertions/suppressions/substitutions pour passer de
    `a` à `b`). Utilisée pour rattraper les erreurs d'OCR qui changent la longueur du texte (ex:
    "û" lu comme "ii"), que la substitution caractère par caractère de `_fuzzy_accent_pattern` ne
    peut pas couvrir."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[-1]


_MAX_FUZZY_DISTANCE = 2  # ex: "û" lu "ii" par l'OCR = 1 suppression + 1 substitution = distance 2

# Longueur d'ancre utilisée pour la recherche élargie côté serveur (voir _fallback_fuzzy_match).
# Volontairement plus courte que le mot complet : DofusDB compare le regex caractère par
# caractère (une classe par lettre), donc un mot entier ne matche jamais si l'OCR a fusionné ou
# éclaté un caractère (ex: ligature "œ" lue comme "ce" ou "oe" -> longueur différente). Un préfixe
# court garde assez de candidats pour laisser le tri par distance de Levenshtein trancher ensuite.
_FALLBACK_ANCHOR_LENGTH = 6


def _is_resource_item(item: dict) -> bool:
    """True si l'item DofusDB est bien une ressource d'artisanat (catégorie 'Ressource'), par
    opposition à un objet de quête/cosmétique/équipement qui porte parfois exactement le même nom
    (ex: 'Carapace Bleue' existe à la fois comme ressource et comme objet de quête d'une île)."""
    super_type = item.get("type", {}).get("superType") or {}
    return super_type.get("name", {}).get("fr") == "Ressource"


def _matches_kind(item: dict, kind: str) -> bool:
    """`kind` = "resource" (fenêtre de prix d'une ressource) ou "item" (équipement, arme, dofus,
    trophée...). Sert uniquement à départager des homonymes."""
    return _is_resource_item(item) if kind == "resource" else not _is_resource_item(item)


def _is_eligible(item: dict, kind: str) -> bool:
    """En mode "item", un objet non échangeable ne peut pas être en vente à l'HDV : on l'écarte
    d'office (ex: deux épées "Crocobur" niveau 100 non échangeables homonymes d'un objet vendu)."""
    return kind != "item" or item.get("exchangeable", True) is not False


def _one_typo_pattern(word: str) -> str:
    """Motif qui accepte `word` avec UN caractère quelconque substitué, n'importe où. Rattrape une
    erreur d'OCR située dans les premières lettres du mot, que l'ancre par préfixe ne voit pas
    (ex: "Rtoaraf" lu pour "Rtograf", "Juagement" pour "Jugement")."""
    return "|".join(
        _fuzzy_accent_pattern(word[:i]) + "." + _fuzzy_accent_pattern(word[i + 1 :])
        for i in range(len(word))
    )


def _fetch_candidates(patterns: list[str], timeout: float) -> list[dict]:
    """Interroge DofusDB pour chaque motif et renvoie l'union des objets trouvés (sans doublon)."""
    found: dict[int, dict] = {}
    for pattern in patterns:
        params = {"$limit": 80, "name.fr[$regex]": pattern, "name.fr[$options]": "i"}
        try:
            response = _session.get(f"{API_URL}/items", params=params, timeout=timeout)
            response.raise_for_status()
            data = response.json().get("data", [])
        except (requests.RequestException, ValueError):
            continue
        for item in data:
            found.setdefault(item["id"], item)
    return list(found.values())


def _fallback_fuzzy_match(cleaned: str, key: str, timeout: float, kind: str) -> str | None:
    """Repli quand la correspondance exacte/à accent près échoue : élargit la recherche DofusDB
    autour des mots du nom lu, puis choisit par distance d'édition (au plus `_MAX_FUZZY_DISTANCE`
    caractères d'écart), seulement si un unique candidat est strictement le plus proche. Ne devine
    jamais si plusieurs candidats sont à égalité, ou si le meilleur est trop éloigné.

    Chaque mot d'au moins 4 lettres sert d'ancre, du plus long au plus court, de deux façons : son
    préfixe (tolère une lettre en trop/en moins plus loin dans le mot) et le mot entier avec une
    lettre quelconque substituée (tolère une erreur dans le préfixe lui-même). Ancrer sur un seul
    mot échouait dès que l'erreur d'OCR tombait justement dans celui-là. La recherche n'est pas
    ancrée en début de nom (pas de "^") car ce mot peut être n'importe où dans le nom complet."""
    words = sorted({w for w in cleaned.split(" ") if len(w) >= 4}, key=len, reverse=True)
    if not words:
        return None
    patterns = []
    for word in words:
        patterns.append(_fuzzy_accent_pattern(word[:_FALLBACK_ANCHOR_LENGTH]))
        patterns.append(_one_typo_pattern(word))
    data = [item for item in _fetch_candidates(patterns, timeout) if _is_eligible(item, kind)]

    scored = []
    for item in data:
        name = item.get("name", {}).get("fr") or ""
        distance = _levenshtein(_normalize(name), key)
        if distance <= _MAX_FUZZY_DISTANCE:
            scored.append((distance, item))
    if not scored:
        return None

    best_distance = min(distance for distance, _ in scored)
    best_items = [item for distance, item in scored if distance == best_distance]
    if len(best_items) != 1:
        # Plusieurs candidats à égalité : si un seul est du type attendu (vraie ressource
        # d'artisanat, ou équipement en mode "item"), on le préfère.
        kind_items = [item for item in best_items if _matches_kind(item, kind)]
        if len(kind_items) != 1:
            return None
        return str(kind_items[0]["id"])
    return str(best_items[0]["id"])


def resolve_resource_id(ocr_name: str, timeout: float = 5.0, kind: str = "resource") -> str | None:
    """Retourne l'id DofusDB (resourceId) correspondant au nom lu, ou None si aucune
    correspondance fiable n'est trouvée. Tolère les confusions d'accent, i/l, et jusqu'à 1
    caractère inséré/supprimé/substitué par rapport à un nom DofusDB existant, mais ne devine
    jamais quand plusieurs ressources sont candidates. `kind="item"` pour un équipement : même
    logique, mais un homonyme est départagé en faveur de l'objet qui n'est pas une ressource."""
    # DofusDB compare le regex reçu octet par octet : un accent "composé" (é) et le même accent
    # "décomposé" (e + accent séparé) sont visuellement identiques mais ne matchent pas entre eux
    # côté serveur. Tesseract peut renvoyer l'une ou l'autre forme selon les cas : on force donc
    # la forme composée (NFC), celle utilisée par les noms stockés sur DofusDB.
    cleaned = _expand_ligatures(unicodedata.normalize("NFC", _strip_ocr_noise(ocr_name)))
    key = _normalize(cleaned)
    if not key:
        return None
    cache_key = f"{kind}:{key}"
    if cache_key in _cache:
        return _cache[cache_key]

    pattern = _fuzzy_accent_pattern(cleaned)
    params = {
        "$limit": 10,
        "name.fr[$regex]": f"^{pattern}$",
        "name.fr[$options]": "i",
    }
    try:
        response = _session.get(f"{API_URL}/items", params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json().get("data", [])
    except (requests.RequestException, ValueError):
        return None

    # On revérifie côté client avec le même motif flou (plutôt qu'une simple égalité de chaînes
    # normalisées) pour que la tolérance i/l et accents ait bien un effet sur la décision finale,
    # pas seulement sur la requête envoyée à DofusDB.
    compiled = re.compile(pattern, re.IGNORECASE)
    matches = [
        item
        for item in data
        if compiled.fullmatch(item.get("name", {}).get("fr") or "") and _is_eligible(item, kind)
    ]

    if len(matches) == 1:
        resource_id = str(matches[0]["id"])
    elif len(matches) == 0:
        resource_id = _fallback_fuzzy_match(cleaned, key, timeout, kind)
    else:
        # Plusieurs objets partagent exactement ce nom (ex: une ressource d'artisanat et un objet
        # de quête homonyme) : on ne devine pas entre plusieurs ressources, mais si un seul des
        # candidats est effectivement une ressource, l'ambiguïté n'en est pas une pour ce script.
        # En mode "item", c'est l'inverse : on garde l'unique candidat qui n'est pas une ressource.
        kind_matches = [item for item in matches if _matches_kind(item, kind)]
        resource_id = str(kind_matches[0]["id"]) if len(kind_matches) == 1 else None

    _cache[cache_key] = resource_id
    return resource_id
