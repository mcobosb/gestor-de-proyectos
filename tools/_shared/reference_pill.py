"""Píldoras de ayuda contextual para las ventanas principales de Qt."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QMessageBox, QPushButton, QStyle, QVBoxLayout, QWidget,
)

from budget_config import REFERENCE_PRICE_DATABASES, REPOSITORY_URL


_CONTRACTS = ("Ley 9/2017 de Contratos del Sector Público",
              "https://www.boe.es/buscar/act.php?id=BOE-A-2017-12902")
_REGULATION = "https://www.boe.es/buscar/act.php?id=BOE-A-2001-19995"
_REFERENCES: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "budget": (
        "El presupuesto y la base de referencia son independientes. "
        "Añadir una partida al proyecto no modifica la base original.",
        (*REFERENCE_PRICE_DATABASES, ("Guía de la aplicación", f"{REPOSITORY_URL}/blob/main/docs/MANUAL_USUARIO.md")),
    ),
    "price_justification": (
        "El anejo reutiliza los precios del presupuesto. Contrasta los datos laborales "
        "con las referencias vigentes en la fecha de valoración.",
        (("Composición de precios — artículo 130 del reglamento", f"{_REGULATION}#a130"),
         ("Convenio de la construcción — modificación de 2025",
          "https://www.boe.es/buscar/doc.php?id=BOE-A-2025-25234")),
    ),
    "pptp": (
        "Revisa las fichas y las normas propuestas para adaptarlas al alcance y a las "
        "prescripciones del proyecto antes de generar el pliego.",
        (_CONTRACTS, ("Buscador de normas UNE", "https://www.une.org/encuentra-tu-norma/busca-tu-norma")),
    ),
    "safety_health": (
        "Revisa los riesgos y medidas de cada capítulo según las condiciones reales de la obra. "
        "Las NTP del INSST son guías técnicas, no sustituyen a la normativa.",
        (("Disposiciones de seguridad y salud en obras — RD 1627/1997",
          "https://www.boe.es/buscar/act.php?id=BOE-A-1997-22614"),
         ("Notas Técnicas de Prevención — INSST",
          "https://www.insst.es/ntp-notas-tecnicas-de-prevencion")),
    ),
    "rcd": (
        "Las correspondencias LER–BCCA son orientativas: confirma el origen y la "
        "peligrosidad del residuo antes de asignar un código.",
        (("Lista Europea de Residuos — Decisión 2000/532/CE",
          "https://eur-lex.europa.eu/eli/dec/2000/532/oj?locale=es"),
         ("Producción y gestión de RCD — Real Decreto 105/2008",
          "https://www.boe.es/buscar/act.php?id=BOE-A-2008-2486")),
    ),
    "planning": (
        "Revisa el calendario de obra, las dependencias y los rendimientos antes de "
        "aceptar la secuencia y las duraciones calculadas.",
        (("Programa de trabajo — artículo 144 del reglamento", f"{_REGULATION}#a144"),),
    ),
    "certifications": (
        "Contrasta las mediciones ejecutadas y el acumulado anterior antes de emitir "
        "la certificación del periodo.",
        (("Certificaciones de obra — artículo 240 de la LCSP", f"{_CONTRACTS[1]}#a240"),),
    ),
    "invoicing": (
        "Comprueba los datos extraídos y la asignación de las líneas antes de incorporar "
        "la factura al registro.",
        (("Reglamento de facturación — Real Decreto 1619/2012",
          "https://www.boe.es/buscar/act.php?id=BOE-A-2012-14696"),),
    ),
    "bim": (
        "Revisa las equivalencias IFC–BCCA y sus cantidades antes de incorporar las "
        "mediciones al presupuesto.",
        (("Documentación IFC 4.3 — buildingSMART", "https://ifc43-docs.standards.buildingsmart.org/"),
         REFERENCE_PRICE_DATABASES[0]),
    ),
}


class ReferencePill(QPushButton):
    """Abre la nota y los enlaces de una ventana sin navegar automáticamente."""

    def __init__(self, topic: str, parent: QWidget | None = None) -> None:
        """Crea un botón reutilizable a partir del catálogo de referencias."""
        super().__init__("Referencias", parent)
        self.note, self.links = _REFERENCES[topic]
        self.setObjectName("referencePill")
        self.setToolTip(self.note)
        self.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        self.clicked.connect(self._show_references)

    def _show_references(self) -> None:
        """Muestra un diálogo accesible por teclado con enlaces explícitos."""
        dialog = QDialog(self.window())
        dialog.setObjectName("referencePillDialog")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle(self.text())
        layout = QVBoxLayout(dialog)
        note = QLabel(self.note, dialog)
        note.setWordWrap(True)
        layout.addWidget(note)
        for title, url in self.links:
            link = QLabel(f'<a href="{escape(url)}">{escape(title)}</a>', dialog)
            link.setTextFormat(Qt.TextFormat.RichText)
            link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            link.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            link.setWordWrap(True)
            link.linkActivated.connect(self._open_link)
            layout.addWidget(link)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(460, dialog.sizeHint().height())
        dialog.open()

    def _open_link(self, address: str) -> None:
        """Abre únicamente referencias HTTPS y avisa si el navegador no responde."""
        url = QUrl(address)
        if not url.isValid() or url.scheme() != "https" or not url.host():
            QMessageBox.warning(self, self.text(), "La dirección de referencia no es válida.")
        elif not QDesktopServices.openUrl(url):
            QMessageBox.warning(self, self.text(), "No se pudo abrir el enlace en el navegador.")


def add_reference_pill(layout: QVBoxLayout, topic: str) -> None:
    """Coloca una píldora compacta en la parte superior de una ventana."""
    layout.addWidget(ReferencePill(topic), alignment=Qt.AlignmentFlag.AlignRight)
