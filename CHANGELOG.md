# Changelog

## 1.7.0 — 2026-11-17

### Añadido
- Herramienta «Justificación de precios» como primera opción del menú, con
  biblioteca de ocho documentos base Markdown y editor con historial como el
  del PPTP. Incluye el texto técnico y las fórmulas de composición, indirectos,
  mano de obra y maquinaria como texto nativo con subíndices y exponentes.
  Lee el emplazamiento y demás datos comunes de la configuración general,
  conserva los datos particulares y notas previas en el proyecto y genera un
  único anejo PDF/ODT con índice y los listados reutilizados de elementales,
  auxiliares y descompuestos, sin recalcular los precios presupuestados.
- Menú Consultas entre Herramientas e Información, con buscadores independientes
  de NTP y UNE. Filtrado inmediato por código, título y metadatos, sin distinguir
  tildes ni mayúsculas; detalle de la selección y apertura de la ficha oficial.
  Se usan los catálogos locales completos, incluidas normas anuladas y proyectos
  UNE, sin modificar el presupuesto ni reproducir el texto de las normas.
- Selección múltiple de municipios en la configuración general, con desplegable
  alfabético filtrable y provincia y comunidad autónoma derivadas de CMUN/CPRO.
  Selección múltiple de códigos CPV del capítulo 45 desde el catálogo suministrado.
  Ambas selecciones se conservan en el proyecto y como valores iniciales generales.
- Guardado de la planificación en la plantilla PDF u ODT, en horizontal, con
  cronograma tabulado en lugar de una imagen, editable en ODT. Incluye un párrafo
  con el plazo total calculado y la actividad de mayor duración.
- Generación del Estudio de Seguridad y Salud o Estudio Básico completo desde
  las plantillas, fichas y catálogos de la biblioteca, en PDF u ODT.
- Resumen de la actuación editable en la primera ventana de Seguridad y Salud,
  inicialmente tomado del resumen guardado en PPTP, con persistencia propia
  y sin sustituirlo por una enumeración automática de partidas.
- Estimación de los cuatro criterios del artículo 4.1 del RD 1627/1997 con
  justificación numérica y casillas editables. Se admite cumplir varios criterios;
  si no se marca ninguno, se propone un Estudio Básico.
- Editor de condicionantes externos con descripción y aplicabilidad
  Sí / No / Pendiente, guardado independientemente del presupuesto.
- Filas de medios auxiliares y maquinaria extraídas de las partidas y sus
  descompuestos anidados, sin duplicados. Servicios afectados y riesgos detectados
  con sus descripciones, y última fila de centros asistenciales con dirección
  completa, teléfonos, observaciones y emergencias 112.
- Revisión de precios del PPTP con elección editable, fórmula y justificaciones,
  propuesta de no revisión hasta dos años y conservación de la decisión manual.
- Tabla de disposiciones legales del asistente PPTP con las partidas y fichas
  vinculadas a cada referencia, detalle de sus condiciones y selección revisable.

### Cambiado
- La exportación en plantilla pasa a ser la salida principal de planificación.
  La figura detallada PNG/PDF se mantiene como opción secundaria. El pie muestra
  Planificación y el número de página; no se anteponen el proyecto ni Plan de trabajo.
- La propuesta de unidades preventivas se revisa en una ventana separada. Su
  aceptación integra el presupuesto preventivo en el siguiente capítulo CAP. XX;
  el botón de informe genera el estudio completo, no un presupuesto SS independiente.
- ESS/EBSS con índice enlazado y páginas reales, numeración jerárquica y formato
  equivalente al PPTP, sin portada ni títulos repetidos. El pie identifica el
  tipo de estudio y conserva la numeración de página.
- Primera ficha del estudio reorganizada: objeto y alcance, criterios cumplidos
  y tabla resumen con leyenda superior, cabecera azul y filas alternas azul
  claro/blanco. Eliminada la ficha 0 y los resúmenes redundantes de unidades,
  partidas importadas y códigos.
  Se renombra a `01_objeto_y_alcance.md` y se elimina el epígrafe «Datos del
  proyecto», manteniendo la tabla. Los criterios se redactan con mayúscula
  inicial, reservando las mayúsculas del tipo de estudio en la conclusión.
- Resumen ESS/EBSS con denominación del proyecto, técnicos redactores, PEM,
  PEC con IVA, CPV y capítulos con viñetas, duración de planificación y superficie
  máxima de las partidas en m². Valores dinámicos en mayúsculas, preservando m²,
  y condicionantes externos antes de los riesgos.
- Memoria ESS/EBSS con tabla de nueve grupos de riesgo, situaciones previsibles
  y criterios preventivos generales, con cabecera azul y filas alternas en PDF
  y ODT. Criterios comunes, comprobaciones previas y delimitación consolidados,
  sin eliminar las obligaciones particulares del pliego, emergencias y fichas.
- Catálogo preventivo a continuación de la tabla de riesgos, con leyenda,
  cabecera azul y filas alternas. Referencias enlazadas a su fila de definición
  desde párrafos y tablas en PDF/ODT, incluidas variantes con tilde. Ampliadas
  de forma concisa las 50 definiciones de riesgos y varias medidas y EPI higiénicos.
- Eliminados del informe y del índice el anexo de trazabilidad con la relación
  de partidas y el apartado de datos pendientes. La confirmación de generación
  muestra únicamente el documento generado y Aceptar, como en PPTP, sin avisos.
- Condiciones generales y gestión preventiva integradas en la memoria del
  capítulo 2, sin capítulos independientes duplicados. La tabla del capítulo 1
  muestra los códigos de riesgos y medidas preventivas en filas separadas,
  enlazados al catálogo y separados por coma y espacio, sin viñetas ni definiciones.
- Ampliada la protección de terceros con control de accesos y señalización,
  vertidos, residuos, acopios, polvo, humos, ruido, vibraciones, deslumbramientos,
  barro y circulación de maquinaria. Texto adaptado a cada obra, sin datos del
  proyecto de ejemplo ni dimensiones o equipos fijos; consulta y participación
  de los trabajadores integradas en la coordinación preventiva.
- Integrados en el capítulo 2 protecciones colectivas, tabla orientativa de nueve
  EPI con leyenda y formato azul/blanco, señalización, equipos de trabajo, acopios,
  iluminación y circulación. Ampliadas instalaciones provisionales, servicios
  higiénicos, instalación eléctrica, primeros auxilios, accidentes, incendios,
  derrames y formación, sin duplicados ni dotaciones universales del ejemplo.
- Capítulo 2 denominado Memoria descriptiva, sin el apartado redundante de datos
  y condicionantes; alcance de las fichas trasladado al capítulo 3 y títulos
  revisados con tildes. Catálogo preventivo con introducción y desarrollo
  posterior de las obligaciones del contratista en el PSS.
- Catálogo con una cabecera por página y tipos consecutivos: una etiqueta por
  página en PDF y celdas combinadas por grupo en ODT, sin exigir LibreOffice.
  Sangrías en viñetas y enumeraciones, incluidas las celdas del resumen.
  Eliminados los estados de aplicabilidad de la narración de instalaciones y
  coordinación, conservándolos en la tabla resumen; centros de primeros auxilios
  referidos a la Tabla 1.
- Capítulo 3 reorganizado con introducción, tabla resumen de todas las unidades,
  riesgos y medidas preventivas, con leyenda superior y formato azul/blanco.
  Las fichas específicas aparecen a continuación, sin duplicados y vinculadas
  a sus partidas BCCA, sin códigos UO ni capítulos separados de matriz y fichas.
- Reorganizadas las 11 fichas preventivas existentes en las carpetas y subcarpetas
  de PPTP, con nombres por código y título, sin extender el catálogo a todas
  sus familias. Cada ficha incluye definición y alcance, riesgos y medidas
  preventivas, con protecciones colectivas, EPI, equipos y validación como
  subapartados textuales. Revisadas las once fichas: títulos visibles sin código,
  sin párrafos de unidades vinculadas ni clasificación; riesgos como códigos
  enlazados al catálogo; protecciones, EPI y equipos en viñetas, y Validación
  como último apartado independiente. Se conservan el alcance, el primer bloque
  de medidas y las condiciones específicas. Sumideros varios dispone de ficha autónoma completa.
  Las unidades sin ficha se señalan expresamente como pendientes y ya no se
  clasifican por palabras de su descripción. Revisadas las correspondencias
  de residuos bituminosos y ensayos; compatibilidad revisable de 17RRBN con 17RB.
- Nuevo capítulo 4: Seguimiento, control, coordinación, formación y documentación.
  Integra controles preventivos, coordinación empresarial, formación, documentos,
  vigilancia de la salud, primeros auxilios, servicios ocultos, recursos preventivos
  y trabajos posteriores. Formación, coordinación, documentación y previsiones de
  mantenimiento se trasladan desde el capítulo 2 para evitar repeticiones.
  Se mantienen consentimiento y confidencialidad sanitaria, sin revisiones médicas
  genéricas; servicios y recursos preventivos se condicionan a la situación real
  y a los supuestos exigibles, sin copiar una línea de alta tensión del ejemplo.
- ESS organizado en Documento I (Memoria, capítulos 1 a 4) y Documento II
  (Pliego de condiciones), dentro del mismo PDF/ODT y con índices propios
  enlazados y actualizables en ODT, sin mezclar entradas entre documentos.
  El pliego reinicia la numeración con Objeto del presente pliego y su listado
  de condiciones; siguen los capítulos de naturaleza legal, facultativa,
  técnica y económica (2 a 5), conservando el contenido de los cuatro bloques
  anteriores y actualizando índices y referencias. El capítulo 4 mantiene
  apartados para protecciones, maquinaria/equipos y señalización/balizamiento/
  protección de terceros.
  Se amplían las obligaciones de los agentes, la aprobación del PSS, el control
  documental, las modificaciones y la certificación con el importe preventivo
  real del presupuesto, adaptando las referencias del ejemplo a cada obra.
- El capítulo de naturaleza económica incorpora valoración mensual salvo pacto
  en contrario, precios contratados y aprobación previa al abono, pago según
  contrato, valoración de unidades no previstas y comunicación escrita de
  revisiones de precios con aprobación previa.
- Integrados en el capítulo facultativo el PSS y sus modificaciones, acta o
  resolución de aprobación, comunicación de apertura, libros y control de la
  efectividad. Cuadro de seguimiento, cuatro índices con fórmulas coherentes
  y tratamiento de denominadores cero, investigación de accidentes y cierre
  verificado de deficiencias. Actualizados Libro de Incidencias, Subcontratación,
  Órdenes y diligencias inspectoras; sin exigir un nuevo Libro de Visitas ni
  visado obligatorio general del acta. El capítulo económico conserva el abono
  y la certificación e incorpora garantías, precios, mediciones, plazos y
  liquidación según contrato, sin duplicar trámites facultativos.
- Completadas las condiciones facultativas con consulta y participación,
  delegados, comité y obligaciones de los trabajadores; precisados los recursos
  preventivos y la documentación vigente. El coordinador se integra en la
  Dirección Facultativa sin asumir automáticamente competencias económicas.
  El comité se condiciona a los supuestos de empresa o centro con 50 o más
  trabajadores, no a sumar plantillas de distintas empresas. No se reproducen
  remisiones indiscriminadas de todas las anotaciones a la Inspección,
  demolición automática, homologaciones genéricas ni modelos documentales antiguos.
- Desarrollados los medios de protección colectiva e individual en el apartado
  4.1 y añadidas las instalaciones provisionales en el 4.4: vestuarios, aseos,
  duchas, retretes, comedores y descanso. Responsabilidad empresarial del
  mantenimiento, marcado CE conforme al régimen aplicable y dimensionado
  según plantilla simultánea, normativa y convenio, sin ratios universales.
- Condiciones técnicas del ESS con núcleo general fijo y diez bloques
  específicos revisables. Propuestas por referencias de fichas, recursos
  físicos y condicionantes confirmados, con inclusión manual de los no
  detectados y conservación de exclusiones expresas.
  Se completan cerramientos, huecos, pasos, maquinaria, elevación,
  señalización, señalistas y protección de terceros. La selección se guarda
  en el proyecto, se conserva al recalcular y se confirma antes de exportar
  cuando cambian sus fuentes o prescripciones. PDF y ODT comparten el filtro
  y la numeración; los borradores programáticos sin revisión lo advierten.
- Confirmación persistente de normativa preventiva y NTP por unidades de obra.
  Propuestas basadas en códigos BCCA y riesgos/medidas expresos de las fichas,
  con exclusiones y asignación manual desde el catálogo oficial local.
  Las coincidencias solo temáticas y las referencias con avisos no se marcan
  automáticamente. Una modificación de las unidades o sus fichas requiere
  nueva confirmación. El capítulo legal del Pliego incorpora las normas
  confirmadas, condiciones generales, prioridad de protección colectiva y una
  tabla de dos columnas en el apartado 2.5, por capítulo presupuestario y NTP
  seleccionadas en viñetas. Cada NTP compartida figura solo en el primer capítulo
  seleccionado según el orden del presupuesto. Se mantienen los riesgos y
  medidas en las fichas de la Memoria y las NTP quedan
  identificadas como guías técnicas y no como disposiciones obligatorias.
- Nuevo apartado 2.4 de UNE por familias de equipos y protecciones, antes de las
  NTP. Reutiliza el catálogo UNE local, incluye solo referencias vigentes y
  excluye proyectos, normas anuladas y familias homónimas ajenas a prevención.
  Su aplicación se condiciona a los equipos reales y a la revisión de edición,
  modificaciones, correcciones y exigibilidad, sin confirmar obligatoriedad.
- Selección persistente de capítulos antes de iniciar el análisis de Seguridad
  y Salud. RCD y Control de Calidad se desmarcan por defecto, con inclusión
  expresa opcional. Propuestas, fichas, medios, planificación y normativa se
  limitan al ámbito marcado, sin alterar los importes globales del proyecto
  ni su criterio legal por presupuesto. El porcentaje preventivo usa la base
  seleccionada; cambiar el ámbito obliga a revisar de nuevo sus propuestas.
- PPTP agrupado por capítulos del presupuesto, con tabla de fichas enlazadas,
  colores alternos e índice de profundidad configurable. Las disposiciones
  legales y UNE seleccionadas se agrupan por ámbitos temáticos en su apartado,
  sin repetirse en la tabla de fichas.
- Preselección de la UNE más reciente por familia, sin distinguir partes,
  conservando las decisiones revisadas manualmente al recalcular o reabrir.
  La columna Referencia muestra el texto sin duplicar identificadores ni estados.
- Bibliotecas PPTP reorganizadas por códigos BCCA terminales, con creación y
  renombrado de fichas y recuperación de rutas anteriores y fichas comunes sustituidas.
  Editor con fichas utilizadas en verde y paneles separados para referencias
  internas amarillas no exportables y prescripciones; historial de revisiones
  con autor, fecha y descripción.
- Estilos PPTP editables en Writer y normalización de listas, títulos, párrafos,
  tablas y pies, conservando la cabecera institucional y la plantilla.
- NTP conserva Ver ficha oficial como acción de consulta, sin botón Abrir PDF.
  Instalador actualizado con los catálogos territoriales, CPV, NTP y UNE.
- Aplicación, metadatos del ejecutable e instalador actualizados a 1.7.0.
  Se conserva la identificación histórica inicial v1.6.0 de las bibliotecas.

### Corregido
- Estimación del máximo simultáneo en personas enteras, con al menos una persona
  por equipo con carga de trabajo, evitando resultados fraccionarios como 0,99.
- Alineación y ancho de las tablas al continuar en otra página, incluidas las
  divisiones dentro de filas largas en PDF y las sangrías de celdas ODT.
- Conservación de la dirección SICESS completa y del modo activo de centros
  asistenciales, sin recuperar datos del modo inactivo.
- Sustitución de marcadores de capítulo y página con y sin tilde, y tratamiento
  de tildes y caracteres especiales en el editor y los documentos generados.

## 1.6.0 — 2026-11-10

### Añadido
- Editor de bibliotecas Markdown de PPTP y Seguridad y Salud.
- Fichas de materiales y maquinaria BC3 en la biblioteca de Seguridad y Salud.
- Centros asistenciales con texto libre o tabla SICESS, resumen con dirección y
  teléfono y conservación de los datos en el proyecto.

### Corregido
- Conservación de la sangría de las listas de PPTP al generar PDF y ODT.
- Acceso a RCDs y Seguridad y Salud sin modificar el presupuesto al abrirlos.
- Los capítulos vacíos no bloquean la recreación de sus partidas.

## 1.5.3

### Corregido
- El instalador fija el backend no interactivo Agg de Matplotlib para que la
  generación de la figura de planificación funcione de forma estable desde la
  aplicación distribuida.

## 1.5.2

### Corregido
- Inclusión explícita del módulo de figura de planificación y del backend
  no interactivo de Matplotlib en el instalador, para generar el diagrama de
  Gantt desde la aplicación distribuida.

## 1.5.1

### Añadido
- Vista de solo lectura en Facturación para unificar y sumar los recursos
  iguales de distintas partidas.
- Enlace a la Base de Precios de Referencia desde la información de la
  aplicación.

### Cambiado
- Los filtros de recursos del presupuesto se sitúan junto al título del
  proyecto.
- Las herramientas RCDs y Seguridad y salud se desactivan cuando el presupuesto
  ya contiene capítulos de esas especialidades no generados por la aplicación,
  para evitar duplicidades y preservar sus datos.

### Corregido
- Expansión de los elementos de la Base de Precios de Referencia y restauración
  del divisor al volver a mostrar las bases de referencia.

## 1.5.0

### Añadido
- Facturación por recurso con lectura y actualización local de facturas PDF,
  asignación de importes en tabla y filtros de materiales, maquinaria y RCDs.
- Comparación del coste previsto con lo facturado, global o acumulado por mes.
- Visor PDF integrado junto a la tabla, con navegación de páginas y zoom.
- Filtros de recursos en las bases de referencia, edición de descripciones
  y copia de precios básicos al descompuesto de la partida seleccionada.

### Cambiado
- Recursos filtrados con capítulo y unidad de obra a su izquierda.
- Reordenación de componentes con los botones de subir y bajar.
- Aplicación, compilación e instalador actualizados a 1.5.0.

## 1.4.0

### Añadido
- Herramienta Seguridad y salud con propuesta revisable por riesgos de las UO
  BCCA, matriz CSV incluida y capítulo generado en el presupuesto.
- Porcentaje orientativo configurable e informe PDF del capítulo con
  presupuesto y mediciones.

## 1.3.0

### Añadido
- Informe de mediciones, estilos editables ODT y cabecera de plantilla repetida.
- Buscador del proyecto, filtros de maquinaria, mano de obra y materiales,
  expansión de capítulos y partidas y duplicación con aviso de revisión.
- Incorporación de capítulos completos desde las bases de referencia.
- Divisa del proyecto configurable e informe de desviaciones en PDF u ODT.

### Cambiado
- Márgenes, alineaciones, cabeceras, firmas y separación de tablas revisados
  en los informes PDF y ODT.
- Cálculo de componentes porcentuales, redondeos e indirectos compartido entre
  el presupuesto y sus informes, con indirectos explícitos en el cuadro de precios 2.
- Información de la aplicación sin datos personales y con acceso al repositorio.
- Resumen y descripción de nuevas partidas, copias, RCDs y transporte en mayúsculas,
  sin alterar las bases originales ni los textos al abrir proyectos existentes.
- Aplicación, compilación e instalador actualizados a 1.3.0.

## 1.2.0

### Añadido
- Herramienta RCDs en primer lugar del menú Herramientas, con dos pasos:
  datos de cálculo editables y selección manual de residuos con evidencias.
- Catálogo de 57 filas y coeficientes de las hojas Resumen y Lista MAM del
  modelo ODS; no requiere el archivo original para ejecutarse.
- Presupuesto de gestión A1/A2/B1/B2/B3 y total en PDF u ODT con la plantilla
  elegida en configuración. Guardado de datos y selecciones en el proyecto.
- Copia revisable de mediciones de demolición a transporte a vertedero, con
  conversión explícita a m³, material/LER y procedencia por línea. Creación de
  partida si falta, sin inventar precios y con aviso persistente de revisión.
  Sincronización sin duplicados y cálculo de RCDs por los volúmenes revisados,
  conservando mediciones manuales y el volumen independiente de excavación.
- Incorporación del capítulo Gestión de residuos al guardar RCDs: Nivel I,
  tipologías Nivel II y complementos B1/B2/B3 con medición, precio e importe.
  Actualización sin duplicados, importes redondeados por partida y aplicación
  de costes indirectos según configuración, sin sustituir partidas manuales.

### Cambiado
- Cálculo revisado del modelo: mínimo Nivel II configurable (inicial 0,4 %),
  B1 manual e inclusión de todas las filas visibles en el total.
- Volumen y tarifa redondeados a dos decimales antes de calcular cada coste;
  B2 se calcula sobre importes redondeados y el total suma los importes visibles.
  Con los datos del ejemplo, el total revisado es 13.044,62 €, frente a
  13.044,63 € de la hoja original por su distinto redondeo del complemento B2.
- Metadatos de compilación e instalador actualizados a 1.2.0.

## No publicado

### Añadido
- Compilación y distribución de un instalador para Windows.
- Pantalla de inicio con la imagen de marca.
- Almacenamiento de preferencias e historial en `%APPDATA%` en la versión instalada.

### Cambiado
- Organización de las pruebas automatizadas bajo `tests/`.
- Documentación del arranque, la instalación y la compilación del instalador.
