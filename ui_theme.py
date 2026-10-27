"""Paleta y estilos compartidos por las ventanas de la aplicacion."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


COLORS = {
    "background": "#F1F5F9",
    "surface": "#FFFFFF",
    "text": "#24364B",
    "muted": "#526579",
    "border": "#CBD5E1",
    "navy": "#173753",
    "navy_hover": "#294F70",
    "blue": "#245DA8",
    "blue_hover": "#194A8B",
    "blue_soft": "#E8F0FA",
    "teal": "#087F6D",
    "teal_hover": "#066657",
    "teal_soft": "#E0F2EC",
    "teal_text": "#155E50",
    "danger": "#A32F3D",
    "danger_soft": "#FBEAEC",
}


def apply_theme(root: tk.Tk) -> None:
    """Usa clam para que Windows respete los colores de todos los controles."""
    c = COLORS
    root.configure(background=c["background"])
    for pattern, value in {
        "*Font": ("Segoe UI", 9),
        "*Toplevel.background": c["background"],
        "*Text.background": c["surface"],
        "*Text.foreground": c["text"],
        "*Text.insertBackground": c["blue"],
        "*Text.selectBackground": c["blue"],
        "*Text.selectForeground": c["surface"],
        "*Text.relief": "flat",
        "*Text.highlightThickness": 1,
        "*Text.highlightBackground": c["border"],
        "*Text.highlightColor": c["blue"],
        "*Text.padX": 8,
        "*Text.padY": 6,
        "*Menu.background": c["surface"],
        "*Menu.foreground": c["text"],
        "*Menu.activeBackground": c["blue"],
        "*Menu.activeForeground": c["surface"],
        "*Button.background": c["teal_soft"],
        "*Button.foreground": c["teal_text"],
        "*Button.activeBackground": c["teal"],
        "*Button.activeForeground": c["surface"],
        "*Button.relief": "flat",
        "*Button.padY": 4,
    }.items():
        root.option_add(pattern, value)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".", background=c["background"], foreground=c["text"],
        font=("Segoe UI", 9), bordercolor=c["border"],
        lightcolor=c["border"], darkcolor=c["border"], focuscolor=c["blue"],
    )
    style.configure("TFrame", background=c["background"])
    style.configure("TLabel", background=c["background"])
    style.configure("Muted.TLabel", foreground=c["muted"])
    style.configure("Toolbar.TFrame", background=c["navy"])
    style.configure(
        "Toolbar.TLabel", background=c["navy"], foreground=c["surface"],
        font=("Segoe UI", 10, "bold"),
    )
    style.configure(
        "Section.TLabel", background=c["blue"], foreground=c["surface"],
        font=("Segoe UI", 11, "bold"), padding=(10, 8),
    )
    style.configure("Reference.Section.TLabel", background=c["teal"])
    style.configure(
        "Total.TLabel", background=c["teal_soft"], foreground=c["teal_text"],
        font=("Segoe UI", 11, "bold"), padding=(10, 8),
    )
    style.configure("TButton", background=c["surface"], foreground=c["blue"], padding=(8, 4))
    style.map(
        "TButton", background=[("disabled", c["background"]), ("pressed", c["blue_soft"]),
                               ("active", c["blue_soft"])],
        foreground=[("disabled", c["muted"])],
        bordercolor=[("focus", c["blue"]), ("active", c["blue"])],
    )
    for name, normal, hover in (
        ("Accent.TButton", c["teal"], c["teal_hover"]),
        ("Primary.TButton", c["blue"], c["blue_hover"]),
    ):
        style.configure(name, background=normal, foreground=c["surface"])
        style.map(
            name, background=[("disabled", c["border"]), ("pressed", hover), ("active", hover)],
            foreground=[("disabled", c["muted"]), ("!disabled", c["surface"])],
        )
    style.configure("Danger.TButton", foreground=c["danger"])
    style.map("Danger.TButton", background=[("active", c["danger_soft"])])
    style.configure(
        "Toolbar.TMenubutton", background=c["navy"], foreground=c["surface"],
        borderwidth=0, padding=(10, 6), arrowcolor=c["surface"],
    )
    style.map(
        "Toolbar.TMenubutton",
        background=[("active", c["navy_hover"]), ("pressed", c["navy_hover"])],
    )
    style.configure("Treeview", background=c["surface"], fieldbackground=c["surface"],
                    foreground=c["text"], rowheight=24, borderwidth=0)
    style.map("Treeview", background=[("selected", c["blue"])],
              foreground=[("selected", c["surface"])])
    style.configure(
        "Treeview.Heading", background=c["blue_soft"], foreground=c["navy"],
        font=("Segoe UI", 9, "bold"), padding=(6, 6), relief="flat",
    )
    style.map("Treeview.Heading", background=[("active", c["border"])])
    style.configure("TNotebook", background=c["background"], borderwidth=0, tabmargins=(0, 4, 0, 0))
    style.configure("TNotebook.Tab", background=c["blue_soft"], padding=(12, 6))
    style.map(
        "TNotebook.Tab",
        background=[("selected", c["blue"]), ("active", c["border"])],
        foreground=[("selected", c["surface"])],
    )
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=c["surface"], foreground=c["text"], padding=4)
        style.map(
            name, fieldbackground=[("disabled", c["background"]), ("readonly", c["surface"])],
            bordercolor=[("focus", c["blue"])],
            selectbackground=[("focus", c["blue"])],
            selectforeground=[("focus", c["surface"])],
        )
    style.configure("TLabelframe", bordercolor=c["border"], relief="solid")
    style.configure("TLabelframe.Label", foreground=c["blue"], font=("Segoe UI", 9, "bold"))
    style.configure("Sash", sashthickness=6)
