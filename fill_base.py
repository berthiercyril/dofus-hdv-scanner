#!/usr/bin/env python3
"""
Script d'automatisation avec arrêt rapide (Escape)

Principe : on traite la liste page par page. Depuis la position de départ,
on clique la ligne puis on descend la souris d'une ligne, jusqu'à la dernière
ligne visible de la page. On scroll ensuite d'une page entière et on remet la
souris à la position de départ pour recommencer.
"""

try:
    import pyautogui
    import time
    import keyboard
    import ctypes
    import platform
    import random
except ImportError as e:
    print("\n❌ ERREUR : Une librairie manque !")
    print(f"Erreur : {e}")
    print("\nInstalle les dépendances avec :")
    print("   pip install pyautogui keyboard")
    input("\nAppuyez sur Entrée pour fermer...")
    exit(1)

# Configuration
# Le scanner n'a besoin que de ~0,5 s par objet (OCR du nom + prix, puis une seconde lecture de
# confirmation qui, à écran inchangé, ne coûte qu'un sondage de 0,1 s) : on garde une marge pour
# le rafraîchissement de l'affichage du jeu après le clic.
TIME_MIN = 0.8                  # Délai minimum entre chaque clic (secondes)
TIME_MAX = 1.3                  # Délai maximum entre chaque clic (secondes)
MAX_CLICKS = 10000              # Sécurité : max de clics

# Parcours page par page
ROW_DESCENTS_PER_PAGE = 7        # Descentes de souris par page (donc 8 lignes cliquées)
ROW_STEP_PX = 76                 # Hauteur d'une ligne (px) : 70 px de ligne + 5-6 px de séparateur

# Recalage sur la ligne avant chaque clic : le pas réel (~75,6 px) et un scroll qui ne tombe pas
# pile sur un nombre entier de lignes faisaient dériver la souris jusqu'à cliquer dans le
# séparateur entre deux lignes (rien de sélectionné). On repère les séparateurs (plus sombres que
# le fond d'une ligne) sur une bande verticale autour du point visé et on clique au centre de la
# ligne la plus proche.
ROW_GAP_COLOR = (27, 29, 50)     # Couleur du séparateur entre deux lignes de la liste
ROW_GAP_TOLERANCE = 8            # Écart max par canal pour considérer un pixel comme séparateur
ROW_SNAP_HALF_HEIGHT = 100       # Demi-hauteur de la bande analysée (> 1 ligne complète)
ROW_SNAP_SAMPLE_XS = (-60, -30, 0, 30, 60)  # Points testés horizontalement autour de la souris
PAGE_SCROLL_NOTCHES = 12         # Crans de molette pour passer à la page suivante

# Incertitude sur les clics : décalage aléatoire autour du point visé (la ligne fait ~70 px de
# haut, on reste donc bien à l'intérieur), durée de déplacement et pause avant le clic variables.
CLICK_JITTER_X_PX = 40           # Décalage horizontal max (±px)
CLICK_JITTER_Y_PX = 12           # Décalage vertical max (±px), à garder < moitié de ligne
CLICK_MOVE_DURATION = (0.08, 0.25)   # Durée du déplacement de la souris vers le clic (s)
CLICK_PRE_DELAY = (0.03, 0.12)       # Pause entre l'arrivée de la souris et le clic (s)

# Détection de fin de liste : zone capturée à la position de départ avant et après le scroll de
# page. Si elle est identique, le scroll n'a rien fait : on est en bas de la liste.
END_OF_LIST_REGION_SIZE = (260, 26)  # (largeur, hauteur) en pixels
END_OF_LIST_RECHECK_DELAY = 0.5      # Seconde vérification avant d'arrêter (animation du scroll)

pyautogui.FAILSAFE = True  # Ctrl+Alt+Del pour arrêter

# ---- Scroll bas niveau (Windows) : plus compatible avec les jeux ----
IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    PUL = ctypes.POINTER(ctypes.c_ulong)

    class MouseInput(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long),
                    ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_long),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", PUL)]

    class InputUnion(ctypes.Union):
        _fields_ = [("mi", MouseInput)]

    class Input(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("ii", InputUnion)]

    MOUSEEVENTF_WHEEL = 0x0800
    WHEEL_DELTA = 120

    def real_scroll(notches: int):
        """Scroll via SendInput (API Windows bas niveau, compatible jeux)"""
        extra = ctypes.c_ulong(0)
        ii = InputUnion()
        ii.mi = MouseInput(0, 0, notches * WHEEL_DELTA, MOUSEEVENTF_WHEEL, 0, ctypes.pointer(extra))
        inp = Input(ctypes.c_ulong(0), ii)
        ctypes.windll.user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
else:
    def real_scroll(notches: int):
        """Fallback pour Mac/Linux"""
        pyautogui.scroll(notches * 10)

def interruptible_sleep(duration: float, step: float = 0.05) -> bool:
    """Dort par petits pas en surveillant ESC. Retourne True si interrompu."""
    elapsed = 0.0
    while elapsed < duration:
        if keyboard.is_pressed('esc'):
            return True
        time.sleep(min(step, duration - elapsed))
        elapsed += step
    return False

def capture_region_snapshot(x: int, y: int) -> bytes:
    """Capture les pixels autour de (x, y) pour détecter si la liste a visuellement changé."""
    width, height = END_OF_LIST_REGION_SIZE
    left = max(0, x - width // 2)
    top = max(0, y - height // 2)
    shot = pyautogui.screenshot(region=(left, top, width, height))
    return shot.tobytes()

def snap_to_row(x: int, y: int) -> int:
    """Renvoie l'ordonnée du centre de la ligne de liste la plus proche de (x, y). Si aucun
    séparateur n'est trouvé (souris hors de la liste, couleurs différentes), renvoie y inchangé."""
    top = max(0, y - ROW_SNAP_HALF_HEIGHT)
    left = max(0, x + min(ROW_SNAP_SAMPLE_XS))
    width = max(ROW_SNAP_SAMPLE_XS) - min(ROW_SNAP_SAMPLE_XS) + 1
    shot = pyautogui.screenshot(region=(left, top, width, 2 * ROW_SNAP_HALF_HEIGHT))
    px = shot.load()

    def is_gap_pixel(color) -> bool:
        return all(abs(c - g) <= ROW_GAP_TOLERANCE for c, g in zip(color[:3], ROW_GAP_COLOR))

    # Une ligne de pixels est un séparateur si presque tous les points testés en ont la couleur
    # (un seul point sombre peut être le contour d'une icône à l'intérieur d'une ligne).
    xs = [dx - min(ROW_SNAP_SAMPLE_XS) for dx in ROW_SNAP_SAMPLE_XS]
    gap = [sum(is_gap_pixel(px[sx, sy]) for sx in xs) >= len(xs) - 1 for sy in range(shot.height)]

    # Lignes de liste complètes = segments non-séparateurs bornés par un séparateur des deux côtés.
    rows, start = [], None
    for sy in range(1, shot.height):
        if gap[sy - 1] and not gap[sy]:
            start = sy
        elif not gap[sy - 1] and gap[sy] and start is not None:
            if sy - start >= 30:  # ignore les petits segments parasites
                rows.append((start, sy - 1))
            start = None
    if not rows:
        return y

    target = y - top
    center_top, center_bottom = min(rows, key=lambda r: abs((r[0] + r[1]) / 2 - target))
    return top + (center_top + center_bottom) // 2


def jittered_click(x: int, y: int) -> tuple[int, int]:
    """Clique près de (x, y) avec un décalage aléatoire (distribution centrée, plus probable
    près du point visé) et un déplacement de souris de durée variable. Renvoie le point cliqué."""
    def offset(max_px: int) -> int:
        return round(max(-max_px, min(max_px, random.gauss(0, max_px / 2))))

    cx, cy = x + offset(CLICK_JITTER_X_PX), y + offset(CLICK_JITTER_Y_PX)
    pyautogui.moveTo(cx, cy, duration=random.uniform(*CLICK_MOVE_DURATION),
                     tween=pyautogui.easeOutQuad)
    time.sleep(random.uniform(*CLICK_PRE_DELAY))
    pyautogui.click(cx, cy)
    return cx, cy


def scroll_down(x: int, y: int, notches: int) -> bool:
    """Positionne la souris et scroll vers le bas, cran par cran avec pause
    pour laisser l'animation de la liste se stabiliser. Retourne True si ESC
    a été détecté en cours de route."""
    if notches <= 0:
        return False
    pyautogui.moveTo(x, y)
    if interruptible_sleep(0.1):
        return True
    for _ in range(notches):
        real_scroll(-1)  # -1 = vers le bas
        if interruptible_sleep(0.15):
            return True
    return interruptible_sleep(0.2)

def main():
    print("=" * 70)
    print("🤖 CLIQUEUR AUTOMATIQUE")
    print("=" * 70)
    print(f"\n⚙️  Configuration :")
    print(f"   • Délai entre clics : {TIME_MIN}s - {TIME_MAX}s (aléatoire)")
    print(f"   • Incertitude des clics : ±{CLICK_JITTER_X_PX}px en X, ±{CLICK_JITTER_Y_PX}px en Y")
    print(f"   • Page : {ROW_DESCENTS_PER_PAGE + 1} lignes de {ROW_STEP_PX}px, puis scroll de {PAGE_SCROLL_NOTCHES} crans")
    print(f"\n📍 MODE D'EMPLOI :")
    print(f"   1️⃣  Placez la souris sur la PREMIÈRE ligne de la liste")
    print(f"   2️⃣  Le script démarre dans 5 secondes")
    print(f"   3️⃣  Clic → descente d'1 ligne (x{ROW_DESCENTS_PER_PAGE}) → scroll d'1 page → retour en haut...")
    print(f"   • Méthode de scroll : {'SendInput (Windows)' if IS_WINDOWS else 'pyautogui (fallback)'}")
    print(f"\n⚠️  IMPORTANT : Gardez la fenêtre visible et en focus")
    print(f"🛑 ARRÊTER : Appuyez sur ESC (ou Ctrl+Alt+Del)")
    print("=" * 70)

    # Attendre et récupérer la position initiale
    print("\n⏳ Positionnez votre souris et attendez 5 secondes...\n")
    time.sleep(5)

    fixed_x, start_y = pyautogui.position()
    print(f"✅ Position de départ : ({fixed_x}, {start_y})")
    print("🚀 Démarrage dans 1 seconde...")
    print("⚠️  Appuyez sur ESC pour arrêter\n")
    time.sleep(1)

    clicks = 0
    row = 0
    current_y = start_y
    page_start_snapshot = capture_region_snapshot(fixed_x, start_y)

    try:
        while clicks < MAX_CLICKS:
            # Vérifier si ESC est pressé
            if keyboard.is_pressed('esc'):
                print("\n\n⛔ Arrêt (ESC pressé)")
                break

            snapped_y = snap_to_row(fixed_x, current_y)
            if snapped_y != current_y:
                print(f"   ↕ Recalage sur la ligne : {current_y} -> {snapped_y}")
                current_y = snapped_y
            click_x, click_y = jittered_click(fixed_x, current_y)
            clicks += 1
            wait_time = random.uniform(TIME_MIN, TIME_MAX)
            print(f"✓ Clic #{clicks:4d} (ligne {row + 1}, ({click_x}, {click_y}), attente {wait_time:.2f}s)")

            if interruptible_sleep(wait_time):
                print("\n\n⛔ Arrêt (ESC pressé)")
                break

            if row < ROW_DESCENTS_PER_PAGE:
                row += 1
                current_y += ROW_STEP_PX
                print(f"   ↓ Souris descendue de {ROW_STEP_PX}px ({row}/{ROW_DESCENTS_PER_PAGE})")
                if interruptible_sleep(0.2):
                    print("\n\n⛔ Arrêt (ESC pressé)")
                    break
                continue

            # Dernière ligne de la page cliquée : scroll d'une page puis retour en haut.
            print(f"   ↓ Scroll {PAGE_SCROLL_NOTCHES} crans, retour à la position de départ")
            if scroll_down(fixed_x, current_y, PAGE_SCROLL_NOTCHES):
                print("\n\n⛔ Arrêt (ESC pressé)")
                break
            row = 0
            current_y = start_y
            pyautogui.moveTo(fixed_x, start_y)

            # Détection de fin de liste : la première ligne n'a pas changé malgré le scroll.
            new_snapshot = capture_region_snapshot(fixed_x, start_y)
            if new_snapshot == page_start_snapshot:
                if interruptible_sleep(END_OF_LIST_RECHECK_DELAY):
                    print("\n\n⛔ Arrêt (ESC pressé)")
                    break
                new_snapshot = capture_region_snapshot(fixed_x, start_y)
                if new_snapshot == page_start_snapshot:
                    print("\n\n🏁 Fin de liste détectée (le scroll n'a rien changé) — arrêt automatique.")
                    break
            page_start_snapshot = new_snapshot

    except KeyboardInterrupt:
        print("\n\n⛔ Arrêt (Ctrl+C)")
    except Exception as e:
        print(f"\n\n❌ Erreur : {e}")

    print("\n" + "=" * 70)
    print(f"✅ RÉSUMÉ : {clicks} clics effectués avec succès")
    print("=" * 70)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERREUR : {e}")
        print(f"Type d'erreur : {type(e).__name__}")
        input("\nAppuyez sur Entrée pour fermer...")
