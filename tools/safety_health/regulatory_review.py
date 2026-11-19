"""Confirmacion de normativa y asociaciones NTP antes de emitir el estudio."""
from __future__ import annotations

import webbrowser

from .regulations import (
    REGULATIONS, WorkUnit, initially_selected, law_proposals, load_ntps,
    ntp_proposals, restore_review, review_context,
)


# Las clases visuales Tk de este módulo se retiraron; la GUI vigente está en PySide6.
