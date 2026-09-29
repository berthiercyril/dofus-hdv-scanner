"""Calibrage à lancer une fois (ou à chaque changement de résolution/fenêtre du jeu).

Configure :
  - le serveur (server_id envoyé à l'API, identique à celui utilisé par le site Dofus-Craft)
  - la zone d'écran où apparaît le NOM de la ressource dans la fenêtre de prix de l'HDV
  - la zone d'écran où apparaît le PRIX de la ressource dans cette même fenêtre

Pour chaque zone, tu places la souris sur le coin haut-gauche puis appuies sur Entrée dans cette
fenêtre de commande, puis tu as 3 secondes pour placer la souris sur le coin bas-droit avant la
capture automatique de la position. Le jeu peut rester au premier plan pendant ce temps : seule la
position de la souris est lue, aucun clic n'est envoyé.

`python calibrate.py --items` calibre à la place les zones de l'HDV des équipements (nom de
l'objet + prix du lot le moins cher), utilisées par dofus_hdv_items.py.
"""
import sys
import time

from pynput.mouse import Controller

from common import load_config, save_config, slugify_server_name

mouse = Controller()


def wait_for_point(label: str) -> tuple[int, int]:
    input(f"Place la souris sur {label}, puis appuie sur Entrée ici...")
    for remaining in (3, 2, 1):
        print(f"  capture dans {remaining}...")
        time.sleep(1)
    pos = mouse.position
    print(f"  -> point capturé: {pos}")
    return int(pos[0]), int(pos[1])


def calibrate_region(region_label: str) -> dict:
    print(f"\n=== Zone : {region_label} ===")
    left, top = wait_for_point("le coin HAUT-GAUCHE de la zone")
    right, bottom = wait_for_point("le coin BAS-DROIT de la zone")
    width = max(1, right - left)
    height = max(1, bottom - top)
    region = {"left": left, "top": top, "width": width, "height": height}
    print(f"  Zone enregistrée: {region}")
    return region


def main() -> None:
    config = load_config()

    print("=== Serveur ===")
    current = config.get("server_name")
    prompt = f"Nom du serveur Dofus (actuel: {current}) : " if current else "Nom du serveur Dofus : "
    server_name = input(prompt).strip() or current
    if not server_name:
        raise SystemExit("Nom de serveur requis.")
    server_id = slugify_server_name(server_name)
    print(f"server_id calculé: {server_id}")
    config["server_name"] = server_name
    config["server_id"] = server_id

    if "--items" in sys.argv:
        config["item_name_region"] = calibrate_region(
            "la zone où s'affiche le NOM de l'objet, en haut du panneau de détail (à gauche de l'HDV)"
        )
        config["item_price_region"] = calibrate_region(
            "la zone du PRIX de la PREMIÈRE ligne de lots uniquement (le moins cher, en haut), "
            "sans la colonne Lot, ni l'icône kamas, ni le bouton Acheter. À faire sur un objet "
            "SANS panoplie (le décalage d'une ligne des objets de panoplie est géré automatiquement)"
        )
        script = "dofus_hdv_items.py"
    else:
        config["name_region"] = calibrate_region(
            "la zone où s'affiche le NOM de la ressource dans la fenêtre de prix de l'HDV"
        )
        # Le prix envoyé est celui du plus gros lot disponible ramené à l'unité (100, sinon 10,
        # sinon 1) : acheter en volume au prix du lot de 1 est rarement possible.
        config["price_region"] = calibrate_region(
            "le TABLEAU DES LOTS de cette même fenêtre : les 4 lignes (1, 10, 100, 1000), colonne "
            "Lot et colonne Prix, sans le bouton Acheter. À faire sur une ressource dont les 4 "
            "lots sont en vente"
        )
        script = "dofus_hdv_scanner.py"

    save_config(config)
    print("\nCalibrage enregistré dans config.json.")
    print(f"Lance ensuite `python {script}` (il démarre en dry-run: rien n'est envoyé, "
          "seulement affiché, tant que tu n'as pas vérifié que la lecture est fiable).")


if __name__ == "__main__":
    main()
