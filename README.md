# Dofus HDV Scanner

Outil personnel : lit en continu le nom et le prix d'une ressource affichés dans la fenêtre de
prix de l'Hôtel de Vente du jeu Dofus, et envoie le prix à la base communautaire utilisée par
Dofus-Craft (même API que le site web).

Ce n'est pas une page web : c'est un script Python à lancer sur Windows, en même temps que le jeu.

## Installation (une seule fois)

1. **Configuration** : copie `config.example.json` en `config.json` et remplace `api_key` par la
   valeur de `VITE_COMMUNITY_API_KEY` du fichier `.env.local` du projet Dofus-Craft. `config.json`
   est volontairement exclu du dépôt Git (`.gitignore`) car il contient cette clé et, une fois
   calibré, tes réglages personnels.
2. **Python** : déjà installé chez toi. Ouvre une invite de commande dans ce dossier
   (`D:\Outils\dofus-hdv-scanner`) et installe les dépendances :
   ```
   pip install -r requirements.txt
   ```
2. **Tesseract OCR** (moteur de reconnaissance de texte, nécessaire en plus des paquets Python) :
   - Télécharge et installe la version Windows depuis le dépôt officiel UB-Mannheim :
     https://github.com/UB-Mannheim/tesseract/wiki
   - Installe-le avec les options par défaut (chemin par défaut :
     `C:\Program Files\Tesseract-OCR\tesseract.exe`).
   - Si tu l'installes ailleurs, mets à jour `"tesseract_path"` dans `config.json`.

## Interface graphique (recommandé)

```
python gui.py
```

Une fenêtre s'ouvre avec :
- un champ pour le nom du serveur (bouton "Enregistrer"),
- deux boutons "Calibrer" (zone NOM et zone PRIX de la ressource) : clique, place ensuite la
  souris en jeu sur le coin HAUT-GAUCHE de la zone puis appuie sur **F10**, puis fais pareil pour
  le coin BAS-DROIT. Le jeu garde le focus, seule la position de la souris est lue (**Échap**
  annule un calibrage en cours),
- les réglages (intervalle de lecture, délai anti-doublon, seuil d'alerte sur les écarts de prix),
- un bouton Démarrer/Arrêter, une case "Dry-run" et une case "En pause",
- un journal en direct de tout ce que l'outil lit et fait (ou ignore, et pourquoi).

Comme la fenêtre du jeu garde le focus pendant que tu joues, **F8** (pause/reprise) et **F9**
(dry-run/envoi réel) fonctionnent aussi en raccourcis globaux, même quand la fenêtre de l'outil
n'est pas au premier plan.

## Calibrage en ligne de commande (alternative à l'interface graphique)

1. Lance le jeu, ouvre l'Hôtel de Vente sur une ressource dont le prix est affiché.
2. Dans une invite de commande, dans ce dossier :
   ```
   python calibrate.py
   ```
3. Réponds au nom de ton serveur, puis suis les instructions à l'écran : tu places la souris sur
   les coins de la zone où s'affiche le NOM de la ressource, puis sur les coins de la zone où
   s'affiche le PRIX. Le jeu peut rester au premier plan pendant ce temps, seule la position de la
   souris est lue (aucun clic n'est envoyé au jeu).

## Utilisation en ligne de commande (alternative à l'interface graphique)

```
python dofus_hdv_scanner.py
```

- Il démarre toujours en **dry-run** (`"dry_run": true` dans `config.json`) : rien n'est envoyé à
  la base communautaire, tout est seulement affiché dans la console. Regarde d'abord quelques
  lectures pour vérifier que le nom et le prix sont bien reconnus avant d'activer l'envoi réel.
- **F9** : bascule entre dry-run et envoi réel, à tout moment.
- **F8** : met en pause / reprend la lecture (utile si tu quittes l'HDV).
- **Échap** ou **Ctrl+C** : arrête le script.

Le script ignore et signale (sans jamais envoyer) :
- une lecture où le nom de ressource ne correspond à aucun objet Dofus-Craft connu avec certitude,
- une lecture où aucun prix numérique n'est trouvé.

Si un prix diffère trop fortement (par défaut plus de 3x, réglable via `outlier_ratio_warning`
dans `config.json`) du prix médian communautaire actuel pour cette ressource — signe probable
d'une erreur de lecture (ex : un chiffre manqué par l'OCR) — l'envoi n'est **pas** automatique :
- en mode envoi réel, l'outil demande confirmation avant d'envoyer (une question oui/non en
  console, une boîte de dialogue dans l'interface graphique) ; sans confirmation, la lecture est
  ignorée,
- en dry-run, l'écart est seulement signalé dans le journal (aucune confirmation demandée, rien
  n'est envoyé de toute façon).

## Équipements (armes, équipements, dofus, trophées)

Même principe, mais pour l'HDV des équipements : l'outil lit le nom de l'objet sélectionné (en
haut du panneau de détail, à gauche) et **uniquement le prix le plus bas**, c'est-à-dire celui de
la première ligne de la liste des lots.

1. Ouvre l'HDV des équipements et sélectionne un objet pour afficher son panneau de détail.
2. Calibre les zones (une seule fois, elles sont séparées de celles des ressources) :
   ```
   python calibrate.py --items
   ```
   - zone NOM : le nom de l'objet en haut du panneau (ex : « Jugement de Thanatena »), sans la
     ligne « Niv. 200 • Faux » en dessous,
   - zone PRIX : uniquement le prix de la **première** ligne (ex : « 109 999 999 »), sans la
     colonne Lot, ni l'icône kamas, ni le bouton Acheter. **Calibre-la sur un objet sans
     panoplie** : pour un objet de panoplie, la ligne « Panoplie des … » décale la liste des lots
     d'environ 18 px vers le bas, et l'outil cherche automatiquement le premier prix jusqu'à
     `item_price_extra_height` pixels (24 par défaut, dans `config.json`) sous la zone calibrée.
3. Lance :
   ```
   python dofus_hdv_items.py
   ```

Dans l'interface graphique (`python gui.py`), choisis simplement « Équipements » dans le cadre
« Type d'HDV » : les boutons Calibrer et Démarrer utilisent alors les zones des équipements (le
choix est mémorisé, et verrouillé pendant une lecture).

Mêmes raccourcis (F8, F9, Échap), même dry-run par défaut, même contrôle d'écart avec la médiane.
Différence : une lecture n'est prise en compte que si elle est identique deux fois de suite, pour
ne pas associer le prix de l'objet précédent au nouveau nom pendant le rafraîchissement de
l'interface.

## Fichier `config.json`

Contient notamment ta clé d'API communautaire Dofus-Craft en clair : ce fichier reste uniquement
sur ta machine, ne le partage pas et ne le mets pas dans un dépôt Git public.

- `server_id` / `server_name` : ton serveur Dofus (calculés par `calibrate.py`).
- `name_region` / `price_region` : zones d'écran calibrées. `price_region` couvre tout le tableau
  des lots (colonnes Lot et Prix des lignes 1, 10, 100, 1000). Le prix envoyé est la valeur du
  milieu des prix à l'unité des lots en vente (médiane ; avec 2 lots, le plus gros sauf s'il
  dépasse le double de l'autre), pour écarter un lot de 1 trop bas comme un gros lot trop haut.
- `poll_interval_seconds` : fréquence de lecture de l'écran (1 seconde par défaut).
- `submission_cooldown_seconds` : délai minimum entre deux envois pour la même ressource, pour
  éviter d'envoyer plusieurs fois la même valeur en boucle.
- `dry_run` : `true` par défaut (aucun envoi réel).

## Limites connues

- Doit être relancé/recalibré si tu changes la résolution du jeu, le mode fenêtré/plein écran, ou
  l'interface (mods graphiques).
- La reconnaissance du nom de ressource est volontairement stricte (correspondance exacte,
  insensible aux accents/majuscules) : en cas de doute, la lecture est ignorée plutôt que
  d'envoyer une ressource incorrecte.
- Ce script n'a pas été testé en conditions réelles contre le jeu (impossible depuis
  l'environnement où il a été écrit) : le calibrage des zones d'écran et éventuellement la
  configuration Tesseract (langue, `--psm`) devront être ajustés en le lançant réellement pendant
  que tu joues.
