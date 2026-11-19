"""Buscador PySide6 de referencias locales NTP y UNE."""
from __future__ import annotations

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from municipalities import municipality_search_key
from reference_search import ReferenceEntry, ReferenceKind


class ReferenceSearchWindowQt(QDialog):
    """Busca referencias y abre exclusivamente sus enlaces oficiales."""

    def __init__(
        self, parent, kind: ReferenceKind, entries: tuple[ReferenceEntry, ...]
    ) -> None:
        """Construye filtro, tabla y detalle sin modificar el proyecto."""
        super().__init__(parent)
        self.kind = kind
        self.entries = entries
        self.searchable = [
            municipality_search_key(" ".join((
                entry.code,
                entry.title,
                entry.publication,
                entry.status,
                entry.committee,
            )))
            for entry in entries
        ]
        self.setWindowTitle(f"Buscador de {kind}")
        self.resize(1100, 680)
        layout = QVBoxLayout(self)
        search = QHBoxLayout()
        search.addWidget(QLabel("Buscar por código o texto:"))
        self.query = QLineEdit()
        self.query.textChanged.connect(self.filter_results)
        search.addWidget(self.query, 1)
        layout.addLayout(search)
        self.count = QLabel()
        layout.addWidget(self.count)
        headers = ["Código", "Año" if kind == "NTP" else "Fecha"]
        if kind == "LER":
            headers = ["Código", "Nivel", "Peligrosidad"]
        if kind in {"UNE", "LER"}:
            headers.append("Estado")
        if kind == "LER":
            headers = ["Código", "Nivel", "Peligrosidad", "Título"]
        headers.append("Título")
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            len(headers) - 1, QHeaderView.ResizeMode.Stretch
        )
        self.table.itemSelectionChanged.connect(self.show_selected)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self.open_selected())
        layout.addWidget(self.table, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(130)
        layout.addWidget(self.detail)
        actions = QHBoxLayout()
        self.open_button = QPushButton("Ver ficha oficial")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected)
        actions.addWidget(self.open_button)
        actions.addStretch()
        close = QPushButton("Cerrar")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        warning = QLabel(
            "Catálogo local. Comprueba en la fuente oficial la vigencia y aplicabilidad."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.filter_results("")
        self.query.setFocus()

    def filter_results(self, text: str) -> None:
        """Filtra por todas las palabras sin distinguir tildes."""
        words = municipality_search_key(text).split()
        visible = [
            index
            for index, searchable in enumerate(self.searchable)
            if all(word in searchable for word in words)
        ]
        self.table.setRowCount(len(visible))
        for row, index in enumerate(visible):
            entry = self.entries[index]
            values = [entry.code, entry.publication]
            if self.kind in {"UNE", "LER"}:
                values.append(entry.status)
            values.append(entry.title)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(self.TABLE_INDEX_ROLE, index)
                self.table.setItem(row, column, item)
        self.count.setText(f"{len(visible)} de {len(self.entries)} referencias")
        self.detail.clear()
        self.open_button.setEnabled(False)

    TABLE_INDEX_ROLE = 0x0101

    def selected_entry(self) -> ReferenceEntry | None:
        """Devuelve la entrada visible seleccionada."""
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        index = item.data(self.TABLE_INDEX_ROLE) if item else None
        return self.entries[index] if isinstance(index, int) else None

    def show_selected(self) -> None:
        """Presenta metadatos y enlace de la entrada activa."""
        entry = self.selected_entry()
        if entry is None:
            self.detail.clear()
            self.open_button.setEnabled(False)
            return
        lines = [entry.code, entry.title]
        for label, value in (
            ("Año" if self.kind == "NTP" else "Fecha", entry.publication),
            ("Estado", entry.status),
            ("Comité", entry.committee),
        ):
            if value:
                lines.append(f"{label}: {value}")
        lines.append(entry.url)
        self.detail.setPlainText("\n".join(lines))
        self.open_button.setEnabled(bool(entry.url))

    def open_selected(self) -> None:
        """Abre la ficha validada en el navegador predeterminado."""
        entry = self.selected_entry()
        if entry is None or not entry.url:
            return
        if hasattr(self.parentWidget(), "_open_web_page"):
            self.parentWidget()._open_web_page(
                entry.url,
                f"Buscador de {self.kind}",
                "Referencia abierta en el navegador.",
            )
        else:
            QMessageBox.critical(
                self, "Referencia", "No está disponible el navegador."
            )
