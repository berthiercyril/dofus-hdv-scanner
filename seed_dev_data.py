"""Script de remplissage de la base Cloudflare de dev avec des données générées/fictives.

Déclenché depuis l'interface (bouton "Remplir base (dev)"). À compléter : pour l'instant
ce script ne fait qu'un test de bout en bout (log + code de sortie) tant que la logique
d'insertion n'est pas écrite.

IMPORTANT : ne pointer que vers une base de dev/test perso (jamais l'URL du worker
communautaire de production utilisée par les autres joueurs, cf. config.json / api_url).
"""
import sys


def main() -> int:
    print("[seed_dev_data] TODO : générer des données fictives et les insérer dans la base de dev.")
    print("[seed_dev_data] Rien n'a été inséré, ce script est un stub.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
