# Gestor de Proyectos

Aplicación de escritorio para Windows que reúne el presupuesto, la documentación y el seguimiento económico de una obra.

[Descargas](https://github.com/mcobosb/gestor-de-proyectos/releases) · [Manual de usuario](docs/MANUAL_USUARIO.md) · [Guía detallada](docs/GUIA_DETALLADA.md) · [Historial de cambios](CHANGELOG.md)

## Release v2.1.0

- **RCDs:** previsiones de gestión, medidas preventivas y destinos por LER, con un catálogo local ampliable de centros de gestión e incorporación al estudio PDF/ODT.
- **Documentación:** mejoras de trazabilidad, estilos e índices propios de cada anejo dentro del documento maestro.
- **Cajetines de planos:** vinculación de la plantilla y guía de identificadores estándar para sus campos SVG.

## Otras funciones principales

- **Atomización del proyecto:** los capítulos y las partidas se guardan en archivos independientes. Los proyectos anteriores siguen siendo compatibles y se pueden migrar al nuevo formato.
- **Trabajo colaborativo:** comparte una carpeta de proyecto, consulta quién está activo y edita con bloqueos por sección, detección de conflictos y cifrado opcional.
- **Visor BIM:** abre modelos IFC en 3D con árbol, propiedades, selección, aislamiento y cortes. Revisa las equivalencias IFC 4.3 → BCCA y añade mediciones al presupuesto con trazabilidad por `GlobalId`.
- **Presupuestos BC3 (FIEBDC-3):** importa presupuestos y edita capítulos, partidas, mediciones, descomposiciones y precios.
- **Bases de precios:** carga BCCA u otras bases BC3, busca por código o descripción y copia partidas al presupuesto. Las bases se descargan por separado.
- **Estructura documental:** consulta el índice de un proyecto de obra civil y copia las plantillas ODT seleccionadas.
- **Justificación de precios:** prepara un anejo con la composición de precios y los costes indirectos.
- **Planificación:** organiza tareas y plazos, genera el diagrama de Gantt y expórtalo a PDF u ODT.
- **Certificaciones:** registra previsiones y certificaciones mensuales y compara la ejecución con el presupuesto.
- **Facturación:** consulta facturas PDF, asigna importes a recursos y revisa costes y desviaciones.
- **Residuos de construcción y demolición (RCDs):** estima residuos, revisa su clasificación LER y añade su gestión al presupuesto.
- **Seguridad y Salud:** prepara el estudio o estudio básico (ESS/EBSS) y su capítulo presupuestario.
- **Pliego técnico (PPTP):** genera el pliego por capítulos a partir de fichas, con revisión de normativa y precios.
- **Consultas NTP, UNE y LER:** busca referencias y códigos desde catálogos independientes del presupuesto.
- **Informes y exportación:** genera documentos en PDF u ODT editable, con cabeceras y firmas configurables, y exporta datos a ODS o CSV.
- **Configuración de obra:** registra el emplazamiento, los municipios, los códigos CPV de construcción y los datos del proyecto.

## Primeros pasos

1. Descarga el instalador para Windows desde [Publicaciones](https://github.com/mcobosb/gestor-de-proyectos/releases).
2. Crea un proyecto o importa un presupuesto BC3 desde **Archivo**.
3. Si necesitas copiar partidas o traducir mediciones IFC a BCCA, carga una base BC3 desde **Archivo → Añadir base de referencia BCCA…**.

Para [ejecutar desde el código fuente](docs/GUIA_DETALLADA.md#instalación-y-arranque) o [compilar el instalador](docs/GUIA_DETALLADA.md#crear-el-instalador-de-windows), consulta la guía detallada.

[Licencia MIT](LICENSE). Revisa las mediciones, los precios y la documentación generada antes de utilizarlos en obra.
