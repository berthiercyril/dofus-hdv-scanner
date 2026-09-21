"""Version console : lit en continu le nom et le prix d'une ressource affichés dans la fenêtre de
prix de l'HDV Dofus (zones calibrées par calibrate.py ou par l'interface graphique gui.py) et
envoie le prix à la base communautaire Dofus-Craft.

Pour une interface graphique à la place, lance `python gui.py`.

Raccourcis pendant l'exécution :
  F8  : mettre en pause / reprendre la lecture
  F9  : basculer dry-run (aucun envoi, juste affiché) / envoi réel
  Échap ou Ctrl+C : quitter
"""
import sys

from pynput import keyboard

from common import get_or_create_device_id, load_config
from scanner_core import ScanLoop


def main() -> None:
    config = load_config()
    if not config.get("server_id") or not config.get("name_region") or not config.get("price_region"):
        print("Configuration incomplète : lance d'abord `python calibrate.py` (ou `python gui.py`).")
        sys.exit(1)

    device_id = get_or_create_device_id(config)
    loop = ScanLoop(config, device_id, log=print)

    print(f"Serveur: {config['server_name']} ({config['server_id']})")
    label = "DRY-RUN (rien n'est envoyé)" if loop.is_dry_run() else "ENVOI RÉEL"
    print(f"Mode initial: {label} (F9 pour basculer, F8 pause, Échap pour quitter)")

    def on_press(key):
        if key == keyboard.Key.f8:
            loop.set_paused(not loop.is_paused())
            print(f"[pause] {'EN PAUSE' if loop.is_paused() else 'reprise'}")
        elif key == keyboard.Key.f9:
            loop.set_dry_run(not loop.is_dry_run())
            label = "DRY-RUN (rien n'est envoyé)" if loop.is_dry_run() else "ENVOI RÉEL activé"
            print(f"[mode] {label}")
        elif key == keyboard.Key.esc:
            print("[quit] arrêt demandé")
            loop.stop()
            return False

    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    loop.start()
    try:
        loop.join()
    except KeyboardInterrupt:
        loop.stop()
        loop.join()
    listener.stop()


if __name__ == "__main__":
    main()
