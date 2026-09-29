"""Interface graphique du Dofus HDV Scanner.

Lance `python gui.py`. Permet de calibrer les zones d'écran, de démarrer/arrêter la lecture, de
basculer dry-run/envoi réel et de suivre les lectures en direct, sans passer par la console.

Le jeu garde le focus pendant que tu joues : les raccourcis globaux restent donc disponibles
même quand la fenêtre de l'outil n'est pas au premier plan :
  F8  : pause / reprise de la lecture
  F9  : bascule dry-run / envoi réel
  F10 : valide un point pendant un calibrage en cours
  Échap : annule un calibrage en cours
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from pynput import keyboard
from pynput.mouse import Controller as MouseController

from common import get_or_create_device_id, load_config, save_config, slugify_server_name
from scanner_core import MODES, ScanLoop

mouse = MouseController()

SEED_DEV_SCRIPT = Path(__file__).parent / (
    "fill_base.exe" if getattr(sys, "frozen", False) else "fill_base.py"
)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Dofus HDV Scanner")
        self.root.geometry("640x760")

        self.config = load_config()
        self.device_id = get_or_create_device_id(self.config)
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.loop: ScanLoop | None = None

        self.calibration_target: str | None = None  # "name" | "price" | None
        self.calibration_step: str | None = None  # "top_left" | "bottom_right"
        self.calibration_point1: tuple[int, int] | None = None
        self.seed_process: subprocess.Popen | None = None
        self._seed_started_scan = False
        self._seed_output_lines: list[str] = []

        self._build_widgets()
        self._refresh_calibration_labels()

        self.keyboard_listener = keyboard.Listener(on_press=self._on_key)
        self.keyboard_listener.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._drain_log_queue)

    # --- Construction de l'interface -------------------------------------------------

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        server_frame = ttk.LabelFrame(self.root, text="Serveur")
        server_frame.pack(fill="x", **pad)
        ttk.Label(server_frame, text="Nom du serveur :").grid(row=0, column=0, sticky="w", **pad)
        self.server_var = tk.StringVar(value=self.config.get("server_name") or "")
        ttk.Entry(server_frame, textvariable=self.server_var, width=30).grid(row=0, column=1, **pad)
        ttk.Button(server_frame, text="Enregistrer", command=self._save_server).grid(row=0, column=2, **pad)
        self.server_id_label = ttk.Label(server_frame, text=self._server_id_text())
        self.server_id_label.grid(row=1, column=0, columnspan=3, sticky="w", **pad)

        mode_frame = ttk.LabelFrame(self.root, text="Type d'HDV")
        mode_frame.pack(fill="x", **pad)
        self.mode_var = tk.StringVar(value=self.config.get("scan_mode", "resource"))
        ttk.Radiobutton(
            mode_frame, text="Ressources", value="resource", variable=self.mode_var, command=self._apply_mode
        ).grid(row=0, column=0, **pad)
        ttk.Radiobutton(
            mode_frame,
            text="Équipements (prix le plus bas uniquement)",
            value="item",
            variable=self.mode_var,
            command=self._apply_mode,
        ).grid(row=0, column=1, **pad)
        self.mode_radios = mode_frame.winfo_children()

        calib_frame = ttk.LabelFrame(self.root, text="Calibrage des zones d'écran")
        calib_frame.pack(fill="x", **pad)
        self.name_zone_title = ttk.Label(calib_frame, text="")
        self.name_zone_title.grid(row=0, column=0, sticky="w", **pad)
        self.name_zone_label = ttk.Label(calib_frame, text="")
        self.name_zone_label.grid(row=0, column=1, sticky="w", **pad)
        self.name_calib_btn = ttk.Button(
            calib_frame, text="Calibrer", command=lambda: self._start_calibration("name")
        )
        self.name_calib_btn.grid(row=0, column=2, **pad)

        self.price_zone_title = ttk.Label(calib_frame, text="")
        self.price_zone_title.grid(row=1, column=0, sticky="w", **pad)
        self.price_zone_label = ttk.Label(calib_frame, text="")
        self.price_zone_label.grid(row=1, column=1, sticky="w", **pad)
        self.price_calib_btn = ttk.Button(
            calib_frame, text="Calibrer", command=lambda: self._start_calibration("price")
        )
        self.price_calib_btn.grid(row=1, column=2, **pad)

        self.calib_instruction = ttk.Label(calib_frame, text="", foreground="#b06000")
        self.calib_instruction.grid(row=2, column=0, columnspan=3, sticky="w", **pad)

        settings_frame = ttk.LabelFrame(self.root, text="Réglages")
        settings_frame.pack(fill="x", **pad)
        self.poll_var = tk.StringVar(value=str(self.config.get("poll_interval_seconds", 1.0)))
        self.cooldown_var = tk.StringVar(value=str(self.config.get("submission_cooldown_seconds", 15)))
        self.outlier_var = tk.StringVar(value=str(self.config.get("outlier_ratio_warning", 3.0)))
        ttk.Label(settings_frame, text="Intervalle de lecture (s)").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(settings_frame, textvariable=self.poll_var, width=8).grid(row=0, column=1, **pad)
        ttk.Label(settings_frame, text="Délai anti-doublon (s)").grid(row=0, column=2, sticky="w", **pad)
        ttk.Entry(settings_frame, textvariable=self.cooldown_var, width=8).grid(row=0, column=3, **pad)
        ttk.Label(settings_frame, text="Seuil alerte écart médiane (x)").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(settings_frame, textvariable=self.outlier_var, width=8).grid(row=1, column=1, **pad)
        ttk.Button(settings_frame, text="Enregistrer les réglages", command=self._save_settings).grid(
            row=1, column=3, **pad
        )

        control_frame = ttk.LabelFrame(self.root, text="Contrôle (F8 pause, F9 dry-run, fonctionnent même en jeu)")
        control_frame.pack(fill="x", **pad)
        self.start_btn = ttk.Button(control_frame, text="Démarrer", command=self._toggle_running)
        self.start_btn.grid(row=0, column=0, **pad)

        self.dry_run_var = tk.BooleanVar(value=bool(self.config.get("dry_run", True)))
        ttk.Checkbutton(
            control_frame, text="Dry-run (aucun envoi réel)", variable=self.dry_run_var, command=self._apply_dry_run
        ).grid(row=0, column=1, **pad)

        self.paused_var = tk.BooleanVar(value=False)
        self.pause_check = ttk.Checkbutton(
            control_frame, text="En pause", variable=self.paused_var, command=self._apply_paused
        )
        self.pause_check.grid(row=0, column=2, **pad)

        self.status_label = ttk.Label(control_frame, text="Arrêté", foreground="#a00000")
        self.status_label.grid(row=0, column=3, **pad)

        dev_frame = ttk.LabelFrame(self.root, text="Outils développeur")
        dev_frame.pack(fill="x", **pad)
        self.seed_btn = ttk.Button(
            dev_frame, text="Remplir base (dev)", command=self._run_seed_dev_data
        )
        self.seed_btn.grid(row=0, column=0, **pad)
        ttk.Label(
            dev_frame, text="Lance seed_dev_data.py (base de dev perso, jamais la base communautaire)."
        ).grid(row=0, column=1, sticky="w", **pad)

        log_frame = ttk.LabelFrame(self.root, text="Journal")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_widget = scrolledtext.ScrolledText(log_frame, height=16, state="disabled", wrap="word")
        self.log_widget.pack(fill="both", expand=True, padx=4, pady=(4, 0))

        seed_entry_frame = ttk.Frame(log_frame)
        seed_entry_frame.pack(fill="x", padx=4, pady=4)
        self.seed_entry = ttk.Entry(seed_entry_frame, state="disabled")
        self.seed_entry.pack(side="left", fill="x", expand=True)
        self.seed_entry.bind("<Return>", self._seed_on_enter)
        self.seed_entry.bind("<Escape>", lambda e: self._seed_stop_process())
        self.seed_send_btn = ttk.Button(
            seed_entry_frame, text="Envoyer", command=self._seed_on_enter, state="disabled"
        )
        self.seed_send_btn.pack(side="left", padx=(6, 0))
        self.seed_stop_btn = ttk.Button(
            seed_entry_frame, text="Arrêter (Échap)", command=self._seed_stop_process, state="disabled"
        )
        self.seed_stop_btn.pack(side="left", padx=(6, 0))

    # --- Serveur / réglages ------------------------------------------------------------

    def _server_id_text(self) -> str:
        server_id = self.config.get("server_id")
        return f"server_id : {server_id}" if server_id else "server_id : (non défini)"

    def _save_server(self) -> None:
        name = self.server_var.get().strip()
        if not name:
            return
        self.config["server_name"] = name
        self.config["server_id"] = slugify_server_name(name)
        save_config(self.config)
        self.server_id_label.config(text=self._server_id_text())
        self._log(f"Serveur enregistré : {name} ({self.config['server_id']})")

    def _save_settings(self) -> None:
        try:
            self.config["poll_interval_seconds"] = float(self.poll_var.get())
            self.config["submission_cooldown_seconds"] = float(self.cooldown_var.get())
            self.config["outlier_ratio_warning"] = float(self.outlier_var.get())
        except ValueError:
            self._log("Réglages invalides (valeurs numériques attendues), non enregistrés.")
            return
        save_config(self.config)
        self._log("Réglages enregistrés.")

    # --- Calibrage -----------------------------------------------------------------

    def _mode_settings(self) -> dict:
        return MODES[self.mode_var.get()]

    def _apply_mode(self) -> None:
        self.config["scan_mode"] = self.mode_var.get()
        save_config(self.config)
        self._refresh_calibration_labels()
        self._log(f"Type d'HDV : {'Équipements' if self.mode_var.get() == 'item' else 'Ressources'}.")

    def _refresh_calibration_labels(self) -> None:
        settings = self._mode_settings()
        if self.mode_var.get() == "item":
            self.name_zone_title.config(text="Zone NOM de l'objet :")
            self.price_zone_title.config(text="Zone PRIX (1re ligne, le moins cher) :")
        else:
            self.name_zone_title.config(text="Zone NOM de la ressource :")
            self.price_zone_title.config(text="Zone TABLEAU DES LOTS (1, 10, 100, 1000) :")
        self.name_zone_label.config(text=self._region_text(self.config.get(settings["name_region"])))
        self.price_zone_label.config(text=self._region_text(self.config.get(settings["price_region"])))

    def _calibration_key(self, target: str) -> str:
        return self._mode_settings()["name_region" if target == "name" else "price_region"]

    @staticmethod
    def _calibration_label(target: str) -> str:
        return "NOM" if target == "name" else "PRIX"

    def _set_calibration_buttons(self, state: str) -> None:
        self.name_calib_btn.config(state=state)
        self.price_calib_btn.config(state=state)

    @staticmethod
    def _region_text(region: dict | None) -> str:
        if not region:
            return "non calibrée"
        return f"({region['left']}, {region['top']}) {region['width']}x{region['height']}px"

    def _start_calibration(self, target: str) -> None:
        if self.loop is not None:
            self._log("Arrête la lecture avant de recalibrer.")
            return
        self.calibration_target = target
        self.calibration_step = "top_left"
        self.calibration_point1 = None
        self._set_calibration_buttons("disabled")
        label = self._calibration_label(target)
        hint = ""
        if target == "price" and self.mode_var.get() == "item":
            hint = " Sur un objet SANS panoplie, 1re ligne seulement, sans l'icône kamas."
        elif target == "price":
            hint = (" Englobe les 4 lignes (1, 10, 100, 1000) : colonne Lot + colonne Prix, sans le "
                    "bouton Acheter. Fais-le sur une ressource dont les 4 lots sont en vente.")
        self.calib_instruction.config(
            text=f"Zone {label} : place la souris sur le coin HAUT-GAUCHE en jeu, puis appuie sur F10 "
            f"(Échap pour annuler).{hint}"
        )

    def _cancel_calibration(self) -> None:
        self.calibration_target = None
        self.calibration_step = None
        self.calibration_point1 = None
        self.calib_instruction.config(text="Calibrage annulé.")
        self._set_calibration_buttons("normal")

    def _capture_calibration_point(self) -> None:
        # Appelé depuis le thread du listener clavier : ne touche à Tkinter que via `after`.
        x, y = mouse.position
        target = self.calibration_target
        step = self.calibration_step

        if step == "top_left":
            self.calibration_point1 = (int(x), int(y))
            self.calibration_step = "bottom_right"
            label = self._calibration_label(target)
            self.root.after(
                0,
                lambda: self.calib_instruction.config(
                    text=f"Zone {label} : place la souris sur le coin BAS-DROIT, puis F10."
                ),
            )
            return

        if step == "bottom_right":
            left, top = self.calibration_point1
            width = max(1, int(x) - left)
            height = max(1, int(y) - top)
            region = {"left": left, "top": top, "width": width, "height": height}
            self.config[self._calibration_key(target)] = region
            save_config(self.config)

            def finish():
                self._refresh_calibration_labels()
                self.calib_instruction.config(text="Zone calibrée.")
                self._set_calibration_buttons("normal")
                self._log(f"Zone {self._calibration_label(target)} calibrée : {region}")

            self.calibration_target = None
            self.calibration_step = None
            self.calibration_point1 = None
            self.root.after(0, finish)

    # --- Raccourcis globaux ---------------------------------------------------------

    def _on_key(self, key) -> None:
        if self.calibration_target is not None:
            if key == keyboard.Key.f10:
                self._capture_calibration_point()
            elif key == keyboard.Key.esc:
                self.root.after(0, self._cancel_calibration)
            return

        if key == keyboard.Key.f8 and self.loop is not None:
            self.root.after(0, lambda: self.paused_var.set(not self.paused_var.get()))
            self.root.after(0, self._apply_paused)
        elif key == keyboard.Key.f9:
            self.root.after(0, lambda: self.dry_run_var.set(not self.dry_run_var.get()))
            self.root.after(0, self._apply_dry_run)

    # --- Démarrage / arrêt de la lecture ---------------------------------------------

    def _toggle_running(self) -> None:
        if self.loop is None:
            self._start_scan()
        else:
            self._stop_scan()

    def _start_scan(self) -> None:
        settings = self._mode_settings()
        if not self.config.get("server_id") or not self.config.get(settings["name_region"]) or not self.config.get(
            settings["price_region"]
        ):
            self._log("Configuration incomplète : renseigne le serveur et calibre les deux zones d'abord.")
            return
        self.loop = ScanLoop(
            self.config,
            self.device_id,
            log=self.log_queue.put,
            confirm=self._confirm_outlier,
            mode=self.mode_var.get(),
        )
        self.loop.set_dry_run(self.dry_run_var.get())
        self.loop.start()
        self.start_btn.config(text="Arrêter")
        # Changer de type d'HDV en cours de lecture n'aurait aucun effet sur la boucle déjà lancée.
        for radio in self.mode_radios:
            radio.config(state="disabled")
        self.paused_var.set(False)
        self._update_status()
        self._log("Lecture démarrée.")

    def _stop_scan(self) -> None:
        if self.loop is not None:
            self.loop.stop()
            self.loop = None
        self.start_btn.config(text="Démarrer")
        for radio in self.mode_radios:
            radio.config(state="normal")
        self._update_status()
        self._log("Lecture arrêtée.")

    def _confirm_outlier(self, message: str) -> bool:
        # En mode script (fill_base actif), personne ne peut répondre à la boîte de dialogue :
        # on envoie directement, sans l'afficher.
        if self.seed_process is not None and self.seed_process.poll() is None:
            self._log("  -> écart médiane ignoré (mode script actif), envoi quand même.")
            return True

        # Appelé depuis le thread ScanLoop : on bloque ce thread jusqu'à ce que l'utilisateur
        # réponde à une boîte de dialogue affichée (via `after`) sur le thread Tkinter.
        answered = threading.Event()
        result: dict[str, bool] = {}

        def ask():
            result["value"] = messagebox.askyesno("Prix très différent de la médiane", message)
            answered.set()

        self.root.after(0, ask)
        answered.wait()
        return result.get("value", False)

    def _apply_paused(self) -> None:
        if self.loop is not None:
            self.loop.set_paused(self.paused_var.get())
        self._update_status()

    def _apply_dry_run(self) -> None:
        if self.loop is not None:
            self.loop.set_dry_run(self.dry_run_var.get())
        self.config["dry_run"] = self.dry_run_var.get()
        save_config(self.config)
        self._update_status()

    def _update_status(self) -> None:
        if self.loop is None:
            self.status_label.config(text="Arrêté", foreground="#a00000")
            return
        mode = "dry-run" if self.dry_run_var.get() else "ENVOI RÉEL"
        state = "en pause" if self.paused_var.get() else "en cours"
        self.status_label.config(text=f"{state} ({mode})", foreground="#007000")

    # --- Outils développeur -----------------------------------------------------------

    def _run_seed_dev_data(self) -> None:
        if not SEED_DEV_SCRIPT.exists():
            self._log(f"Script introuvable : {SEED_DEV_SCRIPT}")
            return
        self.seed_btn.config(state="disabled")
        self.seed_entry.config(state="normal")
        self.seed_send_btn.config(state="normal")
        self.seed_stop_btn.config(state="normal")
        self._log(f"Lancement de {SEED_DEV_SCRIPT.name}...")

        # Le script d'auto-clic pilote la lecture : on démarre le contrôle avec lui s'il
        # n'est pas déjà en cours, et on le stoppera automatiquement à la fin du script.
        self._seed_started_scan = False
        if self.loop is None:
            self._start_scan()
            self._seed_started_scan = self.loop is not None

        self._seed_output_lines = []
        threading.Thread(target=self._seed_dev_data_worker, daemon=True).start()

    def _seed_dev_data_worker(self) -> None:
        try:
            if getattr(sys, "frozen", False):
                cmd = [str(SEED_DEV_SCRIPT)]
            else:
                cmd = [sys.executable, str(SEED_DEV_SCRIPT)]
            # stdin/stdout en pipe + CREATE_NO_WINDOW : le script dialogue avec le journal
            # intégré ci-dessus au lieu d'ouvrir une vraie console.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(SEED_DEV_SCRIPT.parent),
                creationflags=creationflags,
                env=env,
            )
            self.seed_process = process
            # On garde chaque ligne pour extraire le résumé final, sans les afficher au fil
            # de l'eau (l'utilisateur ne veut voir que le bilan, pas chaque clic/scroll).
            for line in process.stdout:
                self._seed_output_lines.append(line.rstrip())
            returncode = process.wait()
            self._log(f"{SEED_DEV_SCRIPT.name} terminé (code {returncode}).")
        except Exception as e:
            message = f"Erreur lors du lancement de {SEED_DEV_SCRIPT.name} : {e}"
            self._log(message)
        finally:
            self.root.after(0, self._seed_disable_input)
            self.root.after(0, lambda: self.seed_btn.config(state="normal"))
            self.root.after(0, self._finish_seed_run)

    def _seed_append(self, line: str) -> None:
        self._log(line)

    def _seed_log_summary(self) -> None:
        """N'affiche que le résumé final du script (raison d'arrêt + ligne RÉSUMÉ),
        sans le détail clic-par-clic accumulé pendant l'exécution."""
        end_reason = next(
            (l for l in reversed(self._seed_output_lines) if l.strip().startswith(("⛔", "🏁", "❌"))),
            None,
        )
        resume_line = next((l for l in self._seed_output_lines if "RÉSUMÉ" in l), None)
        if end_reason:
            self._log(end_reason.strip())
        if resume_line:
            self._log(resume_line.strip())

    def _seed_on_enter(self, event=None) -> None:
        line = self.seed_entry.get()
        self.seed_entry.delete(0, "end")
        self._seed_append(f"> {line}")
        if self.seed_process and self.seed_process.stdin and self.seed_process.poll() is None:
            try:
                self.seed_process.stdin.write(line + "\n")
                self.seed_process.stdin.flush()
            except Exception as e:
                self._seed_append(f"Erreur d'envoi : {e}")

    def _seed_stop_process(self) -> None:
        if self.seed_process and self.seed_process.poll() is None:
            self._seed_append("Arrêt du script demandé...")
            try:
                self.seed_process.terminate()
                try:
                    self.seed_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.seed_process.kill()
            except Exception as e:
                self._seed_append(f"Erreur lors de l'arrêt : {e}")

    def _seed_disable_input(self) -> None:
        self.seed_entry.config(state="disabled")
        self.seed_send_btn.config(state="disabled")
        self.seed_stop_btn.config(state="disabled")

    def _finish_seed_run(self) -> None:
        self._seed_log_summary()

        unique_count = self.loop.unique_submission_count() if self.loop is not None else 0
        if self._seed_started_scan and self.loop is not None:
            self._stop_scan()
        self._seed_started_scan = False

        summary = f"Bilan : {unique_count} ligne(s) unique(s) ajoutée(s) sur Cloudflare (doublons exclus)."
        self._log(summary)

    # --- Journal / fermeture ----------------------------------------------------------

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    def _drain_log_queue(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_widget.config(state="normal")
            self.log_widget.insert("end", message + "\n")
            self.log_widget.see("end")
            self.log_widget.config(state="disabled")
        self.root.after(150, self._drain_log_queue)

    def _on_close(self) -> None:
        if self.loop is not None:
            self.loop.stop()
        self._seed_stop_process()
        self.keyboard_listener.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
