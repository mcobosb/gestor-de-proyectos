"""Descubrimiento de herramientas Python y extensiones compiladas."""
from __future__ import annotations

from dataclasses import dataclass
import importlib
from importlib.machinery import ModuleSpec
from importlib.util import find_spec
from pathlib import Path
import pkgutil
import stat
import sys
from types import ModuleType
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from bc3_gui import BudgetApp


@dataclass(frozen=True)
class Tool:
    """Contrato de una entrada del menu y sus integraciones opcionales."""

    name: str
    label: str
    order: int
    open: Callable[[BudgetApp], None]
    available: Callable[[BudgetApp], bool] | None = None
    restore: Callable[[dict], dict] | None = None
    before_save: Callable[[BudgetApp], None] | None = None


def _optional_hook(module: ModuleType, name: str) -> Callable | None:
    """Valida un punto de integracion opcional sin ignorar valores incorrectos."""
    hook = getattr(module, name, None)
    if hook is not None and not callable(hook):
        raise ValueError(f"{name} debe ser una funcion.")
    return hook


def discover_tools(directory: Path) -> tuple[list[Tool], list[str]]:
    """Lee exclusivamente la carpeta indicada y comunica los modulos invalidos."""
    found: list[Tool] = []
    errors: list[str] = []
    try:
        mode = directory.stat().st_mode
    except FileNotFoundError:
        return found, errors
    except OSError as exc:
        return found, [f"{directory}: {exc}"]
    if not stat.S_ISDIR(mode):
        return found, [f"{directory}: no es una carpeta."]
    package = sys.modules.get("tools")
    if package is None:
        package = ModuleType("tools")
        package.__package__ = "tools"
        package.__spec__ = ModuleSpec("tools", loader=None, is_package=True)
        sys.modules["tools"] = package
    package.__path__ = [str(directory.resolve())]
    importlib.invalidate_caches()
    try:
        list(directory.iterdir())
        candidates = list(pkgutil.iter_modules([str(directory)]))
    except OSError as exc:
        return found, [f"{directory}: {exc}"]
    for candidate in candidates:
        if candidate.name.startswith("_"):
            continue
        try:
            module_name = f"tools.{candidate.name}"
            previous = sys.modules.get(module_name)
            if previous is not None:
                origin = getattr(previous, "__file__", None)
                if origin is None or not Path(origin).resolve().is_relative_to(directory.resolve()):
                    for cached_name in list(sys.modules):
                        if cached_name == module_name or cached_name.startswith(module_name + "."):
                            del sys.modules[cached_name]
            spec = find_spec(module_name)
            if spec is None or spec.origin is None:
                raise ImportError(f"No se encuentra la entrada de {candidate.name}.")
            if not Path(spec.origin).resolve().is_relative_to(directory.resolve()):
                raise ImportError(f"La entrada de {candidate.name} no pertenece a tools.")
            module = importlib.import_module(module_name)
            label = getattr(module, "TOOL_LABEL", None)
            order = getattr(module, "TOOL_ORDER", 100)
            opener = getattr(module, "open_tool", None)
            if not isinstance(label, str) or not label.strip() or not callable(opener):
                raise ValueError("El modulo debe definir TOOL_LABEL y open_tool(app).")
            if not isinstance(order, int) or isinstance(order, bool):
                raise ValueError("TOOL_ORDER debe ser un entero.")
            found.append(Tool(
                candidate.name, label, order, opener,
                _optional_hook(module, "is_available"),
                _optional_hook(module, "restore_project_state"),
                _optional_hook(module, "before_project_save"),
            ))
        except (ImportError, OSError, SyntaxError, ValueError, TypeError, AttributeError, RuntimeError, SystemExit) as exc:
            errors.append(f"{candidate.name}: {type(exc).__name__}: {exc}")
    found.sort(key=lambda tool: (tool.order, tool.label.casefold(), tool.name))
    return found, errors
