"""Editor reutilizable de archivos Markdown dentro de bibliotecas ZIP."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable
from zipfile import BadZipFile, ZipFile

from localization import localize_widget_tree, simpledialog
from ui_theme import COLORS

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
            from ._library_archive import _catalog_changes

            changes = {member_name: encoded}
            changes.update(_catalog_changes(source, {}, {}, changes))
            with tempfile.NamedTemporaryFile(
                prefix=f".{archive_path.name}.", suffix=".tmp",
                dir=archive_path.parent, delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            with ZipFile(temporary_path, "w") as target:
                target.comment = source.comment
                for info, name in zip(infos, names):
                    if name in changes:
                        target.writestr(info, changes[name])
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
        *, used_members: set[str] | None = None,
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
        tk.Label(
            self, text="Verde: fichas utilizadas y carpetas que las contienen.",
            background=COLORS["teal_soft"], foreground=COLORS["teal_text"], anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 4))
        self.save_button = ttk.Button(toolbar, text="Guardar", command=self.save, state="disabled")
        self.save_button.pack(side="right")
        self.rename_button = ttk.Button(
            toolbar, text="Renombrar ficha...", command=self.rename_sheet, state="disabled",
        )
        self.rename_button.pack(side="right", padx=4)
        self.new_button = ttk.Button(
            toolbar, text="Nueva ficha...", command=self.new_sheet, state="disabled",
        )
        self.new_button.pack(side="right")

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        tree_frame = ttk.Frame(panes)
        editor_frame = ttk.Frame(panes)
        panes.add(tree_frame, weight=1)
        panes.add(editor_frame, weight=3)

        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        self.tree.tag_configure(
            "used", background=COLORS["teal_soft"], foreground=COLORS["teal_text"],
        )
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
        self.highlight_used(used_members or set())
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

    def highlight_used(self, members: set[str]) -> None:
        """Marca las fichas utilizadas y todos sus antecesores sin cambiar la selección."""
        self.used_members = set(members)
        stack = list(self.tree.get_children())
        while stack:
            node = stack.pop()
            self.tree.item(node, tags=())
            stack.extend(self.tree.get_children(node))
        for member in members:
            node = f"file:{member}"
            if not self.tree.exists(node) or PurePosixPath(member).suffix.casefold() != ".md":
                continue
            while node:
                self.tree.item(node, tags=("used",))
                node = self.tree.parent(node)

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
        folder = self._terminal_folder(item)
        self.new_button.configure(state="normal" if folder else "disabled")
        self.rename_button.configure(
            state="normal" if folder and item.startswith("file:") and item.lower().endswith(".md")
            else "disabled",
        )
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

    def _terminal_folder(self, node: str) -> str | None:
        """Obtiene la carpeta seleccionada solo si tiene fichas y no subcarpetas."""
        parent = node if node.startswith("dir:") else self.tree.parent(node) if node else ""
        if not parent.startswith("dir:"):
            return None
        children = self.tree.get_children(parent)
        if any(child.startswith("dir:") for child in children):
            return None
        if not any(child.startswith("file:") and child.lower().endswith(".md") for child in children):
            return None
        return parent.removeprefix("dir:")

    def new_sheet(self) -> None:
        """Crea una ficha catalogada en la carpeta terminal seleccionada."""
        from ._library_archive import sheet_name, update_library

        selection = self.tree.selection()
        folder = self._terminal_folder(selection[0]) if selection else None
        if folder is None:
            messagebox.showwarning(
                "Nueva ficha", "Selecciona una carpeta terminal que ya contenga fichas Markdown.",
                parent=self,
            )
            return
        name = simpledialog.askstring(
            "Nueva ficha", "Nombre de ficha (CÓDIGO-Descripción.md):", parent=self,
            initialvalue=PurePosixPath(folder).name.split("_", 1)[0] + "-",
        )
        if name is None:
            return
        try:
            name, code, title = sheet_name(name)
            member = (PurePosixPath(folder) / name).as_posix()
            if self.dirty and not self._confirm_discard_or_save():
                return
            self.signature = update_library(
                self.archive_path, self.signature,
                additions={member: f"# {title}\n\n**Clasificación:** {code}\n\n"},
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se pudo crear la ficha", str(exc), parent=self)
            return
        self._refresh_members(member)
        self._notify_saved()

    def rename_sheet(self) -> None:
        """Renombra una ficha conservando catálogo, enlaces y selecciones anteriores."""
        from ._library_archive import sheet_name, update_library

        selection = self.tree.selection()
        node = selection[0] if selection else ""
        if not self._terminal_folder(node) or not node.startswith("file:") or not node.lower().endswith(".md"):
            messagebox.showwarning(
                "Renombrar ficha", "Selecciona una ficha Markdown de una carpeta terminal.",
                parent=self,
            )
            return
        old = node.removeprefix("file:")
        name = simpledialog.askstring(
            "Renombrar ficha", "Nombre de ficha (CÓDIGO-Descripción.md):", parent=self,
            initialvalue=PurePosixPath(old).name,
        )
        if name is None:
            return
        try:
            name, _, _ = sheet_name(name)
            new = (PurePosixPath(old).parent / name).as_posix()
            if new == old:
                return
            if self.dirty and not self._confirm_discard_or_save():
                return
            self.signature = update_library(
                self.archive_path, self.signature, renames={old: new},
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se pudo renombrar la ficha", str(exc), parent=self)
            return
        self.used_members = {new if member == old else member for member in self.used_members}
        self._refresh_members(new)
        self._notify_saved()

    def _refresh_members(self, member: str) -> None:
        """Recarga el árbol tras crear o renombrar sin perder las marcas de uso."""
        self.selected_member = None
        self.dirty = False
        self.tree.delete(*self.tree.get_children())
        self._populate()
        self.highlight_used(self.used_members)
        self.tree.selection_set(f"file:{member}")
        self.tree.see(f"file:{member}")
        self._select()

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
        self._notify_saved()
        return True

    def _notify_saved(self) -> None:
        """Actualiza el asistente e informa de fallos posteriores al guardado."""
        if self.on_saved is not None:
            try:
                self.on_saved()
            except (OSError, ValueError) as exc:
                messagebox.showerror(
                    "Biblioteca guardada", f"Se guardó el archivo, pero no se pudo actualizar "
                    f"la herramienta: {exc}", parent=self,
                )

    def close(self) -> None:
        """Cierra la ventana solo después de resolver cambios pendientes."""
        if not self.dirty or self._confirm_discard_or_save():
            self.destroy()
