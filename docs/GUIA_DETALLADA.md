# Gestor de Proyectos · Guía detallada

Aplicación de escritorio para Windows que permite trabajar con presupuestos
FIEBDC-3 (BC3), editar mediciones y descomposiciones, planificar obras y gestionar
certificaciones mensuales.

## Versión 2.2.0 — 25/11/2026

- **Seguridad y Salud:** catálogo de planos A3, generación reforzada sin
  plantillas, presupuesto ligado al porcentaje del PEM y alternativas
  preventivas agrupadas por tipo.
- **Plantillas e informes:** logo institucional configurable que se ajusta al
  marco, ayuda contextual y propuestas de ruta y nombre para los anejos.
- **Presupuesto:** columnas principales redimensionables horizontalmente.
- Corregidos avisos de fuentes en la visualización y exportación de fichas SVG.

Consulta el [historial de cambios](../CHANGELOG.md#220--2026-11-25).

## Versión 2.0.0 — 19/11/2026

- **Atomización del proyecto:** capítulos y partidas se guardan en archivos
  independientes. Los proyectos anteriores se pueden abrir y migrar.
- **Trabajo colaborativo:** [proyectos en carpeta compartida](#proyectos-colaborativos-divididos)
  con presencia de usuarios, bloqueos de edición, detección de conflictos y
  cifrado opcional.
- **Visor BIM:** [Motor BIM](#motor-bim) para explorar el IFC en 3D y revisar
  las equivalencias IFC 4.3 → BCCA antes de incorporar mediciones.

Consulta el [historial de cambios](../CHANGELOG.md#200--2026-11-19).

## Versión 1.7.0 — 17/11/2026

### Novedades de esta versión

- **Consultas NTP y UNE:** buscadores separados con filtrado al escribir,
  detalle y acceso a la ficha oficial; NTP sin botón adicional de PDF.
- **Configuración de obra:** selección de varios municipios y códigos CPV
  del capítulo 45, con búsquedas filtrables y persistencia en el proyecto.
- **Planificación en plantilla:** cronograma horizontal en PDF u ODT, sin
  convertirlo en una imagen, con resumen del plazo y de la actividad más larga.
- **ESS/EBSS completo:** criterios legales estimados pero editables, propuesta
  preventiva separada, capítulo presupuestario correlativo, índice enlazado
  y tabla resumen revisada con condicionantes, medios, maquinaria y contactos.
- **PPTP:** fichas agrupadas por capítulo, tabla enlazada, normativa por ámbitos,
  revisión de precios y decisiones UNE conservadas tras la revisión manual.
- **Bibliotecas y formato:** fichas BCCA reorganizadas, editor con referencias
  internas y prescripciones separadas, historial de revisiones y estilos Writer.
- Correcciones de personas estimadas, direcciones asistenciales, tildes y
  alineación de tablas entre páginas. Ejecutable e instalador Windows 1.7.0.

El detalle de las novedades desde la versión 1.6.0 está en el
[changelog](../CHANGELOG.md#170--2026-11-17). Las marcas históricas de inclusión
inicial de las bibliotecas en v1.6.0 se conservan; no son la versión de la aplicación.

### Funciones generales

- Facturación por recurso con filtros de materiales, maquinaria y RCDs.
- Asignación de importes en tabla y comparación global o acumulada por mes.
- Visor PDF integrado junto a la facturación, con páginas y zoom.

- Informes PDF y ODT editables con estilos, cabeceras y firmas configurables.
- Informe de mediciones y exportación del informe de desviaciones en PDF u ODT.
- Buscador del proyecto, filtros de recursos y expansión de capítulos y partidas.
- Resumen y descripción en mayúsculas al crear, duplicar o incorporar partidas;
  las bases originales y los proyectos existentes no se modifican al abrirlos.

### Municipios de la obra

- **Archivo → Configuración… → Emplazamiento** permite añadir uno o varios
  municipios y quitar los seleccionados antes de guardar.
- El desplegable está ordenado alfabéticamente y se filtra al escribir, sin
  distinguir mayúsculas ni tildes. La provincia distingue municipios homónimos.
- Provincia y comunidad autónoma se obtienen automáticamente al enlazar
  `tools/CMUN.csv` y `tools/CPRO.csv` por su código de provincia. Se utiliza
  la relación suministrada (CMUN: 1 de enero de 2008), sin consultas externas.
- La selección se guarda con el proyecto y en la configuración inicial de
  nuevos proyectos, y rellena los datos territoriales del estudio de Seguridad
  y Salud. El campo «Localidad» existente se conserva por separado.

### Códigos CPV de la obra

- **Archivo → Configuración… → CPV** permite seleccionar varios códigos del
  capítulo 45 (Trabajos de construcción), ordenados por código.
- El desplegable filtra al escribir por código o descripción, sin distinguir
  mayúsculas ni tildes. Permite añadir códigos y quitar los seleccionados.
- Se utiliza la columna española del vocabulario principal incluido en
  `tools/cpv_2008_xls.zip`, sin extraer ni modificar el libro original ni incluir
  códigos de otros capítulos o del vocabulario suplementario.
- La selección conserva el dígito de control y se guarda con el proyecto y
  como configuración inicial de nuevos proyectos, igual que los municipios.

### Justificación de precios

- En **Herramientas → Justificación de precios**, el botón
  **Editar biblioteca de documentos…** abre el mismo editor de Markdown que
  el PPTP. Selecciona un documento a la izquierda y guarda cada revisión con
  su autor y fecha antes de exportar. Los ocho documentos de
  `tools/price_justification/BIBLIOTECA.zip` son la fuente del texto generado;
  las modificaciones guardadas se utilizan en las próximas exportaciones.
- Proyecto, entidad, autoría, expediente, emplazamiento, provincias, comunidades
  autónomas y porcentaje de indirectos se leen de la configuración general
  actual, sin mantener otra copia en la herramienta. Si no se han seleccionado
  municipios se utiliza la localidad configurada, sin deducir su provincia.
- **Datos específicos** recoge fecha de referencia, contratación pública o
  privada, fuente de precios, convenio, salarios, cotizaciones, calendario,
  jornada anual y criterio de maquinaria. Se conservan con el proyecto y su
  historial; las notas anteriores permanecen en **Complementos opcionales**.
- **Guardar anejo…** abre el selector de archivos para elegir nombre y destino.
  Genera un único documento con índice, texto y listados
  de precios elementales, auxiliares y descompuestos, en la plantilla y formato
  PDF u ODT elegidos en el diálogo de guardado, usando la plantilla configurada.
  El ODT contiene texto
  y tablas editables, no capturas. Las tablas de proyecto y referencias laborales
  se numeran al exportar; los datos de proyecto que falten no se imprimen.
  Comparten el formato de tablas de Seguridad y Salud, con cabecera azul
  y filas alternas. Las bases y los tipos de cotización se desglosan en viñetas.
  Las referencias normativas enlazan con su publicación oficial. Las fórmulas
  tienen subíndices y fracciones apiladas. Los paréntesis que encierran una
  fracción se ajustan a su altura en PDF y ODT. Las fórmulas se editan en los
  bloques `$$` de la biblioteca, con `\frac{numerador}{denominador}`.
- Los elementales incluyen mano de obra, maquinaria y materiales identificados.
  Se reutilizan las cantidades, precios y redondeos del presupuesto actual,
  sin cambiar el informe «Materiales» existente ni aplicar otro recargo indirecto.
- Incluir las fórmulas no recalcula los precios. Los importes del ejemplo no
  se adoptan como datos reales ni se presentan referencias de 2008-2009 como
  vigentes. Los consumos históricos se identifican como orientativos; los
  parámetros económicos y referencias laborales se deben verificar.

### Planificación en plantilla

- En **Herramientas → Planificación**, **Guardar planificación en plantilla**
  permite elegir PDF u ODT al guardar y utiliza la plantilla configurada.
- Se exporta solo el cronograma central con sus datos y barras tabuladas, no
  una captura de imagen; el ODT permite editarlo. La página es A3 horizontal para
  mejorar la lectura y después se describe la duración total calculada y la
  actividad de mayor duración.
- El pie rellena el marcador de capítulo con **Planificación** y el de página
  con la numeración correspondiente. No se anteponen el nombre del proyecto
  ni el título Plan de trabajo.
- **Detalle de la figura** conserva la salida detallada anterior en PNG/PDF
  como opción secundaria, sin sustituir el guardado estándar en plantilla.

### Seguridad y salud

- **Herramientas → Seguridad y salud** propone partidas según los riesgos de
  las UO BCCA y permite revisarlas antes de crear el capítulo correspondiente.
- La primera ventana estima los cuatro criterios del artículo 4.1 del
  RD 1627/1997: PEC con IVA, duración y concurrencia de trabajadores, jornadas
  acumuladas y tipología especial de obra. Muestra los datos de la estimación
  junto a casillas editables, admite varios criterios y conserva la decisión.
  Si no se marca ninguno, corresponde un Estudio Básico de Seguridad y Salud.
- El cuadro **Resumen de la actuación** de esa primera ventana toma inicialmente
  el resumen guardado en PPTP y permite revisarlo sin modificar el pliego.
  La edición se guarda con el proyecto y se utiliza en el estudio en lugar de
  enumerar automáticamente las partidas. Si falta, se señala como dato pendiente.
- La propuesta preventiva se abre en una ventana separada. Al aceptarla,
  se integra en el siguiente capítulo de la secuencia **CAP. XX**.
- Porcentaje orientativo configurable y matriz CSV incluida en la aplicación.
- Estudio completo en PDF u ODT editable, con el presupuesto preventivo integrado
  en el presupuesto general, sin emitirlo como informe independiente.
- Formato como el PPTP: sin portada ni títulos repetidos del documento y proyecto,
  epígrafes numerados jerárquicamente en mayúsculas y párrafos justificados.
  Comienza con un índice enlazado de dos niveles: el PDF calcula las páginas reales
  en varias pasadas; el ODT usa un índice nativo y campos de página que Writer
  resuelve al paginar y permite actualizar. Se conservan la cabecera institucional
  y la numeración de página en el pie de la plantilla, donde figura
  «ESTUDIO DE SEGURIDAD Y SALUD» o «ESTUDIO BÁSICO DE SEGURIDAD Y SALUD»
  según los criterios seleccionados.
- La primera ficha, `01_objeto_y_alcance.md`, reúne objeto y alcance, los criterios
  seleccionados del artículo 4.1 del RD 1627/1997 y la tabla resumen, sin un
  epígrafe adicional «Datos del proyecto». La justificación de los criterios
  conserva la redacción normal, con mayúscula inicial y el tipo de estudio en
  mayúsculas en «Por tanto, se define un ESTUDIO DE SEGURIDAD Y SALUD»
  (o ESTUDIO BÁSICO, según corresponda).
  Su tabla tiene una leyenda superior, cabecera azul y filas alternas azul claro/blanco,
  sin contratista, con PEM y PEC con IVA. Mantiene el mismo ancho y margen al
  continuar en otra página, incluso al dividir una fila larga. La tipología recoge
  los CPV seleccionados en la configuración y, debajo, los capítulos con viñetas;
  no repite resúmenes de unidades ni partidas importadas. La duración procede de
  la planificación y el máximo simultáneo se expresa en personas enteras, con al
  menos una por equipo de trabajo. La superficie es la mayor medición de las partidas en m²
  (no su suma); si no hay partidas de superficie, queda pendiente de validar.
  Los demás valores insertados en las plantillas aparecen en mayúsculas y la biblioteca
  ya no incluye la ficha 0 de integración; la unidad m² conserva su grafía.
- **Condicionantes externos...** permite editar la descripción de tráfico,
  peatones, servicios, terreno, ambiente, instalaciones provisionales, centro
  asistencial y coordinación de seguridad y salud, indicando **Sí / No / Pendiente**.
  Se guardan con el proyecto sin modificar el presupuesto y se muestran agrupados
  en el resumen. **Servicios afectados** aparece una sola vez en este grupo.
- La **Memoria descriptiva** incluye, en **Criterios preventivos generales**, una tabla
  con nueve grupos de riesgo, situaciones previsibles y criterios
  preventivos, con cabecera azul y filas alternas azul claro/blanco en PDF y ODT.
  Los criterios comunes, comprobaciones previas y medidas de delimitación se
  reúnen sin repetir apartados, conservando las obligaciones específicas del
  pliego, las emergencias y las fichas de cada unidad.
  Las condiciones generales, las instalaciones provisionales y las emergencias
  se integran en el capítulo 2, sin repetir sus procedimientos.
  La protección de terceros abarca cerramientos, accesos e itinerarios, señalización,
  vertidos, residuos, acopios, polvo, humos, ruido, vibraciones, deslumbramientos
  y barro en viales. Las medidas se adaptan a las afecciones y autorizaciones de
  cada obra, sin copiar datos de proyectos de ejemplo ni imponer equipos fijos.
  La consulta y participación de los trabajadores se recoge en la coordinación.
  Incluye protecciones colectivas, tabla orientativa de nueve EPI con leyenda y
  formato azul/blanco, señalización, orden y limpieza, equipos, acopios,
  iluminación y circulación interna. La gestión preventiva desarrolla los
  servicios higiénicos, la instalación eléctrica provisional si es necesaria,
  botiquín y actuación ante accidentes, incendios y derrames,
  sin duplicar emergencias ni fijar dotaciones o dimensiones ajenas al proyecto.
- A continuación se incluye el **Catálogo preventivo**, con introducción, leyenda
  superior, cabecera azul y filas alternas. Reúne las definiciones de riesgos,
  medidas, protecciones colectivas, EPI y equipos citados en el documento.
  Las referencias en párrafos y tablas son enlaces a su fila, tanto en PDF como
  en ODT, también si se escriben con tilde, como `R-EXPLOSIÓN`.
  Los tipos se mantienen consecutivos: en PDF se indican una vez por página y en
  ODT se combinan sus celdas, con una etiqueta al inicio de cada grupo, sin exigir
  LibreOffice. La cabecera se repite al continuar la tabla en otra página.
  El contratista deberá confirmar y desarrollar la selección en el PSS según la
  evaluación y el procedimiento real de cada tajo. Listas y enumeraciones tienen
  sangría, también en las celdas del resumen.
  La aplicabilidad se conserva en la tabla resumen, no en la narración de
  instalaciones y coordinación; primeros auxilios remite a los centros de la
  Tabla 1. El alcance de las fichas se introduce en el capítulo 3.
  Las 50 definiciones de riesgos de la biblioteca se amplían de forma breve,
  describiendo situaciones de exposición, posibles daños y criterios de control.
  En la tabla del capítulo 1, **Riesgos detectados** y **Medidas preventivas**
  muestran únicamente los códigos aplicables, separados por coma y espacio,
  sin viñetas ni definiciones y con enlaces a las filas del catálogo.
- El **capítulo 3** comienza con una introducción y la **Tabla 5**, con leyenda
  superior y formato azul/blanco: relaciona las unidades de los capítulos seleccionados
  con sus fichas BCCA, riesgos y medidas preventivas enlazadas al catálogo.
  Después aparecen las fichas específicas disponibles, una sola vez por ficha,
  en el orden de clasificación de PPTP. Sus títulos muestran solo la denominación,
  sin anteponer el código ni los párrafos de unidades vinculadas o clasificación.
  La relación con las partidas y los códigos BCCA se conserva en la Tabla 5.
  Las **11 fichas ESS** conservan su especialidad preventiva y se organizan en
  las mismas carpetas y subcarpetas BCCA de PPTP, con archivos internos por código y título.
  Las familias generales 15CP y 15CR se mantienen en sus carpetas respectivas;
  no se amplía la biblioteca a las 1.661 fichas del PPTP.
  Cada ficha desarrolla en texto **Definición y alcance**, **Riesgos detectados**
  y **Medidas preventivas**, con subapartados de **Protecciones colectivas**,
  **Equipos de protección individual** y **Equipos de trabajo**, desglosados en
  viñetas por protección o equipo. **Validación** cierra cada ficha como apartado
  independiente. Los riesgos se muestran como códigos separados por comas
  (por ejemplo, R-ATROPELLO), enlazados a su definición en el catálogo, sin repetirla.
  Las viñetas tienen sangría y las referencias conservan sus enlaces.
  No se muestran códigos UO ni capítulos separados de matriz y fichas BCCA.
  Las unidades sin ficha específica se marcan expresamente como pendientes,
  sin asignarles una evaluación por similitud de su descripción. Se revisan las
  correspondencias de residuos bituminosos y ensayos con la clasificación PPTP;
  los códigos antiguos 17RRBN se vinculan a 17RB con revisión expresa, sin
  modificar el presupuesto.
- El **capítulo 4**, **Seguimiento, control, coordinación, formación y documentación**,
  reúne el control preventivo, la coordinación empresarial, la formación e
  información, los documentos disponibles, la vigilancia de la salud y los
  primeros auxilios, los servicios ocultos, los recursos preventivos y las
  previsiones para trabajos posteriores.
  Estos apartados se trasladan desde la memoria cuando corresponde, evitando
  repetir formación, coordinación, documentación y mantenimiento futuro.
  Incluye controles diarios, correcciones con responsables y plazos, adaptación
  del PSS y coordinación con suministradores, laboratorios y gestores.
  La vigilancia de la salud respeta consentimiento, excepciones y confidencialidad,
  sin imponer una periodicidad médica genérica. Los primeros auxilios remiten al
  capítulo 2 y a la Tabla 1, con local adecuado cuando la obra lo requiera.
  Los servicios desconocidos se consideran en servicio y se detiene la actividad
  que pueda afectarlos hasta comprobarlos y coordinar la actuación con su titular;
  no se presupone una línea de alta tensión detectada.
  Los recursos preventivos se concretan cuando sean legalmente exigibles, con
  capacitación, medios y vigilancia de la eficacia y corrección de las medidas.
  Los capítulos 1 a 4 forman el **Documento I. Memoria**.
- El **Documento II. Pliego de condiciones** se incluye en el mismo PDF/ODT,
  con índice propio y numeración de capítulos reiniciada:
  **1. Objeto del presente pliego**,
  **2. Condiciones de naturaleza legal**,
  **3. Condiciones de naturaleza facultativa**,
  **4. Condiciones de naturaleza técnica** y
  **5. Condiciones de naturaleza económica**.
  El objeto introduce las cuatro naturalezas de condiciones mediante viñetas.
  El capítulo 4 desarrolla la protección colectiva y los EPI en el apartado
  4.1, y las instalaciones provisionales de salud y confort en el 4.4
  (vestuarios, aseos y duchas, retretes, comedores y descanso). Su mantenimiento
  corresponde al empresario; el Plan dimensiona las dotaciones por plantilla
  simultánea y conforme a la normativa y al convenio aplicables, sin imponer
  ratios universales. Maquinaria/equipos y señalización/balizamiento/protección
  de terceros se desarrollan en los apartados 4.2 y 4.3.
  **Condiciones técnicas...** mantiene un núcleo general fijo y permite revisar
  diez bloques específicos (cerramientos, huecos, pasos, maquinaria móvil,
  extendido/compactación, corte/demolición, elevación, señalización, señalistas
  y peatones). Las propuestas proceden de las referencias de las fichas de las
  UO seleccionadas, recursos físicos del presupuesto y condicionantes expresamente
  aplicables, no de buscar palabras en la descripción de las partidas.
  Todos los bloques pueden incluirse o excluirse manualmente; la falta de una
  ficha o recurso no demuestra su innecesariedad. Las decisiones se guardan en
  el proyecto y se conservan al recalcular el capítulo preventivo.
  Antes de exportar desde la ventana se exige confirmar el contexto actual;
  los cambios de UO, referencias, equipos, condicionantes o prescripciones obligan
  a revisar de nuevo. PDF y ODT incluyen los mismos bloques confirmados y
  renumeran sus epígrafes. La generación programática sin revisión actualizada
  muestra una advertencia explícita en el documento y en su lista de validaciones,
  sin presentar los bloques omitidos como innecesarios.
  Define las obligaciones de los agentes, el PSS y su aprobación, el control
  documental, la certificación y las modificaciones. Utiliza el importe
  preventivo realmente incorporado al presupuesto, no una cantidad de ejemplo.
  El capítulo 3 agrupa el PSS, su acta o resolución de aprobación, la comunicación
  de apertura, las modificaciones, los libros y el control de la efectividad.
  Este último desarrolla el cuadro de seguimiento, los índices de incidencia,
  frecuencia, gravedad y duración media, y los partes de accidentes y deficiencias
  con responsables, plazos y comprobación del cierre. La incidencia se expresa
  por cien trabajadores de plantilla media, no por horas; los indicadores con
  denominador cero se identifican como no calculables.
  Se distingue la aprobación pública del Plan y no se impone un visado general
  del acta. El antiguo Libro de Visitas se trata mediante las diligencias
  inspectoras y su conservación, sin exigir un nuevo libro.
  Los apartados 3.10 y 3.11 completan consulta, delegados, comité y obligaciones
  de los trabajadores. El umbral del comité se refiere a empresas o centros
  de trabajo, no a sumar automáticamente personal de distintas empresas en la
  obra. Se precisan la integración del coordinador en la Dirección Facultativa,
  los supuestos y permanencia de recursos preventivos y la comprobación de
  documentación vigente, sin exigir modelos o libros derogados.
  El capítulo 5 conserva valoración, certificación y abono, y concreta las
  materias económicas que deberán regularse en el contrato, sin inventar
  porcentajes de garantías ni revisiones automáticas de precios.
  Ambos índices están enlazados a sus propios epígrafes; en ODT son actualizables
  de forma independiente. La numeración de páginas continúa entre documentos.
- **Normativa y NTP...** permite confirmar las disposiciones y las asociaciones
  entre partidas y NTP antes de generar el estudio. Las propuestas utilizan los
  códigos BCCA y las referencias expresas de riesgos y medidas de las fichas ESS,
  no palabras de la descripción presupuestaria. Se pueden excluir propuestas
  y asignar manualmente otras NTP del catálogo; las decisiones se guardan en el
  proyecto y se conservan al recalcular el capítulo preventivo.
  Las coincidencias solo temáticas y las NTP con avisos no se preseleccionan.
  El capítulo normativo incluye las disposiciones confirmadas, las condiciones
  generales y la prioridad de la protección colectiva. El apartado **2.4. Normas
  UNE de aplicación** recoge un listado general por familias: escaleras portátiles,
  protectores auditivos, protección respiratoria y filtros, guantes, calzado,
  protección de cabeza/ojos/cara y ropa, redes y sistemas contra caídas.
  Solo incluye referencias marcadas como vigentes en el catálogo UNE local,
  con sus títulos y ediciones, sin proyectos ni normas anuladas. Su aplicación
  queda condicionada al equipo y al trabajo realmente previstos; el listado
  no confirma su obligatoriedad ni el carácter armonizado de las normas.
  Se deberán comprobar las ediciones, modificaciones y correcciones antes del uso.
  El apartado **2.5**
  contiene la **Tabla 6**, con dos columnas: capítulo del presupuesto y NTP
  confirmadas en viñetas. Las NTP compartidas aparecen solo en el primer
  capítulo seleccionado, según el orden del presupuesto; su ausencia en los
  siguientes no implica que dejen de ser aplicables. Las fichas de la Memoria
  mantienen las referencias a riesgos y medidas. Las NTP se presentan como guías
  técnicas, no como legislación obligatoria. Deben comprobarse alcance, edición,
  avisos y normativa vigente a la fecha de ejecución.
  Si cambian las unidades, sus capítulos o títulos, los códigos de fichas, riesgos o medidas, o los
  catálogos locales, se requiere una nueva confirmación. Las unidades sin ficha
  o asociación no se dan por evaluadas.
- El informe y su índice no incluyen el anexo de trazabilidad con importes ni
  un capítulo independiente de datos pendientes.
  Al guardar se muestra una confirmación informativa con la ruta del documento
  y el botón Aceptar, como en PPTP, sin incluir avisos de revisión.
- Antes de los **Riesgos detectados** se incluyen filas separadas de
  **Medios auxiliares** y **Maquinaria empleada**, extraídas de las partidas y
  sus descompuestos, también anidados. Los medios físicos reconocibles, como
  andamios, escaleras portátiles o plataformas, se separan del resto de recursos
  clasificados como maquinaria en el presupuesto. Se eliminan duplicados y se
  excluyen mano de obra, materiales ordinarios y porcentajes genéricos de medios
  auxiliares; si faltan descripciones o descompuestos necesarios, se advierte.
  La última fila reúne los **Centros asistenciales, dirección y teléfonos de interés**:
  respeta el modo de entrada seleccionado (texto libre o tabla SICESS), incluye
  la dirección postal completa, los teléfonos y las observaciones revisadas,
  además del teléfono general de emergencias 112. Otros contactos pueden añadirse
  en el texto libre o en la descripción del condicionante de centro asistencial.
- Antes de iniciar el análisis se muestran los capítulos con partidas y sus
  casillas de selección. **Gestión o tratamiento de RCD** y **Control de Calidad**
  aparecen desmarcados inicialmente, aunque pueden incluirse expresamente.
  El capítulo generado de Seguridad y Salud no se analiza como unidad de obra.
  Solo los capítulos marcados alimentan la propuesta preventiva, las fichas,
  los recursos, la planificación analizada y la revisión normativa/NTP.
  El porcentaje preventivo se aplica a su importe seleccionado; los importes
  globales y el criterio legal por presupuesto conservan el presupuesto completo.
- Las selecciones de capítulos y medidas, y el porcentaje aceptado, se guardan
  en JSON. Al reabrir se recuperan sin consultar de nuevo la matriz ni recalcular
  el capítulo, salvo que se cambie el ámbito de capítulos.

### Consulta de NTP y UNE

- **Consultas** abre buscadores independientes de NTP, UNE y LER, identificados
  con una lupa, incluso sin presupuesto abierto.
- El listado se filtra al escribir por código o palabras del título, sin distinguir
  tildes ni mayúsculas. También busca año/fecha, estado y comité cuando constan.
- La selección muestra el título completo y sus datos. Doble clic, Intro o
  **Ver ficha oficial** abre la referencia en INSST o UNE.
- Se consultan los catálogos locales completos, sin limitarse a las referencias del
  presupuesto ni modificar el proyecto. El catálogo UNE conserva normas anuladas,
  proyectos y fichas con la misma designación: comprueba vigencia y aplicabilidad
  en la fuente oficial. No se incluye ni reproduce el texto de las normas UNE.

### Gestión de RCDs

- **Herramientas → RCDs**: datos de cálculo editables y
  revisión de residuos detectados en las partidas.
- Marcas X manuales y evidencias de detección, guardadas con el proyecto.
- **Vínculos LER–BCCA…** cruza el código exacto de cada partida con la tabla BCCA2023
  y muestra los candidatos, su procedencia y la indicación de peligrosidad del catálogo.
  Los vínculos únicos no peligrosos compatibles con el modelo y con la detección por texto
  se usan como preselección revisable en un análisis nuevo; los vínculos múltiples quedan
  como indicios. El estudio incluye la trazabilidad; no deduce cantidades de ese cruce
  ni cambia marcas ya guardadas al reabrir el proyecto.
- Al guardar RCDs, capítulo Gestión de residuos incorporado al presupuesto
  con niveles, tipologías, mediciones, precios e importes, sin duplicados.
- Estudio completo PDF/ODT con índice, memoria, pliego, firma y las tablas
  existentes de identificación, estimación, trazabilidad y presupuesto, con
  el formato y la plantilla configurados. Texto y tablas nativos, editables en ODT.
- **Documentación del estudio…** permite revisar los ocho documentos base y
  editar su biblioteca con el mismo editor e historial del PPTP. Conserva los
  marcadores de tablas: si faltan o se duplican, la exportación informa del error.
- Las decisiones de prevención, separación, reutilización, valorización,
  destinos, peligrosos, instalaciones/planos y condiciones territoriales se
  guardan con el proyecto. Los campos sin completar figuran como pendientes.
- Los datos comunes se toman de la configuración general y el PEM del presupuesto
  actual, diferenciándolo de la base sin gestión. La normativa andaluza se incluye
  automáticamente solo si los municipios configurados sitúan la obra en Andalucía.
  Se reutiliza el resumen de la actuación de Seguridad y Salud o, si no está
  cumplimentado, el del PPTP, indicando su procedencia.
- Se mantiene el modelo revisado de las tres hojas del ODS aportado, sin cambiar
  sus cálculos: coeficientes, precios, mínimos y complemento Nivel I editables.
  Las tarifas del modelo están expresadas en euros; no se convierten divisas.

La estimación debe revisarse por el técnico responsable; no acredita cumplimiento
normativo ni sustituye el desglose por código LER, la identificación de residuos
peligrosos, la concreción de destinos ni los planos que deben incorporarse al proyecto.
Los umbrales del RD 105/2008 no eximen de la clasificación de la Ley 7/2022.
El ODS de trabajo
no es necesario para ejecutar la herramienta y no se incluye en el instalador.

Si un presupuesto BC3 ya incluye partidas en un capítulo externo de RCDs o de
Seguridad y Salud, se conservan para evitar duplicarlas o recalcularlas. Los
capítulos de RCDs se reconocen por su nombre o código (residuos, escombros o
RCDs). Para Seguridad y Salud se exige el nombre completo «Seguridad y Salud»,
sin distinguir mayúsculas ni espacios adicionales; el código no interviene.
En ese caso no se crea otro capítulo preventivo ni se genera el estudio.
Los capítulos generados por este programa conservan el acceso: al abrir la
herramienta desde su proyecto JSON se cargan los datos guardados sin recalcular
ni modificar las partidas. Los cambios se aplican al aceptarlos o guardarlos
expresamente en la herramienta.

### PPTP

- **Herramientas → PPTP**, debajo de Planificación, prepara el pliego en tres
  pasos: datos del proyecto, fichas detectadas y asignaciones manuales.
- Revisa título, entidad y plazo de garantía de la configuración, y escribe
  el resumen de las obras. El plazo de ejecución se calcula desde la planificación
  guardada, incluidos fines de semana y festivos; sin planificación se solicita
  un valor manual y se avisa de que debe realizarse.
- Busca fichas Markdown por prefijos UNI en los niveles correspondientes
  (por ejemplo, `01`, `01T`, `01TR`), excluyendo RCDs y Seguridad y Salud.
  Las partidas sin correspondencia pueden vincularse a una o varias fichas del índice.
- Revisa también las condiciones de aplicación de la normativa propuesta.
  Las UNE relacionadas se preseleccionan por familia: se marca la publicación
  más reciente y se dejan desmarcadas las demás, sin distinguir partes.
  Se usa la fecha de publicación del CSV y, si falta, el año de la designación.
  En empates se escoge una referencia de forma estable por designación.
  Esta propuesta no acredita que una parte sustituya o cubra las otras.
  Las demás referencias pendientes de revisión no se marcan automáticamente.
  En el tercer paso, la pestaña **Disposiciones técnicas legales** muestra una
  tabla de referencias con sus partidas y fichas relacionadas y la casilla
  **Incluir**. El detalle inferior conserva la condición completa y las parejas
  partida–ficha; indica también si la norma está incluida o pendiente.
  Marca las UNE que procedan para que aparezcan en el apartado 7. Las relaciones
  se actualizan al cambiar las fichas, conservando las decisiones de inclusión
  ya revisadas.
  La preselección se aplica automáticamente también a proyectos antiguos con
  UNE guardadas como desmarcadas. No hay botón adicional. Los cambios manuales
  de las casillas UNE quedan identificados en `pptp.une_revisadas` dentro del JSON
  y se respetan al recalcular o reabrir; las demás UNE se actualizan automáticamente
  según las partidas y la publicación más reciente. Las leyes y reglamentos
  conservan sus decisiones guardadas.
  En el apartado 7, las referencias seleccionadas se presentan con subtítulos
  temáticos (contratación, accesibilidad, medio ambiente, estructuras, instalaciones,
  etc.), tanto en PDF como en ODT. Los grupos sin referencias incluidas se omiten.
- Al generar se guarda la selección en el JSON del proyecto y se elige PDF u ODT
  en el diálogo de guardado, con la plantilla de cabecera y pie configurada y las variables sustituidas.
  Las fichas compartidas por varias partidas solo se incorporan una vez.
- La primera ventana permite marcar **Aplicar revisión de precios** e introducir
  la fórmula. Sin marcarla, exige una justificación de no aplicación. Se guarda
  la elección con el proyecto y se resuelve el bloque
  `{{#if APLICA_REVISION_PRECIOS}}…{{else}}…{{/if}}` de la biblioteca antes
  de sustituir las variables de la alternativa elegida. No se decide su
  procedencia automáticamente por importe. Hasta dos años de plazo, desmarca la
  revisión de partida y añade el motivo temporal a la justificación sin borrar motivos manuales.
  La casilla permanece disponible y respeta la decisión manual incluso para plazos
  cortos; al marcarla exige fórmula y justificación de aplicación, incluidas en el PPTP.
  En días naturales usa el inicio de la planificación y dos años de calendario;
  sin fecha o con un plazo no interpretable, la revisión queda pendiente de criterio manual.
- El PPTP comienza con un índice de contenidos. Los títulos y subtítulos se
  numeran jerárquicamente y se muestran en mayúsculas, también en el índice.
  Los párrafos se justifican y las secciones fluyen sin saltos de página
  forzados; solo se separa el índice inicial del cuerpo.
  En el primer paso, **Niveles del índice (1–6)** limita la profundidad del índice
  PDF y ODT (2 por defecto), sin quitar subtítulos del cuerpo. Se guarda con el proyecto.
  El apartado de fichas (capítulo 9 en la biblioteca actual) se agrupa por capítulos
  del presupuesto, siguiendo su orden y sus títulos. Las fichas compartidas aparecen
  solo en el primer capítulo que las utiliza. Dentro de cada grupo, las fichas
  completas se ordenan por código UNI, conservando definición, ejecución,
  medición y abono, sin cambiar la selección ni la edición basada en BCCA.
  Con dos niveles, el índice muestra los grupos; con tres, también las fichas.
- El apartado 9 incluye una tabla de dos columnas: capítulo del proyecto y ficha:
  cabecera azul en negrita y filas de texto normal alternadas en azul claro y blanco.
  Cada fila corresponde a un capítulo real del presupuesto (código y título),
  no a la numeración de secciones del PPTP. Todas sus fichas aparecen en la misma
  celda, con viñetas y sin número de sección; cada nombre enlaza con su
  ficha, tanto en PDF como en ODT. No hay columna de página.
  Los códigos, títulos completos y enlaces oficiales de las disposiciones legales,
  incluidas las UNE seleccionadas, aparecen en el apartado 7; la tabla no repite
  referencias normativas ni se añaden listados normativos al cuerpo de las fichas.
  Las tablas largas repiten la cabecera solo al continuar en otra página y conservan
  la alineación y los anchos de columna.
  La introducción y el título de la tabla se editan en el documento general del ZIP;
  `<!-- TABLA_FICHAS_CAPITULOS -->` inserta la tabla según las fichas seleccionadas.
- El PPTP no repite los títulos del documento y del proyecto en la cabecera.
  El ODT conserva las fuentes y los tamaños de los estilos específicos de la
  plantilla, pero normaliza el espaciado, la justificación del cuerpo, las listas
  con sangría y la continuidad de títulos. Si la plantilla solo tiene un título
  genérico, se aplica una jerarquía tipográfica por niveles. La cabecera y el pie
  personalizados no se modifican.
  Los estilos del PPTP se publican como estilos editables de Writer, con nombres
  `PPTP - Cuerpo`, `PPTP - Título 1` a `6`, `PPTP - Lista…`, `PPTP - Tabla…`,
  `PPTP - Índice…` y el estilo de carácter `PPTP - Énfasis`. En **Estilos → Gestionar
  estilos** (F11), modifica el estilo correspondiente para actualizar todos sus
  párrafos o fragmentos, sin seleccionarlos uno por uno. Se conserva la herencia
  de la plantilla y de los estilos derivados del cuerpo.
  En el pie de la plantilla, `{{capítulo}}` (o `{{capitulo}}`) se sustituye por
  «PLIEGO DE PRESCRIPCIONES TÉCNICAS PARTICULARES» y `{{no_pagina}}`
  (o `{no_pagina}`) por el número de página (campo automático en ODT).
  Solo se rellenan los marcadores presentes: no se añade título ni número si
  falta su marcador. El índice ODT es editable y actualizable desde Writer.

Los textos de `tools/pptp/BIBLIOTECA.zip` y `tools/pptp/docs` son material de trabajo: requieren revisión técnica y
contractual antes de utilizar el pliego. El asistente no certifica vigencia normativa.

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
generado, el instalador `GestorDeProyectos-Setup-2.2.0.exe` no requiere Python.
Incluye los componentes necesarios, pero no los archivos fuente `.py`. La
compilación Nuitka dificulta recuperar el código, aunque no puede impedir
completamente la ingeniería inversa.

Para ejecutar desde el código fuente se necesita Windows, Python 3.11 o
posterior y una sesión gráfica. PySide6 se instala desde `requirements.txt`.
No se necesita Excel para exportar hojas de cálculo ni para generar el Gantt
desde la aplicación.

Desde PowerShell, en la carpeta descargada:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe bc3_gui.py
```

La aplicación completa usa PySide6.

En modo desarrollo, la carpeta debe permitir escritura porque las preferencias
se guardan junto al código. La versión instalada guarda preferencias e historial
en `%APPDATA%\Gestor de Proyectos`. No es necesario activar el entorno virtual.

Al iniciar se muestra la imagen `iconos/Arranque.jpg`, centrada, sin marco y con esquinas redondeadas,
durante aproximadamente un segundo mientras se prepara la ventana principal. La imagen
se retira antes de mostrar la ventana principal maximizada.
La imagen también se incluye en la versión instalada.

## Uso rápido

1. Abre **Archivo → Nuevo proyecto** o importa tu presupuesto BC3.
2. Añade una base de precios en formato BC3 si necesitas incorporar partidas de referencia.
3. Selecciona una partida y pulsa una celda de **Mediciones** para editarla.
4. Completa **Archivo → Configuración** con la autoría y porcentajes.
5. Completa **Herramientas → RCDs** y guarda para incorporar la gestión de residuos al presupuesto.
6. Guarda el proyecto colaborativo por carpetas y genera los documentos desde **Informes**.
7. Continúa con **Herramientas → Planificación**, **Certificaciones** y **Facturación** para el seguimiento.

**Guardar** (Ctrl+S) actualiza la carpeta colaborativa, no el BC3 original. Cada
partida se guarda por UUID en un archivo independiente y no hay autoguardado
general; el indicador muestra los cambios pendientes.
No se crean copias de seguridad automáticas al abrir o importar archivos.
**Archivo → Guardar como proyecto colaborativo…** permite elegir una carpeta;
después, **Guardar** y **Ctrl+S** actualizan esa misma ubicación.
**Deshacer** (Ctrl+Z) revierte hasta las últimas 50 acciones de la sesión.

Puedes ajustar el ancho de los paneles arrastrando el separador. Si el nombre
del proyecto o de la base de referencia no cabe, se muestra con puntos
suspensivos; pasa el ratón por encima para ver el nombre completo.

Consulta el [manual de usuario](MANUAL_USUARIO.md) para el procedimiento completo.

### Motor BIM

En el menú principal, entre **Colaboración** e **Información**, elige
**Motor BIM → Abrir archivo IFC…**. IfcOpenShell
lee y triangula el IFC en segundo plano. Al terminar se abre una ventana
independiente con el visor VTK, el árbol espacial y el inspector de propiedades.

- Haz clic en una pieza del visor o del árbol para resaltarla e inspeccionar su
  GlobalId, clase IFC, materiales y conjuntos de propiedades y cantidades.
- Arrastra con el botón izquierdo para orbitar alrededor del último punto
  pulsado, con el central para desplazar la vista y usa la rueda para acercarla.
- Usa las casillas del árbol para ocultar elementos o ramas. **Aislar selección**
  muestra solo la selección; **Fantasma** deja translúcido el resto no oculto.
- Elige **Corte X**, **Y** o **Z** y desplaza el deslizador para ver el interior.
  **Invertir corte** cambia el lado visible.

El visor agrupa geometría y reutiliza mallas repetidas mediante instancias de GPU.
El modelo y los cambios de visualización no se guardan con el presupuesto.
Si el IFC contiene `GlobalId` duplicados, se avisa y los elementos afectados se
distinguen por su id STEP; sus mediciones no se pueden incorporar hasta corregir
los identificadores en el IFC.

**Mediciones IFC → BCCA.** Carga una base de referencia BC3 en el presupuesto,
selecciona una pieza o rama en el visor y pulsa **Añadir selección o rama** en
el panel inferior izquierdo. A la derecha, el visor busca primero un código
BCCA explícito, después aplica la tabla IFC 4.3 y, si no hay regla, sugiere una
partida por descripción. Los casos
sin equivalencia o sin cantidad compatible quedan visibles y se pueden resolver
indicando una partida o cantidad manualmente.

**Ver tabla IFC 4.3 → BCCA** muestra las equivalencias iniciales, verificadas
contra BCCA 2023 V02. No hay un código universal por clase IFC: cada fila exige
características adicionales o un tipo con nombre exacto. Puedes elegir una
partida y usar **Guardar regla para este tipo IFC…** para reutilizar la
equivalencia en otros modelos. Las reglas personales se guardan en
`bim_equivalencias.json`, junto a las preferencias del programa.

**Incorporar mediciones al presupuesto** añade una línea por `GlobalId` a la
partida BCCA correspondiente, creándola si aún no existe. Usa **Guardar** en el
presupuesto para conservar las líneas y sus vínculos; las propuestas no
incorporadas son de revisión temporal. No se modifican el IFC ni el BC3 base.

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

El archivo [requirements.txt](../requirements.txt) contiene las dependencias de uso:

| Paquete | Uso |
|---|---|
| PySide6 | Ventana principal, controles y ciclo de eventos Qt |
| Pillow | Icono e imágenes de la interfaz |
| odfpy | Exportación de hojas de cálculo ODS |
| openpyxl | Conversión independiente de planificación a XLSX |
| ReportLab | Informes PDF |
| Matplotlib | Diagramas de Gantt |
| pdfplumber | Lectura local de tablas de facturas PDF |
| pypdfium2 | Visor PDF integrado de facturas |

Los informes también se generan como ODT editable con los módulos estándar de Python.
El formato se elige al guardar cada informe: ODT editable aparece seleccionado
por defecto y también se puede elegir PDF. El diálogo utiliza el idioma global.
Tkinter ya no es necesario.

## Organización

## Proyectos colaborativos divididos

Los proyectos nuevos y **Guardar como proyecto colaborativo…** crean una carpeta:

```text
Proyecto/
  proyecto.json
  capitulos/cap_<uuid>.json
  partidas/part_<uuid>.json
  planificacion/tareas_gantt.json
  herramientas/*.json
```

Cada partida tiene un UUID estable. Al editarla se adquiere un `part_<uuid>.lock`
con usuario, equipo, token y caducidad; el lock se renueva mientras siga abierta
la edición y se libera al cerrar. Los guardados usan temporales únicos, `fsync`
y sustitución atómica. Si otro colaborador cambió la misma entidad, el guardado
se detiene en vez de sobrescribirla. Los totales y el Gantt se calculan siempre
en memoria.

Al crear o migrar puedes elegir **Público, sin cifrar** o **Protegido con
contraseña**. En modo protegido, cada JSON se cifra con AES-256-GCM y una clave
PBKDF2-HMAC-SHA256; la contraseña no se guarda en la carpeta. El menú
**Colaboración** permite abrir, migrar, cambiar protección, copiar bibliotecas
instaladas y consultar el estado. Las bibliotecas se conservan legibles en modo
público y cifradas en modo protegido, donde se materializan únicamente en una
caché temporal durante la sesión.

La presencia colaborativa muestra en la barra inferior el número de usuarios
activos y sus apodos. El apodo se configura en **Archivo → Configuración →
Colaboración** y los heartbeats caducan automáticamente.

**Colaboración → Fuentes de bibliotecas…** permite usar la biblioteca incluida
con cada módulo o seleccionar una ruta ZIP externa en el equipo. PPTP admite
además una carpeta `docs` externa.

Los locks son por sección: edición general, mediciones o ciclos futuros de otra
sección. Además, el guardado aplica una fusión de tres vías; cambios concurrentes
en campos distintos de una misma partida se conservan automáticamente. Solo hay
conflicto cuando dos usuarios modifican el mismo campo.

Los JSON monolíticos anteriores siguen pudiendo abrirse. Usa **Guardar como
proyecto colaborativo…** para migrarlos sin perder compatibilidad histórica.

| Archivo o carpeta | Función |
|---|---|
| [bc3_gui.py](../bc3_gui.py) | Entrada PySide6 y fachada del modelo |
| [budget_qt_window.py](../budget_qt_window.py) | Coordinación de la ventana principal PySide6 |
| [budget_qt_dialogs.py](../budget_qt_dialogs.py) | Diálogos reutilizables de PySide6 |
| [budget_core.py](../budget_core.py) | Modelos, cálculos y lectura FIEBDC sin GUI |
| [budget_project.py](../budget_project.py) | Lectura, validación y persistencia de proyectos |
| [budget_config.py](../budget_config.py) | Metadatos y constantes compartidas |
| [tool_loader.py](../tool_loader.py) | Descubrimiento y validación de herramientas opcionales |
| [tools/](../tools/) | Módulos opcionales y sus auxiliares |
| [tools/bim/](../tools/bim/) | Visor IFC y mediciones BCCA |
| [tools/rcd/](../tools/rcd/) | Estimación, revisión y presupuesto de gestión de RCDs |
| [tools/safety_health/](../tools/safety_health/) | Seguridad y Salud y su matriz CSV en `datos/` |
| [tools/planning/](../tools/planning/) | Planificación, ventana, conversor XLSX y dibujo del Gantt |
| [tools/rcd/document.py](../tools/rcd/document.py) | Generación de informes de gestión de RCDs |
| [tools/safety_health/document.py](../tools/safety_health/document.py) | Generación del estudio de Seguridad y Salud |
| [tools/planning/document.py](../tools/planning/document.py) | Generación del informe de planificación |
| [tools/pptp/](../tools/pptp/) | Asistente PPTP, biblioteca `BIBLIOTECA.zip` y secciones generales en `docs/` |
| [tools/certifications/](../tools/certifications/) | Previsiones y certificaciones mensuales |
| [tools/invoicing/](../tools/invoicing/) | Facturas PDF y matriz editable de costes por recurso |
| [tools/_shared/](../tools/_shared/) | Cálculo de plazos y marcas persistentes compartidos |
| [tools/price_justification/qt_window.py](../tools/price_justification/qt_window.py) | Herramienta de justificación de precios migrada a PySide6 |
| [tools/rcd/qt_window.py](../tools/rcd/qt_window.py) | Herramienta de RCDs migrada a PySide6 |
| [tools/safety_health/qt_window.py](../tools/safety_health/qt_window.py) | Herramienta de Seguridad y Salud migrada a PySide6 |
| [tools/pptp/qt_window.py](../tools/pptp/qt_window.py) | Asistente PPTP migrado a PySide6 |
| [tools/certifications/qt_window.py](../tools/certifications/qt_window.py) | Plan económico y certificaciones migrados a PySide6 |
| [tools/planning/qt_window.py](../tools/planning/qt_window.py) | Planificación y Gantt migrados a PySide6 |
| [tools/invoicing/qt_window.py](../tools/invoicing/qt_window.py) | Facturación PDF y conciliación de costes migradas a PySide6 |
| [reference_search_qt.py](../reference_search_qt.py) | Consultas NTP, UNE y LER con PySide6 |
| [budget_reports.py](../budget_reports.py) | Configuración, cálculos e informes |
| [report_layout.py](../report_layout.py) | Estilos y maquetación PDF compartida |
| [odt_reports.py](../odt_reports.py) | Exportación editable de informes ODT |
| [ods_export.py](../ods_export.py) | Exportación editable de hojas de cálculo ODS |
| [ui_theme.py](../ui_theme.py) | Tema de la interfaz |
| [tools/planning/figura_gantt.py](../tools/planning/figura_gantt.py) | Dibujo del Gantt; utilizado por planificación |
| [tools/planning/bc3_to_xlsx_plan.py](../tools/planning/bc3_to_xlsx_plan.py) | Conversor independiente de planificación a XLSX |
| [iconos/](../iconos/) | Icono de la aplicación y logo institucional de los informes |
| [plantillas/plantilla.odt](../plantillas/plantilla.odt) | Cabecera y pie personal de los informes de certificación |
| [Manual de usuario](MANUAL_USUARIO.md) | Procedimientos paso a paso |

Los conversores antiguos de imágenes y las imágenes que no utiliza la aplicación
se han trasladado a una carpeta externa; no son necesarios para ejecutarla.

### Herramientas opcionales

Al arrancar, el programa lee la carpeta `tools` situada junto a la aplicación,
independientemente del directorio de trabajo. Solo los módulos encontrados y
válidos se añaden a **Herramientas**. Si la carpeta falta o está vacía, la
aplicación sigue funcionando sin herramientas. Los errores de carga se muestran
con un aviso, sin ocultar las herramientas válidas.

Cada herramienta tiene su propia carpeta dentro de `tools`. Para añadir una,
crea `tools/mi_herramienta/__init__.py`, coloca sus scripts y recursos en esa
misma carpeta y reinicia. El contrato mínimo de `__init__.py` es:

```python
TOOL_LABEL = "Mi herramienta"
TOOL_ORDER = 100  # Opcional; orden ascendente en el menú.


def open_tool(app):
    """Abre la herramienta usando la instancia de BudgetApp."""
    # Construir aquí la ventana o ejecutar la acción.
```

Por compatibilidad también se reconocen módulos `.py` sueltos. Solo se buscan
entradas en el primer nivel de `tools`: los scripts internos de una herramienta
no se convierten en opciones adicionales del menú.

Los nombres que empiezan por `_` se reservan para auxiliares y no aparecen en el
menú. En Qt, `open_tool_qt(app)` abre la herramienta y el punto de entrada
opcional `install_toolbar_qt(app, toolbar)` permite añadir un botón propio a la
barra principal. También se admiten `is_available(app) -> bool`, para desactivar la opción,
`restore_project_state(data) -> dict`, para validar el estado de las herramientas
integradas, y `before_project_save(app)`, para su sincronización antes de guardar.
El núcleo conserva los estados JSON existentes aunque falte su herramienta y
avisa al abrir un proyecto con datos de módulos no disponibles; no los recalcula.
Los cálculos compartidos están en `tools/_shared/scheduling.py`, por lo que
retirar la carpeta `planning` no impide utilizar Certificaciones ni PPTP.
Conserva `tools/_shared` mientras quede alguna herramienta que la utilice.
Los recursos exclusivos se resuelven desde la carpeta de su herramienta:
`tools/safety_health/datos`, `tools/pptp/BIBLIOTECA.zip` y `tools/pptp/docs`.

PPTP detecta `BIBLIOTECA.zip` al abrir su asistente, sin extraerlo ni necesitar
una carpeta `archivos` en disco. El ZIP contiene un único `catalogo.json` con
formato `biblioteca-fichas-uni`, `version_esquema: 1`, `total_fichas`, `indice`
y la lista `fichas`. Cada ficha declara `id`, `codigo_uni`, `titulo`, `archivo`
y las rutas son relativas a la carpeta interna del catálogo y usan `/`. Se
conserva el árbol de carpetas UNI; los identificadores y las rutas de
las fichas deben ser únicos, aunque varias compartan código UNI.

La última letra BCCA se representa con fichas, no con subcarpetas: por ejemplo,
`01RP_PELDAÑOS/01RPN-Retirada de peldaños - piedras naturales.md`. Se conservan
1.621 fichas específicas de tres letras BCCA, con las prescripciones de las
649 comunes sustituidas integradas en sus descendientes. Solo permanecen las
35 fichas comunes sin sustituto: 1.656 fichas en total.
Las fichas nuevas recogen la clasificación y las descripciones del BC3 de origen;
cuando no hay partidas desarrolladas, indican que deben completarse las
prescripciones. No se inventan condiciones técnicas ausentes.
El catálogo admite `rutas_anteriores` para recuperar decisiones de proyectos
guardados antes de renombrar una ficha y `fichas_sustituidas` para resolver las
comunes retiradas a sus específicas según el código de la partida. Si el código
no permite distinguir el material, se conservan las alternativas para revisión
manual; no se escoge una arbitrariamente. El editor permite crear y renombrar fichas
en carpetas terminales y actualiza catálogo, índice y enlaces en una sola operación.

La reorganización puede reproducirse con
`python -m tools.pptp.bcca_library tools\pptp\BIBLIOTECA.zip tools\BCCA2023_V02.bc3`.
El BC3 solo se necesita para esa operación de mantenimiento, no para utilizar PPTP.
Repetirla no duplica fichas ni cambia una biblioteca ya migrada; conserva el
contenido técnico al integrar las comunes y delimitar la información interna.

Las líneas independientes `"""` delimitan bloques informativos no exportables
en las fichas PPTP. El editor los resalta en amarillo; la composición excluye su
contenido antes de sustituir variables y numerar encabezados, tanto en PDF como
en ODT. La migración marca metadatos y trazabilidad sin borrar las prescripciones.
Los bloques sin cerrar y las fichas sin contenido contractual producen un error
explícito, no documentos vacíos. Las tres clasificaciones `18RPA`, `18RPB` y
`18RPC` carecen de prescripciones heredadas y requieren completarlas antes de
generar un pliego con ellas.

La búsqueda usa el JSON, no recorre los textos Markdown ni interpreta el índice.
`INDICE_FICHAS.md` permanece dentro del ZIP como índice de lectura para las
personas. Solo al generar se leen las fichas seleccionadas. No se incorporan al
pliego los documentos de origen del ZIP ni el índice.
Si reemplazas el ZIP con PPTP abierto, cierra y vuelve a abrir el asistente.
Los 1.666 documentos Markdown de la biblioteca PPTP incluyen un historial interno
inicial de la distribución v1.6.0 (versión 1, fecha 10/11/2026). El editor muestra
la versión y el último autor; exige autor y fecha válidos para cada guardado con
cambios y permite describirlo opcionalmente. Las revisiones se incrementan
automáticamente, conservan las entradas anteriores y nunca se exportan al pliego.
Se leen todos los Markdown con prefijo numérico de dos dígitos de la raíz del
ZIP, en orden de nombre (actualmente, secciones 01 a 09). Las fichas se añaden
al último apartado general. Los archivos sin ese prefijo no se incorporan.
`tools/pptp/docs` se admite como respaldo para bibliotecas antiguas sin documentos
generales en el ZIP, sin mezclar ambos orígenes.
La normativa propuesta se busca por sus marcas `NORMATIVA_UNI` en todas las
secciones (actualmente, la 07). Las referencias seleccionadas se mantienen en
su ubicación original junto con el resto del texto general.
El apartado 07 del ZIP incluye también las UNE del listado
`tools/pptp/docs/UNE_tematica_por_codigo_BCCA.csv`, con identificador estable,
designación, título, enlace oficial, prefijos BCCA y condición de aplicación.
Se ofrecen para revisión, con la edición más reciente por familia preseleccionada:
la coincidencia temática aproximada no
acredita por sí sola su aplicabilidad ni obligatoriedad.

Para actualizar esas referencias desde un CSV previamente depurado de UNE no
vigentes, ejecuta desde la raíz del proyecto:

```powershell
python -m tools.pptp.une_library tools\pptp\BIBLIOTECA.zip tools\pptp\docs\UNE_tematica_por_codigo_BCCA.csv --vigentes-confirmadas
```

El CSV no incluye un campo de vigencia: la opción confirma que el responsable
del listado ya la ha comprobado; el script no consulta ni certifica ese estado.
Reúne las asociaciones por norma, conserva los identificadores existentes y
actualiza únicamente el apartado 07 con historial mediante guardado atómico.
Repetir la importación sin cambios no modifica el ZIP. Las referencias de
importaciones anteriores se reemplazan por el catálogo actual; la normativa
preexistente ajena al bloque importado se conserva.

El programa informa de bibliotecas inválidas, fichas ausentes o modificadas y
secciones que falten, sin generar documentos incompletos.

En la distribución instalada, el script de compilación genera extensiones `.pyd`
separadas, conserva la estructura de carpetas y copia los recursos de cada
herramienta, sin distribuir su código fuente. Para retirar una herramienta,
mueve su carpeta completa fuera de `tools` y reinicia. Los complementos nuevos deben ser
compatibles con la versión y arquitectura de Python usadas para compilar la app;
si requieren dependencias nuevas, estas también deben incluirse en la distribución.
Los módulos se ejecutan como parte del programa: instala solo herramientas de
confianza.

Con Python 3.11 o 3.12, la compilación utiliza MinGW64. Con Python 3.13 o
posterior, instala Visual Studio Build Tools con la carga de trabajo de C++:
el script exige MSVC para compilar tanto el ejecutable como sus extensiones.

### Datos no distribuidos

Este repositorio **no contiene presupuestos BC3 reales, bases BCCA, proyectos JSON,
plantillas PDF originales, historial ni configuración personal**.
El programa funciona con los archivos que aporte cada usuario.

Los informes se reconstruyen mediante código; no requieren abrir los PDF
originales de ejemplo. El recurso gráfico del logo se encuentra en
[iconos/logo_odt.jpg](../iconos/logo_odt.jpg).
Los informes de certificación pueden usar la cabecera y el pie de
[plantillas/plantilla.odt](../plantillas/plantilla.odt), opción que se controla
desde **Archivo → Configuración…**. La plantilla se incluye en el instalador.

La aplicación arranca siempre vacía, aunque haya BC3 o proyectos JSON en su
carpeta. Abre un proyecto desde **Archivo → Recientes** o **Abrir proyecto (JSON)**,
o utiliza **Importar presupuesto BC3**. Las bases se añaden mediante
**Archivo → Añadir base de referencia BCCA**. El historial y la configuración
guardada se conservan, pero no se abre ningún proyecto automáticamente.

## Pruebas y documentación del código

Las pruebas se ejecutan localmente. El flujo **Pruebas** de GitHub Actions no
se lanza al subir cambios ni al abrir pull requests; queda disponible solo
para ejecución manual desde **Actions → Pruebas → Run workflow**.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m unittest discover -v
```

Las pruebas de interfaz necesitan un escritorio disponible. El presupuesto
[tests/test.bc3](../tests/test.bc3) contiene datos ficticios: se conserva la estructura
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

El instalador generado aparece en `dist\GestorDeProyectos-Setup-2.2.0.exe`.
Permite instalar todos los módulos, solo el programa base o una selección
personalizada. Los catálogos de **Consultas** están disponibles en las tres
modalidades. En la selección se advierte que las correspondencias automáticas de
**PPTP** y **Seguridad y Salud** están diseñadas para códigos BCCA; en **RCDs**
el cruce LER–BCCA2023 es complementario y no impide usar otras bases.

El script usa automáticamente `.\.venv\Scripts\python.exe` si existe; en caso
contrario, utiliza el comando `python` disponible en el sistema.
La distribución incluye la licencia y el manual, pero no los presupuestos ni
los archivos fuente. El ejecutable compilado no equivale a cifrado y no ofrece
protección absoluta frente a ingeniería inversa. La licencia MIT permite
distribuir binarios siempre que se conserve el aviso y el texto de licencia.

Los scripts auxiliares también admiten uso independiente:

```powershell
python -m tools.planning.bc3_to_xlsx_plan "C:\datos\obra.bc3" "C:\datos\planificacion.xlsx"
python -m tools.planning.figura_gantt "C:\datos\planificacion.xlsx" "C:\datos\plan_de_trabajo"
```

El segundo comando necesita fechas calculadas en el Excel. Puede utilizar
LibreOffice si está instalado o los valores guardados previamente por Excel.
Esto no afecta al Gantt generado directamente desde la interfaz.

## Licencia y responsabilidad

El código se distribuye bajo la [licencia MIT](../LICENSE).
Copyright © 2026 Manuel Cobos Budia.

El software se proporciona sin garantías. Antes de utilizar los resultados,
comprueba datos, mediciones, precios, porcentajes y documentación generada.
La aplicación no sustituye la revisión técnica ni la responsabilidad profesional.

Los nombres, logotipos y signos institucionales identifican a sus respectivos
titulares. La licencia del código no concede derechos de marca ni implica
respaldo institucional a modificaciones o redistribuciones.
