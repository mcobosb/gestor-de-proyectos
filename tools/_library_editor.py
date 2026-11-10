"""Editor reutilizable de archivos Markdown dentro de bibliotecas ZIP."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable
from zipfile import BadZipFile, ZipFile

from localization import localize_widget_tree

MAX_MARKDOWN_BYTES = 16 * 1024 * 1024


def _validate_member_name(name: str) -> str:
    """Rechaza nombres ambiguos o externos antes de mostrarlos o escribirlos."""
    relative = name[:-1] if name.endswith("/") else name
    path = PurePosixPath(relative)
    if (not relative or "\\" in relative or path.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or ":" in relative or "\x00" in relative or path.as_posix() != relative):
        raise ValueError(f"Ruta de biblioteca no válida: {name}")
    return name


def _signature(archive_path: Path) -> tuple[int, int]:
    """Obtiene el tamaño y fecha de modificación para detectar escrituras externas."""
    info = archive_path.stat()
    return info.st_size, info.st_mtime_ns


def library_members(archive_path: Path) -> list[str]:
    """Devuelve archivos del ZIP tras validar rutas y entradas duplicadas."""
    try:
        with ZipFile(archive_path) as archive:
            seen = set()
            for info in archive.infolist():
                name = _validate_member_name(info.filename)
                if name in seen:
                    raise ValueError(f"Entrada duplicada en la biblioteca: {name}")
                seen.add(name)
            return sorted((name for name in seen if not name.endswith("/")), key=str.casefold)
    except (BadZipFile, OSError) as exc:
        raise ValueError(f"No se pudo leer la biblioteca {archive_path}: {exc}") from exc


def read_markdown(archive_path: Path, member_name: str) -> str:
    """Lee un archivo Markdown UTF-8 sin extraerlo del ZIP."""
    member_name = _validate_member_name(member_name)
    if PurePosixPath(member_name).suffix.casefold() != ".md":
        raise ValueError("Solo se pueden abrir archivos Markdown.")
    try:
        with ZipFile(archive_path) as archive:
            info = archive.getinfo(member_name)
            if info.is_dir() or info.file_size > MAX_MARKDOWN_BYTES or info.flag_bits & 1:
                raise ValueError(f"Archivo Markdown demasiado grande o cifrado: {member_name}")
            with archive.open(info) as source:
                content = source.read(MAX_MARKDOWN_BYTES + 1)
            if len(content) > MAX_MARKDOWN_BYTES:
                raise ValueError(f"Archivo Markdown demasiado grande: {member_name}")
            return content.decode("utf-8-sig")
    except (BadZipFile, KeyError, OSError, UnicodeError) as exc:
        raise ValueError(f"No se pudo leer {member_name}: {exc}") from exc


def write_markdown(
    archive_path: Path, member_name: str, content: str, expected_signature: tuple[int, int],
) -> tuple[int, int]:
    """Reemplaza un Markdown y conserva el resto del ZIP mediante sustitución atómica."""
    member_name = _validate_member_name(member_name)
    if PurePosixPath(member_name).suffix.casefold() != ".md":
        raise ValueError("Solo se pueden guardar archivos Markdown.")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_MARKDOWN_BYTES:
        raise ValueError("El archivo Markdown supera el tamaño máximo permitido.")
    if _signature(archive_path) != expected_signature:
        raise ValueError("La biblioteca ha cambiado desde que se abrió. Ciérrala y vuelve a intentarlo.")

    temporary_path = None
    try:
        with ZipFile(archive_path) as source:
            infos = source.infolist()
            names = [_validate_member_name(info.filename) for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("La biblioteca contiene entradas duplicadas.")
            if member_name not in names:
                raise ValueError(f"El archivo ya no existe en la biblioteca: {member_name}")
            selected = infos[names.index(member_name)]
            if selected.is_dir() or selected.flag_bits & 1:
                raise ValueError(f"El archivo Markdown no se puede modificar: {member_name}")
            if selected.file_size > MAX_MARKDOWN_BYTES:
                raise ValueError(f"Archivo Markdown demasiado grande: {member_name}")
            with tempfile.NamedTemporaryFile(
                prefix=f".{archive_path.name}.", suffix=".tmp",
                dir=archive_path.parent, delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            with ZipFile(temporary_path, "w") as target:
                target.comment = source.comment
                for info, name in zip(infos, names):
                    if name == member_name:
                        target.writestr(info, encoded)
                    else:
                        with source.open(info) as original:
                            with target.open(info, "w") as copy:
                                while chunk := original.read(1024 * 1024):
                                    copy.write(chunk)
        if _signature(archive_path) != expected_signature:
            raise ValueError("La biblioteca ha cambiado durante la edición. No se guardaron los cambios.")
        os.replace(temporary_path, archive_path)
        temporary_path = None
        return _signature(archive_path)
    except (BadZipFile, KeyError, OSError, RuntimeError, NotImplementedError) as exc:
        raise ValueError(f"No se pudo guardar {member_name}: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class LibraryEditor(tk.Toplevel):
    """Muestra el árbol de una biblioteca y permite guardar sus fichas Markdown."""

    def __init__(
        self, master: tk.Misc, archive_path: Path, title: str,
        on_saved: Callable[[], None] | None = None,
    ):
        """Construye el navegador y editor vinculado a un archivo ZIP."""
        super().__init__(master)
        self.archive_path = archive_path
        self.on_saved = on_saved
        self.signature = _signature(archive_path)
        self.selected_member: str | None = None
        self.dirty = False
        self.loading = False
        self.title(title)
        self.geometry("1000x650")
        self.minsize(760, 460)
        self.protocol("WM_DELETE_WINDOW", self.close)

        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Selecciona un archivo .md para verlo y editarlo.").pack(side="left")
        self.save_button = ttk.Button(toolbar, text="Guardar", command=self.save, state="disabled")
        self.save_button.pack(side="right")

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        tree_frame = ttk.Frame(panes)
        editor_frame = ttk.Frame(panes)
        panes.add(tree_frame, weight=1)
        panes.add(editor_frame, weight=3)

        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.text = tk.Text(editor_frame, wrap="word", undo=True, state="disabled")
        text_scroll = ttk.Scrollbar(editor_frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=text_scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        text_scroll.pack(side="right", fill="y")
        self.status = tk.StringVar(self, value="")
        ttk.Label(self, textvariable=self.status, anchor="w", padding=(8, 2)).pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.text.bind("<<Modified>>", self._edited)
        self._populate()
        localize_widget_tree(self)

    def _populate(self) -> None:
        """Crea los nodos de carpeta y archivo conservando las rutas del ZIP."""
        folders = {""}
        files = set()
        for member in library_members(self.archive_path):
            parts = PurePosixPath(member).parts
            for index in range(1, len(parts)):
                folders.add("/".join(parts[:index]))
            files.add(member)

        folder_ids = {"": ""}
        root_documents = sorted(
            (member for member in files if len(PurePosixPath(member).parts) == 1
             and PurePosixPath(member).suffix.casefold() == ".md"),
            key=str.casefold,
        )
        for member in root_documents:
            self.tree.insert(
                "", "end", iid=f"file:{member}", text=PurePosixPath(member).name,
                values=(member,),
            )
        for folder in sorted(folders - {""}, key=lambda value: (value.count("/"), value.casefold())):
            parent_path = str(PurePosixPath(folder).parent)
            parent = "" if parent_path == "." else folder_ids[parent_path]
            folder_ids[folder] = self.tree.insert(
                parent, "end", iid=f"dir:{folder}", text=PurePosixPath(folder).name, open=False,
            )
        for member in sorted(
            (member for member in files if len(PurePosixPath(member).parts) > 1
             or PurePosixPath(member).suffix.casefold() != ".md"),
            key=str.casefold,
        ):
            parent_path = str(PurePosixPath(member).parent)
            parent = "" if parent_path == "." else folder_ids[parent_path]
            self.tree.insert(
                parent, "end", iid=f"file:{member}", text=PurePosixPath(member).name,
                values=(member,),
            )

    def _edited(self, event=None) -> None:
        """Habilita el guardado únicamente tras editar una ficha seleccionada."""
        if event is not None and event.widget is not self.text:
            return
        if not self.loading and self.text.edit_modified():
            self.dirty = True
            self.save_button.configure(state="normal")
            self.text.edit_modified(False)

    def _select(self, event=None) -> None:
        """Abre la ficha elegida o muestra el mensaje inicial para carpetas."""
        if event is not None and event.widget is not self.tree:
            return
        selection = self.tree.selection()
        item = selection[0] if selection else ""
        member = self.tree.item(item, "values")[0] if item.startswith("file:") else None
        if member == self.selected_member:
            return
        if self.dirty and not self._confirm_discard_or_save():
            if self.selected_member:
                self.tree.selection_set(f"file:{self.selected_member}")
            else:
                self.tree.selection_remove(item)
            return
        self.loading = True
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if member and PurePosixPath(member).suffix.casefold() == ".md":
            try:
                self.text.insert("1.0", read_markdown(self.archive_path, member))
                self.status.set(member)
            except ValueError as exc:
                self.status.set(str(exc))
                member = None
        else:
            self.status.set("Este archivo no se puede editar aquí." if member
                            else "Selecciona un archivo Markdown.")
        self.selected_member = member
        self.text.edit_modified(False)
        self.text.configure(state="normal" if member else "disabled")
        self.loading = False
        self.dirty = False
        self.save_button.configure(state="disabled")

    def _confirm_discard_or_save(self) -> bool:
        """Pregunta si se guardan o descartan cambios antes de cambiar de ficha."""
        answer = messagebox.askyesnocancel(
            "Cambios sin guardar", "¿Quieres guardar los cambios de esta ficha?", parent=self,
        )
        if answer is None:
            return False
        if answer:
            return self.save()
        self.dirty = False
        return True

    def save(self) -> bool:
        """Guarda el Markdown seleccionado sin sobrescribir cambios externos."""
        if not self.selected_member or not self.dirty:
            return True
        try:
            self.signature = write_markdown(
                self.archive_path, self.selected_member, self.text.get("1.0", "end-1c"),
                self.signature,
            )
            self.dirty = False
            self.text.edit_modified(False)
            self.save_button.configure(state="disabled")
            self.status.set(f"Guardado: {self.selected_member}")
        except (OSError, UnicodeError, ValueError) as exc:
            messagebox.showerror("No se pudo guardar", str(exc), parent=self)
            return False
        if self.on_saved is not None:
            try:
                self.on_saved()
            except (OSError, ValueError) as exc:
                messagebox.showerror(
                    "Biblioteca guardada", f"Se guardó el archivo, pero no se pudo actualizar "
                    f"la herramienta: {exc}", parent=self,
                )
        return True

    def close(self) -> None:
        """Cierra la ventana solo después de resolver cambios pendientes."""
        if not self.dirty or self._confirm_discard_or_save():
            self.destroy()
