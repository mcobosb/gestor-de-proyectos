# Manual de usuario

## Gestor de Proyectos (v1.5.0)

Herramienta para preparar presupuestos, mediciones, planificación de obra y
certificaciones. Los ejemplos de rutas de este manual son ficticios.

## 1. Instalar y abrir

Instala Python 3.11 o posterior para Windows con Tkinter. Descarga el repositorio
y descomprímelo en una carpeta donde puedas escribir.

Abre PowerShell en esa carpeta y ejecuta:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe bc3_gui.py
```

En las siguientes ocasiones basta con ejecutar el último comando. Mantén juntos
los módulos Python y la carpeta `iconos`.

El idioma se puede cambiar desde **Archivo → Configuración… → Idioma**.
Están disponibles español, inglés y francés. La preferencia se guarda en el
archivo global `preferencias.json` y se aplica sin reiniciar la aplicación.

## 2. Crear o abrir un proyecto

La aplicación arranca siempre sin proyecto ni bases cargadas, aunque conserve
archivos de ejemplo en su carpeta. El historial de recientes y la configuración
guardada se mantienen; los datos se rellenan al elegir un proyecto.

- **Archivo → Nuevo proyecto**: introduce un nombre y comienza un presupuesto vacío.
- **Archivo → Importar presupuesto BC3…**: selecciona un BC3 de trabajo.
- **Archivo → Abrir proyecto (JSON)…**: recupera un proyecto guardado.
- En **Archivo → Recientes** aparecen hasta cinco rutas utilizadas anteriormente.

Al importar un BC3, la app utiliza el JSON del mismo nombre si existe junto a él.
En caso contrario crea un JSON de trabajo en esa ubicación. Guarda copias de
seguridad de ambos antes de trabajar sobre información importante.

Los BC3 y JSON no se incluyen en la descarga del programa. Si al abrirlo no hay
datos, utiliza **Nuevo proyecto** o **Importar presupuesto BC3**.

## 3. Trabajar con capítulos y partidas

El panel del proyecto contiene capítulos y partidas. El panel de referencia
permite consultar bases de precios.

Al crear, duplicar o incorporar partidas desde una base de referencia o un
presupuesto BC3, el resumen y la descripción se convierten a mayúsculas.
También se aplica a las partidas generadas de RCDs y transporte. Se conservan
los códigos, unidades y datos originales de las bases; abrir un proyecto JSON
existente no cambia sus textos.

1. Añade una base desde **Archivo → Añadir base de referencia BCCA…**.
   Las tres últimas bases usadas se conservan en **Archivo → Bases de
   referencia recientes** para poder abrirlas de nuevo.
2. Localiza una partida en la referencia y añádela al presupuesto con la flecha
   hacia la izquierda o arrastrándola al proyecto. Si seleccionas un capítulo,
   la flecha o el arrastre incorporan el capítulo con todas sus partidas.
3. Usa **+** para elegir entre añadir un capítulo o una partida manual. La
   partida se añade al capítulo seleccionado; si no hay selección, se utiliza
   el último capítulo. Usa **×** para eliminar la selección.
4. Las flechas **↑** y **↓** cambian el orden.
5. Selecciona una partida para consultar sus mediciones, descripción y recursos.
6. Selecciona un capítulo y usa **✎** para cambiar su nombre; selecciona una
   partida y usa el mismo botón para editar sus datos.
7. Usa **⧉** para duplicar la partida seleccionada. La copia conserva sus datos
   técnicos, aparece justo debajo con un código terminado en `*` y se resalta en
   amarillo: debes modificarla y revisarla manualmente; elimina `*` cuando
   hayas terminado. La planificación y las certificaciones no se copian.

La columna **Tipo** permite modificar la naturaleza de partidas y recursos.
Revisa los tipos porque intervienen en los listados de materiales y precios.
Al pasar el ratón por los botones se muestra su ayuda.

El total del presupuesto aparece a la derecha de la barra azul y siempre
corresponde al proyecto completo. El buscador situado bajo el panel del proyecto
filtra por código, resumen y descripción; **Esc** borra la búsqueda.

Junto a editar y duplicar, **↕** expande o contrae todos los capítulos y partidas.
Los botones **⚙**, **♟** y **▧** muestran, respectivamente, maquinaria, mano de
obra y materiales en una tabla con una fila por recurso. El capítulo y la unidad
de obra aparecen en columnas a su izquierda, en vez de en filas superiores.
Solo un filtro de naturaleza puede estar activo; pulsa otra vez para desactivarlo
y recuperar el árbol de capítulos y partidas.
Puedes combinarlo con el buscador. Estos controles solo cambian la vista:
no alteran el presupuesto ni sus importes.

Sobre el marco de las bases de referencia aparecen también **⚙**, **♟** y **▧**.
Estos filtros son independientes de los del proyecto y se combinan con el
buscador de la referencia. Muestran una fila por uso del recurso, con la base,
el capítulo y la unidad de obra a su izquierda, incluidos los recursos de
descomposiciones todavía sin desplegar. Los recursos sin capítulo o partida
se muestran con ese contexto vacío. Se muestran hasta 500 resultados;
acota la búsqueda si se alcanza ese límite. Pulsa de nuevo el filtro activo
para volver al árbol habitual.

Para copiar un precio básico (material, maquinaria o mano de obra), selecciona
la partida de destino en tu presupuesto y el recurso en la referencia; pulsa
la flecha hacia la izquierda e introduce su cantidad por unidad de obra.
También puedes arrastrarlo sobre la partida o uno de sus componentes.
Se copia al descompuesto con su código, unidad, descripción, tipo y precio,
sin modificar la base original. Si el código ya existe en esa partida, edita
su cantidad en lugar de añadirlo otra vez.

En **Costes y rendimientos**, haz doble clic en la descripción de un componente
para modificarla solo en esa partida. Los textos editados y los precios básicos
copiados se guardan en el JSON del proyecto y se usan en los informes.
Selecciona un componente en esta tabla o en el árbol y pulsa **↑** o **↓**
para moverlo dentro de su descompuesto. Los porcentajes se recalculan según
su nueva posición; las líneas calculadas de redondeo e indirectos no se mueven.
Estas operaciones admiten **Deshacer**.

## 4. Editar las mediciones

1. Selecciona una partida del presupuesto.
2. Abre la pestaña **Mediciones**.
3. Pulsa una celda de descripción, unidades, longitud, anchura, altura o K.
4. Escribe el valor y pulsa **Enter**, o sal de la celda para confirmarlo.
5. Pulsa **Esc** para cancelar la edición.

Puedes escribir decimales con coma o punto. El parcial se calcula automáticamente
y no se edita directamente. Las dimensiones vacías se omiten en el producto.
K multiplica el resultado; su valor neutro es 1.

**Añadir medición** crea una línea; **Eliminar medición** borra la seleccionada.
Cada cambio actualiza la cantidad e importe de la partida y los totales del
capítulo y presupuesto. Después guarda el proyecto.

La pestaña **Texto de la partida** permite editar su descripción extensa.
En **Costes y rendimientos**, el doble clic permite editar cantidades o precios
de recursos de la descomposición.

## 5. Configurar los informes

Abre **Archivo → Configuración…** y completa:

- Autoría, cargo o titulación, entidad o servicio.
- Localidad y texto de firma o fecha.
- Empresa constructora y responsable de la empresa constructora. En ambos
  informes de certificación aparecen el responsable, la empresa y el texto de
  firma o fecha (por defecto, «Fecha y firma electrónicas»), a la izquierda de
  la firma de la administración.
- Costes Indirectos (%): recargo sobre el precio directo de todas las partidas.
- Gastos generales, beneficio industrial e IVA.
- Baja de adjudicación para certificaciones.
- Divisa del proyecto: EUR, USD, GBP, CHF, CAD, AUD o una personalizada.
  Esta opción cambia el símbolo o código mostrado, pero no convierte importes;
  comprueba que los precios de entrada y de las bases de referencia usan la
  misma divisa.
- Formato de salida de los informes: PDF (predeterminado) u ODT editable.
- La ruta de una plantilla ODT que aporta la cabecera y el pie de todos los
  informes de presupuesto y certificaciones. Pulsa **Examinar…** para elegir
  el archivo. Si se deja vacía, se emplea la plantilla incluida con la
  aplicación. Esta plantilla no afecta a exportaciones Excel, CSV ni al
  diagrama de Gantt.

Las líneas horizontales definidas como bordes de párrafo en la cabecera o el
pie de la plantilla también se incluyen en los informes PDF y ODT, conservando
su grosor, color y trazo (continuo, discontinuo, punteado o doble).

Los informes PDF y ODT se generan en A4 con márgenes exteriores de **2,5 cm
en los cuatro lados**. La cabecera y el pie quedan dentro de esos límites;
el cuerpo reserva espacio adicional para no invadirlos. Las columnas se
ajustan al ancho disponible.

El ODT utiliza las fuentes, tamaños, interlineados, sangrías y alineaciones
del PDF como valores iniciales de sus estilos editables. También reproduce
las bandas de capítulo, las columnas, las cabeceras repetidas y los saltos
de capítulo y cuadro de precios. La paginación puede variar al editar texto,
cambiar estilos o abrir el documento con otras fuentes o procesadores.

El logo se repite en la cabecera de todas las páginas, incluidas las páginas
de continuación, con el ancho y alto definidos en la plantilla. La altura de
la cabecera se adapta al logo. Junto al nombre del proyecto se muestra
` · dd/mm/aaaa` con la fecha de generación del informe.

En **Mediciones**, la medición total queda alineada bajo la columna
**Parcial**, con la unidad inmediatamente a su derecha.

En el listado de elementales (**Materiales**), los grupos reúnen códigos
con los mismos tres primeros caracteres. Sus subtotales se destacan en
negrita y con el mismo fondo azul grisáceo que los capítulos, en PDF y ODT.

Los recursos porcentuales del BC3 se calculan sobre el coste acumulado de los
componentes anteriores, redondeando cada coste a céntimos con redondeo
comercial. Los ajustes pequeños de precisión se muestran como **Ajuste de
redondeo**, conservando el precio unitario importado; las discrepancias mayores
siguen siendo visibles. Los porcentajes sin ningún recurso base se consideran
un desglose pendiente: se conserva el precio de la partida sin inventar costes.
El porcentaje de costes indirectos de Configuración es adicional al precio
importado y se aplica una sola vez; cambiarlo no acumula recargos anteriores.
En el cuadro de precios 2 aparece en una línea independiente **Costes
indirectos (porcentaje %)** con su importe, sin agruparse con el resto de costes.

Los informes ODT asignan estilos de párrafo por función: `ReportTitle`,
`ProjectTitle`, `ReportBody`, `Heading1`–`Heading3`, `ChapterTitle`,
`PriceSectionTitle`, `AuxiliaryTitle`, `TableHeader`, `TableText`, `TableTotal`,
`TableItemTitle`, `ItemDescription`, `AmountInWords`, `ReportNote`,
`Justification`, `Signature`, `SignatureNote` y `FooterText`.
En LibreOffice Writer, abre **Estilos** (F11), modifica el estilo que quieras y
se actualizarán todos los textos del documento que lo utilicen. Las variantes
terminadas en `Right` heredan el estilo correspondiente y alinean los números
a la derecha. Los fondos y bordes se modifican por separado en las propiedades
de las tablas y sus celdas.

Los porcentajes deben estar entre 0 y 100. **Guardar** conserva los valores como
configuración inicial para nuevos proyectos y dentro del proyecto actual.
Los proyectos ya guardados mantienen su configuración propia.

Si los costes indirectos son distintos de cero, cada partida muestra una línea
«Costes Indirectos» en su detalle, descompuestos e informes, y su precio e importe
incluyen el recargo. También se aplica a partidas nuevas y sin descomposición.
Al cambiar el porcentaje se conserva el precio directo, sin acumular recargos;
al volver a cero se elimina la línea y se recupera el precio sin recargo.
Con el recargo activo, el formulario de edición solicita el **Precio directo**;
los costes indirectos se calculan automáticamente y se modifican desde configuración.

Los gastos generales y el beneficio industrial se calculan sobre el presupuesto
de ejecución material (PEM). El IVA se aplica después de sumar ambos.
En certificaciones, se descuenta primero la baja de adjudicación y después se
aplica el IVA. La baja no altera el presupuesto base de licitación del informe
de presupuesto.

## 6. Guardar

Usa el botón **Guardar** o **Ctrl+S** para actualizar el archivo actual.
**Archivo → Guardar como (JSON)…** abre siempre el selector para elegir carpeta
y nombre, aunque el proyecto ya tenga una ruta. Tras guardar correctamente,
esa será la ubicación utilizada por **Guardar** y **Ctrl+S**. Cancelar el selector
no cambia el proyecto ni su ruta.
El JSON conserva partidas, mediciones,
precios editados, planificación, certificaciones y configuración de informes.

El **BC3 es el presupuesto de origen**: se lee, pero no se reescribe al guardar.
El **JSON es el proyecto de trabajo**: para continuar con los cambios otro día,
abre ese JSON, no vuelvas a importar el BC3. Guardar no exporta un BC3 actualizado.

No hay autoguardado general. La barra muestra **Cambios sin guardar** o
**Sin cambios pendientes**. Al importar un BC3 se crea un JSON inicial;
algunas herramientas y la configuración también guardan al pulsar sus botones de
guardado. El resto de las ediciones queda en memoria hasta guardar el proyecto.

Los cambios de celda deben confirmarse con **Intro** (o cancelarse con **Escape**)
antes de guardar. Al cerrar la app o cambiar de proyecto,
si quedan cambios pendientes, puedes guardarlos, descartarlos o cancelar el cierre.
El guardado no sobrescribe el BC3 original.

No se crean copias de seguridad automáticas al abrir o importar archivos.
El BC3 de origen permanece intacto y el JSON se actualiza al guardar el proyecto.

### Deshacer

El botón **Deshacer** o **Ctrl+Z** revierte hasta las últimas **50 acciones**:
altas, bajas, duplicados, movimientos, ediciones de partidas, mediciones,
descompuestos, descripciones, tipos, configuración y cambios comunicados por
las herramientas. No guarda automáticamente ni revierte archivos ya escritos.
Puedes deshacer incluso después de guardar; para conservar ese estado debes
guardar de nuevo. El historial se reinicia al crear o abrir otro proyecto y
no se conserva al cerrar la aplicación. No hay botón Rehacer.

Al deshacer se cierran las ventanas de herramientas y configuración para que no
sigan editando partidas anteriores; puedes volver a abrirlas con el estado
restaurado. Las preferencias globales (por ejemplo, idioma y configuración inicial
para nuevos proyectos) y los informes exportados no forman parte del historial.

Si tras trasladar el proyecto falta su BC3 de origen, puede abrirse el JSON,
pero algunos informes de recursos necesitarán la base original o los precios
guardados. Conserva los archivos asociados junto al proyecto.

## 7. Generar presupuestos e informes

En el menú **Informes** de la ventana principal están:

| Opción | Contenido |
|---|---|
| Mediciones | Capítulos, código, unidad, resumen, descripción, desglose de mediciones y medición total, sin precios ni importes |
| Cuadro de precios 1 y 2 por capitulos | Precio unitario y cuadro agrupado de costes por capítulo |
| Descompuestos | Recursos y rendimientos de cada partida |
| Materiales | Materiales utilizados, cantidades acumuladas e importes |
| Precios Auxiliares | Descomposición de auxiliares empleados |
| Presupuesto mediciones | Partidas, líneas de medición e importes |
| Presupuesto resumen | Capítulos, PEM, GG, BI, IVA y presupuesto de licitación |

Selecciona el tipo y el destino del PDF. Al terminar, el programa intenta abrirlo
con la aplicación asociada. Si no puede abrirlo, indica dónde ha quedado guardado.
Puedes imprimirlo desde el visor PDF.

El menú **Imprimir y exportar** conserva la vista HTML del presupuesto,
la impresión, la exportación del presupuesto, recursos y descompuestos a hojas
ODS editables y las mediciones a CSV. La vista HTML es una salida adicional,
no el modelo PDF maquetado.

Los informes no inventan precios ausentes. Si falta un recurso, carga su base de
referencia o completa los precios necesarios antes de volver a generarlo.

## 8. Estimar residuos de construcción y demolición (RCDs)

Si el presupuesto importado ya tiene un capítulo de gestión de residuos, RCDs
o escombros no creado por la herramienta, **Herramientas → RCDs** queda
desactivada. No se recalculan sus importes ni se crea otro capítulo.
Lo mismo se aplica a **Herramientas → Seguridad y salud** si existe un capítulo
externo de seguridad y salud, seguridad e higiene o prevención de riesgos.

Los capítulos generados por el programa mantienen su herramienta habilitada.
Al abrirlos desde el proyecto JSON, RCDs recupera sus datos de cálculo y marcas;
Seguridad y Salud recupera la propuesta aceptada, incluidas las partidas
desmarcadas y el porcentaje. Abrir la ventana no vuelve a calcular ni modifica
el presupuesto. En JSON antiguos sin la revisión de Seguridad y Salud, se
muestran las partidas generadas ya guardadas en el presupuesto, sin consultar
de nuevo la matriz. Para aplicar cambios hay que aceptarlos o guardarlos
expresamente en la herramienta.

Abre **Herramientas → RCDs**, primera entrada del menú. El modelo procede de las
hojas **Resumen**, **Lista MAM** y **Presupuesto** del ODS aportado, pero no
necesita ese archivo para funcionar. Es una estimación revisable, no una
certificación de cantidades reales ni de cumplimiento normativo.

### Paso 1. Datos de cálculo

Completa los campos pendientes (o indica cero si no proceden) y revisa los
valores recuperados, cuya procedencia se muestra junto a cada campo:

- **Superficie afectada**: manual; no se suman superficies de partidas que
  podrían solaparse.
- **Volumen de excavación**: se propone la suma de las mediciones de partidas
  de excavación o desmonte en m³, excluyendo transporte, carga y relleno.
  No se deduce volumen a partir de superficie, toneladas u otras unidades.
- **Presupuesto sin gestión de residuos**: suma del PEM, excluyendo partidas
  o capítulos identificados como gestión de residuos por su descripción.
  Comprueba que no se hayan omitido ni incluido conceptos indebidamente.
- **Movimiento de tierras / trabajos previos**: estimación por las descripciones
  y capítulos; se conserva como dato de referencia, no se usa para la antigua
  fórmula B1 del ODS.
- Coeficiente volumen/superficie (inicial 0,15 m³/m²), densidad global,
  porcentajes de peso y densidades por grupo, precios de gestión y costes
  complementarios: todos revisables.

Los valores particulares de la obra del ODS (superficie, excavación e importes)
no se aplican automáticamente a nuevos proyectos. Los datos deben ser finitos
y no negativos; densidades y presupuesto deben ser mayores que cero. Los
porcentajes individuales admiten de 0 a 100. El porcentaje de tierras no se usa:
ese grupo parte directamente del volumen de excavación.

Pulsa **Aceptar y revisar residuos** para pasar al segundo paso en la misma
ventana. **Datos de cálculo** permite volver al formulario. Los cambios de ese
formulario solo se aplican cuando se aceptan.

### Copiar las demoliciones al transporte a vertedero

En el primer paso, pulsa **Llevar mediciones de demolición a transporte a
vertedero**. Se abre una tabla de revisión con cada línea de medición, su
partida de origen, cantidad, material/LER y factor de conversión:

- Se proponen partidas de demolición, retirada, levantamiento y desmontaje
  por su descripción o capítulo. Desmarca las que no procedan.
- Las partidas originales no se mueven ni se borran. Se copia el parcial de
  cada línea, conservando código, descripción y comentario de origen. Se
  omiten las líneas de subtotal; una partida sin desglose aporta su medición.
- En **m³**, el factor es 1. Para **m²**, introduce el espesor en metros; para
  **m**, la sección en m²; para **ud**, el volumen por unidad; para **t**, los
  m³ por tonelada. No se suponen espesores, densidades de conversión ni
  esponjamiento. No se permite copiar una línea incluida sin material y factor.
- Confirma el material y su código LER. Las coincidencias ambiguas se dejan
  pendientes. Los descuentos se conservan, pero el volumen neto por residuo
  no puede ser negativo.
- Elige la partida de transporte. Si hay varias, debes seleccionar una; el
  destino debe estar medido en m³. También puedes crear una partida nueva.
- Se mantienen las líneas manuales y los precios, rendimientos y descompuestos
  existentes. Al repetir, se sustituyen las líneas identificadas con
  `[RCD demolición]`, sin duplicarlas. Si cambias de destino, las líneas
  generadas se retiran del transporte anterior.

La partida automática tiene código único, unidad m³ y **precio inicial cero**.
Su descripción y un aviso recuerdan que debes **revisar costes, rendimiento,
descomposición, distancia, esponjamiento y condiciones de transporte y vertido**.
El cero no es un precio válido de presupuesto.

Al copiar se activan los **volúmenes revisados por material**, en sustitución
del reparto por superficie y porcentajes del ODS. La superficie, el coeficiente
de generación y la densidad global dejan de intervenir; las densidades de grupo
siguen sirviendo para calcular el peso. Se conserva el volumen de excavación,
salvo que se hayan revisado también mediciones del grupo tierras, que lo
sustituyen. Los campos del modelo que ya no intervienen pueden quedar vacíos.
Revisa el presupuesto sin gestión de residuos después de modificar el
transporte: no se sobrescribe un presupuesto introducido manualmente.

Las mediciones copiadas, sus conversiones y materiales se guardan con el
proyecto y aparecen en los informes PDF/ODT. Las marcas X del segundo paso
permiten excluir residuos del cálculo, pero no borran las líneas del transporte.
Si cambian las demoliciones, repite la copia para actualizar sus cantidades.
Cancelar la tabla de revisión no modifica el presupuesto.

### Paso 2. Revisar residuos y guardar

La tabla muestra las 57 filas del modelo con **X**, grupo, código LER, descripción
y la partida que motivó la detección:

- Se reconocen códigos LER explícitos o materiales mencionados en partidas de
  demolición, retirada, excavación, fresado, etc. Los suministros, por sí solos,
  no se consideran residuos.
- Una coincidencia de texto es orientativa y no una clasificación acreditada.
  Los residuos no reconocidos aparecen sin X.
- Pulsa la primera columna, **X** o **Espacio** sobre la fila seleccionada para
  incluir o excluir manualmente. Las evidencias se mantienen aunque cambies
  la marca y las selecciones manuales no se vuelven a detectar al reabrir.
- Revisa los peligrosos y los códigos LER antes de emitir el informe. El ODS
  repite ciertos códigos y sitúa aislamientos no peligrosos en el grupo
  «potencialmente peligrosos y otros»; se conservan sus filas diferenciadas,
  sin presentar esa agrupación como una clasificación legal actualizada.
- La vista inferior recalcula el presupuesto al cambiar las marcas.

El **disquete** o **Ctrl+S** guarda los datos, coeficientes, precios, selecciones
y evidencias dentro del JSON del proyecto. El disquete negro indica cambios
pendientes. Cerrar conserva las ediciones aceptadas en memoria; guarda el
proyecto para persistirlas.

### Incorporar la gestión de residuos al presupuesto

Al pulsar el **disquete** o **Ctrl+S dentro de RCDs**, se crea un capítulo nuevo
**Gestión de residuos** en el presupuesto y se guarda junto al proyecto:

- **Nivel I**: tierras y pétreos de excavación.
- **Nivel II**: naturaleza pétrea, no pétrea y potencialmente peligrosos/otros.
- **Otros costes**: complementos B1 y B2 y gestión B3.

Las cuatro tipologías se incorporan como partidas en **m³**, con su volumen y
precio de gestión. Los complementos se incorporan como **1 ud**, con su importe
calculado como precio. Se mantienen las siete filas, incluso las de importe
cero, sin añadir otra partida de total que duplicaría el coste.

Los importes de estas partidas se redondean individualmente a céntimos: sin
costes indirectos, el total del capítulo coincide con el informe RCDs. Si hay
**costes indirectos** configurados, se aplican además, con el mismo criterio
que en las demás partidas. El informe RCDs conserva los costes directos.
Las demás partidas mantienen su comportamiento de cálculo anterior.

Al volver a guardar se actualizan las mediciones y precios de las partidas
generadas, sin crear otro capítulo ni duplicarlas. También se conserva su
planificación y certificaciones. Una vez incorporado el capítulo, el guardado
normal del proyecto actualiza sus partidas con el último estado RCDs aceptado.
Las partidas manuales y el transporte existente no se sustituyen ni se borran:
revisa que no estén presupuestando ya los mismos costes de gestión.

Los códigos de capítulo y partidas se eligen sin sobrescribir códigos existentes.
Para cambiar mediciones o precios generados, edita los datos en RCDs: el próximo
guardado reemplaza las ediciones directas de esos campos. Si cancelas el
guardado o falla la escritura, el capítulo queda en memoria como cambio pendiente;
todavía debes guardar el proyecto para conservarlo en disco.

### Informe y cálculo revisado

**Generar informe RCDs** utiliza **PDF u ODT** y la **plantilla de cabecera y pie**
elegidos en **Archivo → Configuración**. Incluye la tabla económica de la hoja
Presupuesto (tipología, volumen, precio de gestión, importe y porcentaje del
presupuesto), datos de cálculo y residuos seleccionados con su procedencia.

- **A1**: volumen de tierras seleccionado × precio de gestión Nivel I.
- **A2**: costes de los grupos pétreo, no pétreo y peligrosos/otros.
  Cuando se han copiado demoliciones, se usan sus volúmenes revisados por
  material y por residuo marcado. En caso contrario se aplica el modelo:
  Peso estimado = superficie × coeficiente volumen/superficie × densidad
  global × porcentaje de peso del grupo. Volumen = peso / densidad del grupo.
  Un grupo participa si tiene al menos un residuo marcado: marcar varias filas
  del mismo grupo no multiplica su volumen ni reparte toneladas por código LER.
- **B1**: complemento Nivel I manual en euros; no se utiliza la fórmula
  incoherente del ODS basada en el umbral 86.000 €.
- **B2**: complemento necesario para alcanzar el mínimo configurado de Nivel II
  (inicial 0,4 % del presupuesto). Si A2 ya lo cubre, es cero.
- **B3**: porcentaje del presupuesto para costes de gestión, alquileres, etc.
- **Total**: suma de A1, A2, B1, B2 y B3. No incluye fianza ni IVA.

Se conservan los porcentajes de grupo del modelo, sin renormalizarlos cuando
se desmarcan residuos; no son porcentajes medidos de la obra. El texto del ODS
mencionaba 0,2 % para Nivel II, mientras su fórmula usaba 0,4 %; aquí el mínimo
es explícito y configurable, no una afirmación de exigencia normativa.

Volúmenes y precios se redondean a dos decimales antes de calcular los costes;
B2 se calcula sobre importes redondeados y el total suma las filas visibles.
Con los datos de ejemplo esto da 13.044,62 €, un céntimo menos que el ODS
original por su distinto redondeo de B2.

## 9. Planificar la obra

Abre **Herramientas → Planificación**.

En **Parámetros**, revisa la fecha de inicio, horas por jornada, escala, modalidad
del plan y festivos. Los festivos iniciales son orientativos: comprueba los del
municipio y los del periodo real de la obra.

En **Secuencia y plan de trabajo**:

- Define equipos o una duración manual cuando sea necesario.
- Configura la predecesora, relación y desfase de cada actividad.
- **FC**: fin-comienzo; **CC**: comienzo-comienzo; **FF**: fin-fin;
  **CF**: comienzo-fin.
- Marca las actividades que deban abarcar toda la obra.
- El encadenado automático enlaza las actividades en secuencia.
- Genera el diagrama de Gantt y guarda su PNG o PDF.

Revisa que la secuencia sea coherente y no tenga dependencias circulares.
El botón del disquete guarda la planificación mediante el proyecto.

## 10. Registrar certificaciones

Abre **Herramientas → Certificaciones**. La tabla muestra hasta 24 meses del
plan económico, con columnas de previsión y certificación.

1. Para usar una previsión como punto de partida, pulsa su cabecera **Plan** y
   después **Copiar plan seleccionado a su certificación**.
2. Haz doble clic en una celda de certificación para introducir el importe del mes.
3. Confirma la edición con **Enter**.
4. Consulta la desviación respecto al plan y escribe su justificación.
5. Guarda con el botón del disquete.

Las diferencias se redistribuyen sobre la previsión futura disponible. Si no
hay saldo suficiente para compensar un exceso, revisa la advertencia y justifícalo.
Comprueba siempre la planificación resultante.

### Informes de certificación

Pulsa primero la cabecera **Certificación** del mes que quieres documentar.
Después abre **Informes** en esa misma ventana:

- **Resumen certificaciones**: importes por capítulo, GG, BI, baja, IVA y líquido.
- **Certificaciones con mediciones**: partidas del mes y cantidad calculada como
  importe certificado dividido por precio unitario.

Ambos incluyen únicamente las partidas con importe certificado distinto de cero
en el mes seleccionado. No son certificaciones acumuladas.
El porcentaje del resumen se refiere al presupuesto de cada capítulo.

El detalle **no inventa líneas ni dimensiones de medición** ni copia las del
presupuesto como si se hubieran ejecutado ese mes. Muestra la cantidad derivada
con seis decimales; el importe mensual guardado prevalece. Un precio unitario
cero impide calcular esa cantidad.

**Informe de desviaciones** permite guardar el informe en PDF u ODT, según
**Archivo → Configuración…**, con la misma cabecera, pie y estilos del resto.
Incluye todas las partidas del mes seleccionado, también las pendientes de
certificar: capítulo, código, resumen, previsión original, certificado,
desviación (certificado menos previsión) y justificación, con sus totales.
Para partidas ya certificadas usa la previsión original guardada antes del
ajuste del plan, como los indicadores de desviación de la tabla.

## 11. Controlar la facturación

Abre **Herramientas → Facturación** para acceder directamente a la tabla.
Pulsa **Carpeta…** para elegir la carpeta de facturas PDF. La selección inicia
su lectura; **Actualizar lectura** vuelve a leer todos sus PDF cuando lo necesites.
La carpeta se conserva dentro del proyecto; también puedes
cambiarla desde **Carpeta…** en la tabla. Se leen todos los PDF de esa carpeta
(no las subcarpetas), de forma local y sin enviarlos a servicios externos.

La aplicación busca las palabras **Obra**, **Expediente**, **Fecha**, **Partida**
e **Importe** y tablas con cabeceras **Partida / Importe**. También admite tablas
sin bordes con columnas separadas por espacios. Los documentos escaneados,
cifrados, dañados o con formatos no reconocidos se señalan para revisión:
no se inventan importes ni se realiza OCR automáticamente. Cada PDF nuevo
tiene su columna, aunque no se haya podido interpretar.

### Costes y asignación de facturas

- **Unificar recursos** muestra una sola fila por recurso con el mismo código,
  descripción, tipo y unidad, sumando sus importes entre todas las partidas.
  Los precios distintos también se acumulan si corresponden al mismo recurso;
  los costes sin código de recurso permanecen separados.
  Oculta **Capítulo** y **Partida** y muestra **Solo lectura**: no se pueden
  editar importes en la tabla agrupada. La columna de la factura seleccionada,
  el previsto, el facturado global o acumulado y la diferencia se suman por grupo.
  Se mantienen los filtros y el periodo elegido. Desactiva el botón para volver
  al detalle editable; no se modifican las filas ni las asignaciones guardadas.
- Cada fila representa un recurso dentro de una partida: material, mano de obra
  o maquinaria. Los auxiliares se desglosan hasta sus recursos. Los porcentajes
  no se consideran recursos facturables.
- Los botones **▧**, **⚙** y **RCDs** filtran materiales, maquinaria y gestión de
  residuos, respectivamente. Solo uno puede estar activo; pulsa otra vez para
  mostrar todas las filas, incluida la mano de obra y los costes de tipo Otro.
  Los filtros no modifican importes ni asignaciones.
- Las partidas de gestión y transporte generadas desde RCDs aparecen como
  **RCDs**, por su coste directo total, sin duplicar sus componentes.
- El coste previsto inicial es **medición de partida × rendimiento de recurso
  × precio del recurso**, incluidos los precios editados del proyecto.
- Si una partida no tiene desglose o faltan recursos, se crea una fila editable
  de tipo **Otro** por su coste directo. Los recursos ausentes se notifican.
- Solo se asignan automáticamente códigos de recurso exactos y únicos,
  o combinaciones exactas **partida / recurso**. Revisa estas asignaciones.
  Los códigos presentes en varias partidas y los textos ambiguos quedan pendientes.
- Selecciona la factura y pulsa **Revisar líneas** para ver las incidencias y
  asignar cada línea a la fila adecuada. Puedes añadir, editar, eliminar
  o desasignar líneas. Para repartir un importe entre varios recursos, introduce
  los importes repartidos directamente en sus celdas y revisa la diferencia.
- Selecciona una factura en el desplegable o en el listado superior. La tabla
  muestra solo su columna, identificada por la **fecha de la factura**. El
  desplegable conserva fecha y nombre para distinguir facturas del mismo día.
- Haz doble clic en el importe de esa columna, sobre la fila del material,
  maquinaria o RCD correspondiente, para asignarlo o modificarlo directamente.
  **Enter** o salir de la celda confirma; **Esc** cancela. Solo se modifican los
  importes de la factura seleccionada; los datos del coste y el previsto no
  son editables. Un cero retira el importe y se admiten abonos negativos.
- Si sustituyes manualmente el importe de una fila con líneas PDF asignadas,
  esas líneas quedan pendientes: el nuevo importe se conserva como asignación
  manual. Esto evita que una reasignación posterior descuente importes antiguos.
- **Sin asignar/diferencia** compara el importe leído/declarado con el total
  introducido en la columna. Los importes sin asignar no se suman como facturados.
  El importe leído/declarado inicial es la suma de las líneas reconocidas,
  no una identificación automática del total fiscal de la factura.

### Edición y guardado

Las facturas se ordenan de la más antigua a la más reciente, tanto en el listado
como en las columnas de costes. Se admiten fechas `dd/mm/aaaa`, `dd-mm-aaaa`,
`dd.mm.aaaa`, `aaaa-mm-dd` y `aaaa/mm/dd`; las fechas vacías o no reconocidas
quedan al final. Las fechas y demás datos de cabecera se obtienen del PDF,
no se añaden ni editan facturas manualmente.

La ventana se divide verticalmente al 50 %: facturación a la izquierda y
visor PDF integrado a la derecha. Arrastra la separación para reajustarlos.
El listado superior muestra solo **Fecha** y **Nombre del fichero (clic para abrir)**,
sin repetir el nombre de la factura, también para los PDF cuya lectura haya fallado.
Al seleccionar una factura en el listado o desplegable, su PDF se muestra a la
derecha sin abrir otra ventana. Puedes cambiar de página con **← / →**, ajustar
el zoom y desplazarte con las barras o la rueda. El zoom del 100 % ajusta la página
al ancho del panel. **Abrir PDF externo** conserva la opción del visor predeterminado.
Los documentos escaneados también pueden visualizarse aunque no se extraigan
sus datos. Los errores de apertura aparecen en el panel sin impedir la facturación.

Si has movido el archivo, selecciona su nueva
carpeta y actualiza la lectura: se recupera la ruta de un PDF idéntico cuyo
archivo original ya no exista, conservando sus asignaciones.

No hay botones para añadir, editar o eliminar costes: las filas proceden del
presupuesto. Las filas personalizadas de proyectos anteriores se conservan.
**Eliminar factura** retira sus asignaciones del proyecto, pero no borra el PDF;
si sigue en la carpeta, la siguiente lectura volverá a incorporarlo.

Las columnas **Previsto**, **Facturado** y **Restante** permanecen fijas en el
lateral derecho, incluso al desplazar las facturas horizontalmente.
**Restante = Previsto − Facturado**; los excesos se muestran en rojo.

En **Periodo acumulado**, **Todo el proyecto** compara el coste previsto total
de cada fila con todas sus facturas asignadas. Al elegir un mes `aaaa-mm`,
**Acumulado** suma solo facturas con fecha válida hasta el último día de ese
mes, incluidas las de años anteriores y los abonos; la **Diferencia** es el
previsto total del proyecto menos ese acumulado. No se distribuye el previsto
por meses. Este selector afecta a los totales de la derecha, no a la factura
que estás editando: la tabla mantiene exclusivamente la factura seleccionada,
aunque su fecha quede fuera del periodo de comparación.

Las facturas sin fecha o con fecha no reconocida se incluyen en el total global,
pero no en ningún acumulado mensual; se indica cuántas quedan excluidas.
**Sin fecha válida** permite revisar exclusivamente esos importes. Corrige su
fecha en el documento de origen y actualiza la lectura para que se incorporen
al mes correspondiente.
El pie resume solo las filas del filtro activo y el periodo elegido. Cambiar
filtros o periodo no altera los datos guardados.

**Actualizar lectura** relee todos los PDF, incorpora documentos nuevos sin
duplicar archivos con el mismo contenido y actualiza las cabeceras, fechas,
líneas e importes leídos de los ya importados. Conserva sus identificadores
y los importes asignados a cada recurso. Las líneas que no hayan cambiado
conservan también su asociación. Si desaparecen o cambian líneas asignadas,
sus importes permanecen como asignaciones manuales y se avisa para revisar
la diferencia. Si falla la relectura, se notificará y conservará la factura anterior.
Los documentos retirados de la carpeta no se eliminan de la tabla automáticamente.

Pulsa el **disquete** o **Ctrl+S** para conservar carpeta, filas, facturas,
asignaciones y ediciones dentro del JSON del proyecto. El disquete aparece con
fondo negro mientras hay cambios pendientes y recupera su color al guardar.
Si aún no se ha guardado el proyecto, se solicita su ruta. Espera a que termine
la lectura de PDF antes de guardar. Cerrar la ventana conserva los cambios
en memoria, pero debes guardar el proyecto para persistirlos. Las filas son una
instantánea editable del presupuesto al iniciar la facturación: los cambios
posteriores del presupuesto no sustituyen los costes personalizados.
Los importes deben usar un criterio homogéneo (por ejemplo, todos sin IVA);
no se deducen ni se añaden impuestos automáticamente.

## 12. Resolver problemas frecuentes

| Problema | Comprobación |
|---|---|
| No se abre el programa | Ejecuta desde PowerShell y revisa el mensaje; instala las dependencias con el Python del entorno |
| Falta Tkinter | Comprueba `python -m tkinter` y repara la instalación de Python |
| No se ve el icono o el logo del PDF | Conserva `iconos` junto a los módulos de la aplicación |
| No puedo editar una medición | Selecciona una partida, no el capítulo; pulsa una celda distinta de Parcial |
| Un informe pide precios | Carga la base de origen o completa los precios del proyecto |
| El PDF no se abre | Ábrelo manualmente desde la ruta de guardado y comprueba la asociación de PDF en Windows |
| No se guarda | Comprueba permisos de escritura y que el archivo no esté bloqueado |
| No aparece una certificación en el informe | Selecciona la cabecera del mes y comprueba que tenga importes distintos de cero |
| No se interpreta una factura | Revisa las cabeceras y los avisos; si es escaneada, realiza OCR externo o introduce sus datos manualmente |
| Un importe de factura no se asigna | Usa Revisar líneas o reparte el importe en las celdas; los códigos ambiguos nunca se reparten automáticamente |

## 13. Licencia y precauciones

El menú **Información** se organiza en tres bloques separados por líneas:
nombre, versión, fecha de la versión y resumen del programa en gris; bases de
precios de referencia; y enlaces al repositorio, licencia y apoyo al proyecto.

En **Información → Repositorio en GitHub** puedes abrir el repositorio del
proyecto en el navegador. El menú no muestra datos personales de autoría.

En **Información → Bases de precios de referencia**, sitúa el puntero sobre
la opción para desplegar los enlaces de Andalucía (JA), Extremadura y Madrid.
Pulsa uno de ellos para abrir su web oficial en el navegador y consultar las
opciones de descarga.

En **Información → Licencia y responsabilidad…** puedes consultar el aviso MIT
y la limitación de responsabilidad.

Si deseas apoyar el mantenimiento del programa, usa **Información → Apoyar el
proyecto (Buy Me a Coffee)**. El repositorio de GitHub también muestra el
botón Sponsor con el mismo enlace.

Verifica profesionalmente todos los resultados antes de emplearlos en un
expediente o una decisión económica. Guarda copias de seguridad y protege los
datos de los proyectos; no los subas al repositorio de código.
