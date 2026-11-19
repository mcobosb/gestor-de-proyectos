"""Índice de obra civil y correspondencia con los archivos ODT distribuidos."""
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from shutil import copyfileobj
from typing import BinaryIO
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

TEMPLATES = Path(__file__).resolve().parent / "plantillas"


@dataclass(frozen=True)
class Entry:
    """Nodo del índice: grupo, plantilla de partida o referencia externa."""

    title: str
    key: str = ""
    outline: tuple[str, ...] = ()
    children: tuple["Entry", ...] = ()
    external: str = ""

    @property
    def filename(self) -> str:
        """Crea un nombre legible y estable, sin caracteres reservados en Windows."""
        return f"{self.key} - {self.title.replace(':', ' -')}.odt"


def _template(key: str, title: str, *outline: str) -> Entry:
    """Declara un apartado con su propio esquema editable."""
    return Entry(title, key, outline)


def _external(title: str, reason: str) -> Entry:
    """Conserva una referencia en el índice sin crear un archivo."""
    return Entry(title, external=reason)


_PLANS = "Planos preparados externamente en CAD o GIS."
_REPORTS = "Se prepara desde Informes o a partir de los datos del presupuesto."
_PROCEDURES = "Documentación externa o dependiente de la tramitación."
PRICE_JUSTIFICATION_NUMBER = "15"
RCD_NUMBER = "18"

CATALOG = (
    Entry("DOCUMENTO N.º 1: MEMORIA Y ANEJOS", "01 Memoria y anejos", children=(
        Entry("1.1. Memoria Descriptiva y Justificativa", "01 Memoria", children=(
            _template("01", "Objeto del proyecto y antecedentes", "Objeto y alcance", "Antecedentes y documentación de partida"),
            _template("02", "Estado actual", "Situación e inventario", "Deficiencias y condicionantes"),
            _template("03", "Justificación de la solución adoptada", "Alternativas consideradas", "Criterios y solución escogida"),
            _template("04", "Descripción detallada de las obras", "Ámbito de actuación", "Trabajos por capítulos e interfaces"),
            _template("05", "Programación y plazo de ejecución", "Fases y dependencias", "Plazo y justificación"),
            _template("06", "Clasificación del contratista", "Aplicabilidad", "Grupo, subgrupo y categoría, si procede"),
            _template("07", "Declaración de obra completa", "Alcance funcional", "Justificación y declaración, si procede"),
            _template("08", "Propuesta de fórmula de revisión de precios", "Aplicabilidad", "Fórmula propuesta y justificación"),
            _external("Resumen del presupuesto", _REPORTS),
        )),
        Entry("1.2. Anejos a la Memoria", "02 Anejos", children=(
            _template("01", "Cartografía y Topografía", "Sistema de referencia y fuentes", "Levantamiento y validación"),
            _template("02", "Geología y Geotecnia", "Campaña y terreno", "Parámetros y recomendaciones"),
            _template("03", "Climatología e Hidrología", "Datos climáticos", "Cuencas y caudales de cálculo"),
            _template("04", "Drenaje Superficial y Subterráneo", "Criterios de diseño", "Dimensionamiento y puntos de vertido"),
            _template("05", "Trazado Geométrico", "Condicionantes y criterios", "Geometría en planta, alzado y sección"),
            _template("06", "Movimiento de Tierras y Compensación", "Mediciones y materiales", "Balance, acopios y destinos"),
            _template("07", "Geotecnia Vial y Dimensionamiento de Firmes y Pavimentos", "Explanada y tráfico de diseño", "Secciones y dimensionamiento"),
            _template("08", "Cálculo de Estructuras y Obras de Fábrica", "Hipótesis, materiales y acciones", "Modelo, comprobaciones y resultados"),
            Entry("Redes de Servicios Urbanos", "09 Redes", children=(
                _template("01", "Abastecimiento de agua potable y red contra incendios", "Demandas y presiones", "Trazado y comprobaciones"),
                _template("02", "Saneamiento y depuración", "Caudales y vertidos", "Trazado, dimensionamiento y tratamiento"),
                _template("03", "Red de Baja y Media Tensión", "Potencias y suministro", "Trazado, protecciones y comprobaciones"),
                _template("04", "Alumbrado público y eficiencia energética", "Niveles de servicio", "Implantación y comprobaciones energéticas"),
                _template("05", "Telecomunicaciones y canalizaciones de fibra óptica", "Operadores y previsión de demanda", "Trazado, registros y cruces"),
                _template("06", "Red de gas canalizado y riego", "Red de gas y requisitos del operador", "Red de riego y dimensionamiento"),
            )),
            _template("10", "Servicios Afectados y Reposiciones", "Inventario y titulares", "Afecciones, coordinación y reposiciones"),
            _template("11", "Señalización, Balizamiento y Defensas", "Criterios e inventario", "Implantación y justificación"),
            _template("12", "Accesibilidad y Supresión de Barreras Arquitectónicas", "Itinerarios y condicionantes", "Comprobaciones y medidas correctoras"),
            _template("13", "Expropiaciones y Bienes Afectados", "Parcelas y titulares", "Superficies, afecciones y documentación asociada"),
            _template("14", "Integración Ambiental y Paisajística", "Inventario y afecciones", "Medidas, restauración y seguimiento"),
            Entry("Justificación de Precios", PRICE_JUSTIFICATION_NUMBER, external="Herramientas → Justificación de precios.", children=tuple(
                Entry(title) for title in ("Mano de obra", "Maquinaria y medios auxiliares",
                                          "Materiales a pie de obra", "Precios descompuestos")
            )),
            Entry("Plan de Obra y Programación de los Trabajos", "16", external="Herramientas → Planificación."),
            _template("17", "Plan de Control de Calidad", "Recepción de materiales", "Ensayos, lotes y criterios de aceptación"),
            Entry("Estudio de Gestión de Residuos de Construcción y Demolición (RCDs)", RCD_NUMBER, external="Herramientas → RCDs."),
            Entry("Estudio de Seguridad y Salud (ESS) o Estudio Básico (EBSS)", "19", external="Herramientas → Seguridad y Salud."),
            _template("20", "Manual de Uso, Conservación y Mantenimiento", "Inventario y condiciones de uso", "Operaciones, frecuencias y registros"),
        )),
    )),
    Entry("DOCUMENTO N.º 2: PLANOS", external=_PLANS, children=tuple(
        Entry(f"Plano n.º {index}: {title}") for index, title in enumerate((
            "Situación y Emplazamiento", "Topográfico y Estado Actual", "Planta General de Conjunto",
            "Planta de Replanteo y Trazado", "Perfiles Longitudinales", "Perfiles Transversales",
            "Secciones Tipo y Detalles de Firmes", "Drenaje Longitudinal y Transversal",
            "Estructuras y Obras de Fábrica", "Redes de Servicios Urbanos", "Servicios Afectados",
            "Señalización, Balizamiento y Semaforización", "Jardinería, Riego y Mobiliario Urbano",
            "Accesibilidad", "Fases de Ejecución y Desvíos Provisionales de Tráfico",
            "Planos Parcelarios de Expropiación", "Planos de Seguridad y Salud",
        ), 1)
    )),
    Entry("DOCUMENTO N.º 3: PLIEGO DE PRESCRIPCIONES TÉCNICAS PARTICULARES (PPTP)",
          external="Herramientas → PPTP.", children=tuple(
        Entry(title) for title in (
            "Capítulo I: Disposiciones Generales", "Capítulo II: Condiciones que deben reunir los Materiales",
            "Capítulo III: Ejecución de las Obras", "Capítulo IV: Medición y Abono de las Obras",
            "Capítulo V: Disposiciones Varias",
        )
    )),
    Entry("DOCUMENTO N.º 4: PRESUPUESTO", external=_REPORTS, children=(
        Entry("Mediciones"), Entry("Cuadro de Precios n.º 1"), Entry("Cuadro de Precios n.º 2"),
        Entry("Presupuestos Parciales"), Entry("Presupuesto General (Resumen)", children=tuple(
            Entry(title) for title in (
                "Presupuesto de Ejecución Material (PEM)", "Gastos Generales (GG)", "Beneficio Industrial (BI)",
                "Presupuesto de Ejecución por Contrata sin IVA", "IVA", "Presupuesto Base de Licitación (PBL)",
                "Valor Estimado del Contrato (VEC) y Presupuesto para Conocimiento de la Administración",
            )
        )),
    )),
    Entry("DOCUMENTACIÓN COMPLEMENTARIA Y TRAMITACIÓN MUNICIPAL", external=_PROCEDURES, children=tuple(
        Entry(title) for title in (
            "Ficheros editables e intercambio (BC3, DWG/DXF/SHP, IFC)", "Autorizaciones Sectoriales Previas",
            "Tramitación Ambiental (Documento Ambiental / Estudio de Impacto Ambiental / Calificación Ambiental)",
            "Visado Colegial o Declaración Responsable de Habilitación Profesional",
            "Acta de Replanteo Previo", "Informe de Supervisión de Proyectos",
        )
    )),
)


def template_paths(entries: tuple[Entry, ...] = CATALOG, folder: Path = Path()) -> dict[Path, Entry]:
    """Enumera las plantillas copiables con su ruta dentro del paquete."""
    result = {}
    for entry in entries:
        if entry.external:
            continue
        if entry.outline:
            result[folder / entry.filename] = entry
        elif entry.children:
            result.update(template_paths(entry.children, folder / entry.key))
    return result


def copy_templates(
    destination: Path, selected: list[Path], *,
    record: Callable[[list[Path]], None] | None = None,
    prepare: Callable[[Path, BinaryIO, str], None] | None = None,
) -> tuple[int, int]:
    """Copia solo rutas del catálogo, sin sobrescribir y con comprobación previa."""
    destination = destination.resolve(strict=True)
    if not destination.is_dir():
        raise NotADirectoryError(destination)
    allowed = template_paths()
    files = []
    for relative in dict.fromkeys(selected):
        if relative not in allowed:
            raise ValueError(f"No se puede copiar este elemento: {relative}")
        source = TEMPLATES / relative
        if not source.is_file():
            raise FileNotFoundError(f"Falta la plantilla: {source}")
        target = destination / relative
        if not target.parent.resolve().is_relative_to(destination):
            raise ValueError(f"La carpeta de salida queda fuera del destino: {target.parent}")
        files.append((source, target, allowed[relative].title))
    created: list[Path] = []
    skipped = 0
    try:
        for source, target, title in files:
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as original:
                try:
                    output = target.open("xb")
                except FileExistsError:
                    skipped += 1
                    continue
                created.append(target)
                with output:
                    if prepare is None:
                        copyfileobj(original, output)
                    else:
                        prepare(source, output, title)
        if created and record is not None:
            record([target.relative_to(destination) for target in created])
    except (OSError, ValueError, ParseError, BadZipFile, KeyError):
        for target in created:
            target.unlink(missing_ok=True)
        raise
    return len(created), skipped
