# Gestor de Proyectos (v1.0.0)

Aplicación de escritorio para Windows que permite trabajar con presupuestos
FIEBDC-3 (BC3), editar mediciones y descomposiciones, planificar obras y gestionar
certificaciones mensuales.

## Instalación y arranque

En Windows se puede crear e instalar una versión compilada con Nuitka. Una vez
generado, el instalador `GestorDeProyectos-Setup-1.0.0.exe` no requiere Python.
Incluye los componentes necesarios, pero no los archivos fuente `.py`. La
compilación Nuitka dificulta recuperar el código, aunque no puede impedir
completamente la ingeniería inversa.

Para ejecutar desde el código fuente se necesita Windows, Python 3.11 o
posterior con Tkinter y una sesión gráfica.
No se necesita Excel para exportar hojas de cálculo ni para generar el Gantt
desde la aplicación.

Desde PowerShell, en la carpeta descargada:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe bc3_gui.py
```

En modo desarrollo, la carpeta debe permitir escritura porque las preferencias
se guardan junto al código. La versión instalada guarda preferencias e historial
en `%APPDATA%\Gestor de Proyectos`. No es necesario activar el entorno virtual.

Al iniciar se muestra la imagen `iconos/Arranque.jpg`, centrada y sin marco,
durante dos segundos. Después se cierra y se abre la ventana principal.
La imagen también se incluye en la versión instalada.

## Uso rápido

1. Abre **Archivo → Nuevo proyecto** o importa tu presupuesto BC3.
2. Añade una base BCCA si necesitas incorporar partidas de referencia.
3. Selecciona una partida y pulsa una celda de **Mediciones** para editarla.
4. Completa **Archivo → Configuración** con la autoría y porcentajes.
5. Guarda el proyecto en JSON y genera los documentos desde **Informes**.
6. Utiliza **Herramientas → Planificación** y **Certificaciones** para el seguimiento.

Consulta el [manual de usuario](docs/MANUAL_USUARIO.md) para el procedimiento completo.

## Dependencias

El archivo [requirements.txt](requirements.txt) contiene las dependencias de uso:

| Paquete | Uso |
|---|---|
| Pillow | Icono e imágenes de la interfaz |
| odfpy | Exportación de hojas de cálculo ODS |
| openpyxl | Conversión independiente de planificación a XLSX |
| ReportLab | Informes PDF |
| Matplotlib | Diagramas de Gantt |

Los informes también se generan como ODT editable con los módulos estándar de Python.
El formato se elige en **Archivo → Configuración…** y, de inicio, es PDF.
Tkinter y los módulos restantes forman parte de Python. Si `python -m tkinter`
no abre una ventana, revisa la instalación de Python antes de iniciar la app.

## Organización

| Archivo o carpeta | Función |
|---|---|
| [bc3_gui.py](bc3_gui.py) | Entrada de la aplicación, presupuesto y mediciones |
| [planning.py](planning.py) | Planificación y secuencia de actividades |
| [certifications.py](certifications.py) | Previsiones y certificaciones mensuales |
| [budget_reports.py](budget_reports.py) | Configuración, cálculos e informes |
| [report_layout.py](report_layout.py) | Estilos y maquetación PDF compartida |
| [odt_reports.py](odt_reports.py) | Exportación editable de informes ODT |
| [ods_export.py](ods_export.py) | Exportación editable de hojas de cálculo ODS |
| [ui_theme.py](ui_theme.py) | Tema de la interfaz |
| [figura_gantt.py](figura_gantt.py) | Dibujo del Gantt; utilizado por planificación |
| [bc3_to_xlsx_plan.py](bc3_to_xlsx_plan.py) | Conversor independiente de planificación a XLSX |
| [iconos/](iconos/) | Icono de la aplicación y logo institucional de los informes |
| [plantillas/plantilla.odt](plantillas/plantilla.odt) | Cabecera y pie personal de los informes de certificación |
| [docs/](docs/) | Manual de usuario |

Los conversores antiguos de imágenes y las imágenes que no utiliza la aplicación
se han trasladado a una carpeta externa; no son necesarios para ejecutarla.

### Datos no distribuidos

Este repositorio **no contiene presupuestos BC3, bases BCCA, proyectos JSON,
plantillas PDF originales, historial ni configuración personal**.
El programa funciona con los archivos que aporte cada usuario.

Los informes se reconstruyen mediante código; no requieren abrir los PDF
originales de ejemplo. El recurso gráfico de la cabecera de los informes
generales se encuentra en [iconos/report_header_logo.png](iconos/report_header_logo.png).
Los informes de certificación pueden usar la cabecera y el pie de
[plantillas/plantilla.odt](plantillas/plantilla.odt), opción que se controla
desde **Archivo → Configuración…**. La plantilla se incluye en el instalador.

La aplicación arranca siempre vacía, aunque haya BC3 o proyectos JSON en su
carpeta. Abre un proyecto desde **Archivo → Recientes** o **Abrir proyecto (JSON)**,
o utiliza **Importar presupuesto BC3**. Las bases se añaden mediante
**Archivo → Añadir base de referencia BCCA**. El historial y la configuración
guardada se conservan, pero no se abre ningún proyecto automáticamente.

## Pruebas y documentación del código

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m unittest discover -v
```

Las pruebas de interfaz necesitan un escritorio disponible. Algunas pruebas
adicionales de regresión utilizan los BC3 locales originales y se omiten cuando
no están presentes. Las pruebas sintéticas de lectura BC3, cálculos, PDF,
mediciones, planificación y configuración no requieren esos datos privados.

Todas las funciones y métodos incluyen docstrings. La suite comprueba su
presencia y verifica que las dependencias y recursos internos siguen accesibles.
Se puede consultar la documentación integrada con `help()` o `python -m pydoc`.

## Crear el instalador de Windows

La distribución se compila en Windows con Python de 64 bits, Nuitka, MinGW64
(descargado por Nuitka durante la primera compilación) e Inno Setup 7 o 6.
Instala Inno Setup y añade `ISCC.exe` al `PATH` o déjalo en su ruta
predeterminada. El script detecta automáticamente las instalaciones habituales
de Inno Setup 7 y 6.
Después, desde PowerShell en la carpeta del proyecto:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
.\build_installer.ps1
```

El instalador generado aparece en `dist\GestorDeProyectos-Setup-1.0.0.exe`.
El script usa automáticamente `.\.venv\Scripts\python.exe` si existe; en caso
contrario, utiliza el comando `python` disponible en el sistema.
La distribución incluye la licencia y el manual, pero no los presupuestos ni
los archivos fuente. El ejecutable compilado no equivale a cifrado y no ofrece
protección absoluta frente a ingeniería inversa. La licencia MIT permite
distribuir binarios siempre que se conserve el aviso y el texto de licencia.

Los scripts auxiliares también admiten uso independiente:

```powershell
python bc3_to_xlsx_plan.py "C:\datos\obra.bc3" "C:\datos\planificacion.xlsx"
python figura_gantt.py "C:\datos\planificacion.xlsx" "C:\datos\plan_de_trabajo"
```

El segundo comando necesita fechas calculadas en el Excel. Puede utilizar
LibreOffice si está instalado o los valores guardados previamente por Excel.
Esto no afecta al Gantt generado directamente desde la interfaz.

## Licencia y responsabilidad

El código se distribuye bajo la [licencia MIT](LICENSE).
Copyright © 2026 Manuel Cobos Budia.

El software se proporciona sin garantías. Antes de utilizar los resultados,
comprueba datos, mediciones, precios, porcentajes y documentación generada.
La aplicación no sustituye la revisión técnica ni la responsabilidad profesional.

Los nombres, logotipos y signos institucionales identifican a sus respectivos
titulares. La licencia del código no concede derechos de marca ni implica
respaldo institucional a modificaciones o redistribuciones.
