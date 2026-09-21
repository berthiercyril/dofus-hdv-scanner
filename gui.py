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
import queue
import tkinter as tk
from tkinter import scrolledtext, ttk

from pynput import keyboard
from pynput.mouse import Controller as MouseController

from common import get_or_create_device_id, load_config, save_config, slugify_server_name
from scanner_core import ScanLoop

mouse = MouseController()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Dofus HDV Scanner")
        self.root.geometry("640x560")

        self.config = load_config()
        self.device_id = get_or_create_device_id(self.config)
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.loop: ScanLoop | None = None

        self.calibration_target: str | None = None  # "name" | "price" | None
        self.calibration_step: str | None = None  # "top_left" | "bottom_right"
        self.calibration_point1: tuple[int, int] | None = None

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

        calib_frame = ttk.LabelFrame(self.root, text="Calibrage des zones d'écran")
        calib_frame.pack(fill="x", **pad)
        ttk.Label(calib_frame, text="Zone NOM de la ressource :").grid(row=0, column=0, sticky="w", **pad)
        self.name_zone_label = ttk.Label(calib_frame, text="")
        self.name_zone_label.grid(row=0, column=1, sticky="w", **pad)
        self.name_calib_btn = ttk.Button(
            calib_frame, text="Calibrer", command=lambda: self._start_calibration("name")
        )
        self.name_calib_btn.grid(row=0, column=2, **pad)

        ttk.Label(calib_frame, text="Zone PRIX de la ressource :").grid(row=1, column=0, sticky="w", **pad)
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

        log_frame = ttk.LabelFrame(self.root, text="Journal")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_widget = scrolledtext.ScrolledText(log_frame, height=16, state="disabled", wrap="word")
        self.log_widget.pack(fill="both", expand=True, padx=4, pady=4)

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

    def _refresh_calibration_labels(self) -> None:
        self.name_zone_label.config(text=self._region_text(self.config.get("name_region")))
        self.price_zone_label.config(text=self._region_text(self.config.get("price_region")))

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
        self.name_calib_btn.config(state="disabled")
        self.price_calib_btn.config(state="disabled")
        label = "NOM" if target == "name" else "PRIX"
        self.calib_instruction.config(
            text=f"Zone {label} : place la souris sur le coin HAUT-GAUCHE en jeu, puis appuie sur F10 "
            "(Échap pour annuler)."
        )

    def _cancel_calibration(self) -> None:
        self.calibration_target = None
        self.calibration_step = None
        self.calibration_point1 = None
        self.calib_instruction.config(text="Calibrage annulé.")
        self.name_calib_btn.config(state="normal")
        self.price_calib_btn.config(state="normal")

    def _capture_calibration_point(self) -> None:
        # Appelé depuis le thread du listener clavier : ne touche à Tkinter que via `after`.
        x, y = mouse.position
        target = self.calibration_target
        step = self.calibration_step

        if step == "top_left":
            self.calibration_point1 = (int(x), int(y))
            self.calibration_step = "bottom_right"
            label = "NOM" if target == "name" else "PRIX"
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
            key = "name_region" if target == "name" else "price_region"
            self.config[key] = region
            save_config(self.config)

            def finish():
                self._refresh_calibration_labels()
                self.calib_instruction.config(text="Zone calibrée.")
                self.name_calib_btn.config(state="normal")
                self.price_calib_btn.config(state="normal")
                self._log(f"Zone {'NOM' if target == 'name' else 'PRIX'} calibrée : {region}")

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
        if not self.config.get("server_id") or not self.config.get("name_region") or not self.config.get(
            "price_region"
        ):
            self._log("Configuration incomplète : renseigne le serveur et calibre les deux zones d'abord.")
            return
        self.loop = ScanLoop(self.config, self.device_id, log=self.log_queue.put)
        self.loop.set_dry_run(self.dry_run_var.get())
        self.loop.start()
        self.start_btn.config(text="Arrêter")
        self.paused_var.set(False)
        self._update_status()
        self._log("Lecture démarrée.")

    def _stop_scan(self) -> None:
        if self.loop is not None:
            self.loop.stop()
            self.loop = None
        self.start_btn.config(text="Démarrer")
        self._update_status()
        self._log("Lecture arrêtée.")

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
        self.keyboard_listener.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
