# Changelog

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
