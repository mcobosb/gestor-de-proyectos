# Gestor de Proyectos (v1.0.0)

Aplicación de escritorio para Windows que permite trabajar con presupuestos
FIEBDC-3 (BC3), editar mediciones y descomposiciones, planificar obras y gestionar
certificaciones mensuales.

## Instalación y arranque

Requisitos: Windows, Python 3.11 o posterior con Tkinter y una sesión gráfica.
No se necesita Excel para exportar hojas de cálculo ni para generar el Gantt
desde la aplicación.

Desde PowerShell, en la carpeta descargada:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe bc3_gui.py
```

La carpeta debe permitir escritura, porque la aplicación guarda preferencias
locales junto al código. No es necesario activar el entorno virtual.

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
| openpyxl | Lectura y escritura de Excel |
| ReportLab | Informes PDF |
| Matplotlib | Diagramas de Gantt |

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
| [ui_theme.py](ui_theme.py) | Tema de la interfaz |
| [figura_gantt.py](figura_gantt.py) | Dibujo del Gantt; utilizado por planificación |
| [bc3_to_xlsx_plan.py](bc3_to_xlsx_plan.py) | Parámetros usados por planificación y conversor Excel independiente |
| [iconos/](iconos/) | Icono de la aplicación y logo institucional de los informes |
| [docs/](docs/) | Manual de usuario |

Los conversores antiguos de imágenes y las imágenes que no utiliza la aplicación
se han trasladado a una carpeta externa; no son necesarios para ejecutarla.

### Datos no distribuidos

Este repositorio **no contiene presupuestos BC3, bases BCCA, proyectos JSON,
plantillas PDF originales, historial ni configuración personal**.
El programa funciona con los archivos que aporte cada usuario.

Los informes se reconstruyen mediante código; no requieren abrir los PDF
originales de ejemplo. El recurso gráfico de la cabecera se encuentra en
[iconos/report_header_logo.png](iconos/report_header_logo.png).

Si hay un presupuesto BC3 en la carpeta de la aplicación, se carga al arrancar.
Los BC3 cuyo nombre empieza por `BCCA` se consideran bases de referencia. Para
evitar la carga automática, guarda los datos en otra carpeta e impórtalos desde
**Archivo**.

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
