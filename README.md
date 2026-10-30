# Gestor de Proyectos

Aplicación de escritorio para Windows que permite trabajar con presupuestos
FIEBDC-3 (BC3), editar mediciones y descomposiciones, planificar obras y gestionar
certificaciones mensuales.

## Versión 1.5.1 — Facturación

- Facturación por recurso con filtros de materiales, maquinaria y RCDs.
- Asignación de importes en tabla y comparación global o acumulada por mes.
- Visor PDF integrado junto a la facturación, con páginas y zoom.

- Informes PDF y ODT editables con estilos, cabeceras y firmas configurables.
- Informe de mediciones y exportación del informe de desviaciones en PDF u ODT.
- Buscador del proyecto, filtros de recursos y expansión de capítulos y partidas.
- Resumen y descripción en mayúsculas al crear, duplicar o incorporar partidas;
  las bases originales y los proyectos existentes no se modifican al abrirlos.

### Seguridad y salud

- **Herramientas → Seguridad y salud** propone partidas según los riesgos de
  las UO BCCA y permite revisarlas antes de crear el capítulo correspondiente.
- Porcentaje orientativo configurable y matriz CSV incluida en la aplicación.
- Informe PDF del capítulo con presupuesto y mediciones.
- La selección y el porcentaje aceptados se guardan en JSON; al reabrir la
  herramienta se recuperan sin consultar de nuevo la matriz ni recalcular el capítulo.

### Gestión de RCDs

- **Herramientas → RCDs**, como primera opción: datos de cálculo editables y
  revisión de residuos detectados en las partidas.
- Marcas X manuales y evidencias de detección, guardadas con el proyecto.
- Al guardar RCDs, capítulo Gestión de residuos incorporado al presupuesto
  con niveles, tipologías, mediciones, precios e importes, sin duplicados.
- Presupuesto de gestión tabulado en PDF u ODT, con el formato y la plantilla
  configurados. Modelo revisado a partir de las tres hojas del ODS aportado;
  coeficientes, precios, mínimos y complemento Nivel I editables.

La estimación debe revisarse por el técnico responsable; no acredita cumplimiento
normativo ni sustituye la identificación de residuos peligrosos. El ODS de trabajo
no es necesario para ejecutar la herramienta y no se incluye en el instalador.

Si un presupuesto BC3 ya incluye capítulos de RCDs o de Seguridad y Salud, su
herramienta correspondiente queda desactivada para evitar duplicarlos o recalcularlos.
Se reconocen por el nombre o código del capítulo (residuos, escombros, RCDs,
seguridad y salud, seguridad e higiene o prevención de riesgos).
Los capítulos generados por este programa conservan el acceso: al abrir la
herramienta desde su proyecto JSON se cargan los datos guardados sin recalcular
ni modificar las partidas. Los cambios se aplican al aceptarlos o guardarlos
expresamente en la herramienta.

## 🚀 Release v1.1.0 — ¡Primeros pasos!

¡Hola a todos! 👋

Aunque la versión marque **v1.1.0**, este es en realidad nuestro **primer
lanzamiento oficial**. El proyecto nace con muchas ganas, dedicación y cariño,
y queremos compartir este primer hito con vosotros.

Por ahora incluye lo básico: todavía no está todo lo fino que nos gustaría,
pero ya es funcional y permite trabajar con las tareas clave que se usan en
aproximadamente el 95 % de las licitaciones. La intención es ir a lo esencial,
sin añadir complejidad innecesaria.

### ✨ ¿Qué vas a encontrar en esta primera entrega?

- **Puesta a punto:** La estructura principal lista para empezar a trabajar.
- **Funcionalidades core:** Herramientas iniciales para presupuestos,
  mediciones, planificación y certificaciones.
- **Primeros ajustes:** Un entorno funcional y estable para probarlo y
  aprovecharlo desde el primer día.

### 💖 Un mensaje de agradecimiento

Si estás leyendo esto o probando el software, **gracias de corazón** por darle
una oportunidad. Cualquier idea, reporte de fallo o sugerencia es bienvenida.
Atenderemos las peticiones siempre que respeten la filosofía del software:
centrarse en lo que realmente se utiliza y mantener el flujo de trabajo ágil y
sencillo.

Puedes descargar la versión publicada desde la
[release v1.1.0](https://github.com/mcobosb/gestor-de-proyectos/releases/tag/v1.1.0).

Esperamos que os sea muy útil. ¡Esto es solo el comienzo!

*Hecho con cariño y muchas tazas de café.* ☕

## Instalación y arranque

En Windows se puede crear e instalar una versión compilada con Nuitka. Una vez
generado, el instalador `GestorDeProyectos-Setup-1.5.1.exe` no requiere Python.
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
2. Añade una base de precios en formato BC3 si necesitas incorporar partidas de referencia.
3. Selecciona una partida y pulsa una celda de **Mediciones** para editarla.
4. Completa **Archivo → Configuración** con la autoría y porcentajes.
5. Completa **Herramientas → RCDs** y guarda para incorporar la gestión de residuos al presupuesto.
6. Guarda el proyecto en JSON y genera los documentos desde **Informes**.
7. Continúa con **Herramientas → Planificación**, **Certificaciones** y **Facturación** para el seguimiento.

**Guardar** (Ctrl+S) conserva los cambios en el proyecto **JSON**, no en el BC3
original. No hay autoguardado general; el indicador muestra los cambios pendientes.
No se crean copias de seguridad automáticas al abrir o importar archivos.
**Archivo → Guardar como (JSON)…** permite elegir siempre otra carpeta y nombre;
después, **Guardar** y **Ctrl+S** utilizan la nueva ubicación.
**Deshacer** (Ctrl+Z) revierte hasta las últimas 50 acciones de la sesión.

Consulta el [manual de usuario](docs/MANUAL_USUARIO.md) para el procedimiento completo.

## Bases de precios de referencia

Las bases de precios reúnen partidas, descompuestos y precios básicos de materiales,
maquinaria y mano de obra. Puedes utilizarlas como referencia para preparar tu
presupuesto; no se incluyen en la aplicación y deben descargarse por separado.

Estas son algunas fuentes oficiales donde consultar las bases y sus opciones de descarga:

También puedes abrir estas tres webs desde **Información → Bases de precios de
referencia** en la pantalla principal: al situarte sobre esa opción se despliega
el submenú y, al pulsar una base, se abre su página en el navegador.

| Ámbito | Base y organismo | Consulta y descarga |
|---|---|---|
| Andalucía (JA) | Base de Costes de la Construcción de Andalucía (BCCA), Junta de Andalucía | [Ediciones y descargas de la BCCA](https://www.juntadeandalucia.es/organismos/viviendajuventudyordenaciondelterritorio/areas/vivienda-rehabilitacion/planes-instrumentos/paginas/vivienda-bcca.html) |
| Extremadura | Base de precios de la construcción, Junta de Extremadura | [Edición 2026: publicación con descarga en BC3](https://www.juntaex.es/w/base-precios-construccion-2026) |
| Madrid | Base de Datos de la Construcción, Comunidad de Madrid | [Información oficial](https://www.comunidad.madrid/vivienda/base-datos-construccion) y [acceso a la base](https://gestiona.comunidad.madrid/bdco_inter/) |

Para incorporarlas al programa:

1. Accede a la web del organismo y elige la edición que necesites.
2. Descarga la base en **BC3 (FIEBDC-3)**, cuando esté disponible. Si se entrega
   comprimida, extrae primero el archivo `.bc3`. Los documentos PDF y los archivos
   de formatos propietarios no sustituyen al BC3 para esta carga.
3. En el programa, abre **Archivo → Añadir base de referencia BCCA…** y selecciona
   el archivo `.bc3`. Aunque el menú menciona BCCA, permite seleccionar bases BC3
   de otros organismos.
4. Consulta la base en el panel de referencia y copia las partidas o precios
   básicos que necesites a tu presupuesto.

Revisa siempre la fecha, el ámbito territorial, las condiciones de uso y los
criterios de precios de cada publicación. Los importes son una referencia y deben
adaptarse a las condiciones reales de la obra. Los formatos y requisitos de
acceso o descarga dependen de cada organismo; los enlaces apuntan a sus páginas,
no a copias redistribuidas por este proyecto.

## Dependencias

El archivo [requirements.txt](requirements.txt) contiene las dependencias de uso:

| Paquete | Uso |
|---|---|
| Pillow | Icono e imágenes de la interfaz |
| odfpy | Exportación de hojas de cálculo ODS |
| openpyxl | Conversión independiente de planificación a XLSX |
| ReportLab | Informes PDF |
| Matplotlib | Diagramas de Gantt |
| pdfplumber | Lectura local de tablas de facturas PDF |
| pypdfium2 | Visor PDF integrado de facturas |

Los informes también se generan como ODT editable con los módulos estándar de Python.
El formato se elige en **Archivo → Configuración…** y, de inicio, es PDF.
Tkinter y los módulos restantes forman parte de Python. Si `python -m tkinter`
no abre una ventana, revisa la instalación de Python antes de iniciar la app.

## Organización

| Archivo o carpeta | Función |
|---|---|
| [bc3_gui.py](bc3_gui.py) | Entrada de la aplicación, presupuesto y mediciones |
| [rcd.py](rcd.py) | Estimación, revisión y presupuesto de gestión de RCDs |
| [planning.py](planning.py) | Planificación y secuencia de actividades |
| [certifications.py](certifications.py) | Previsiones y certificaciones mensuales |
| [invoicing.py](invoicing.py) | Facturas PDF y matriz editable de costes por recurso |
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

Este repositorio **no contiene presupuestos BC3 reales, bases BCCA, proyectos JSON,
plantillas PDF originales, historial ni configuración personal**.
El programa funciona con los archivos que aporte cada usuario.

Los informes se reconstruyen mediante código; no requieren abrir los PDF
originales de ejemplo. El recurso gráfico del logo se encuentra en
[iconos/logo_odt.jpg](iconos/logo_odt.jpg).
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

Las pruebas de interfaz necesitan un escritorio disponible. El presupuesto
[tests/test.bc3](tests/test.bc3) contiene datos ficticios: se conserva la estructura
de capítulos, partidas, recursos y mediciones del archivo aportado, pero se
sustituyen metadatos, códigos, unidades, textos, precios, cantidades y dimensiones.
No incluye los datos originales ni una tabla de correspondencias.
Las pruebas de presupuesto, cálculos, PDF y reapertura utilizan este archivo y no
se omiten si falta: su ausencia es un error.

La prueba adicional de la base BCCA utiliza `tests/BCCA2023_V02.bc3` si se aporta
localmente y se omite si no está disponible. Esa base no se modifica ni se
incorpora al repositorio; continúa excluida por las reglas de Git.

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

El instalador generado aparece en `dist\GestorDeProyectos-Setup-1.5.1.exe`.
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
