# Manual de usuario

## Gestor de Proyectos (v1.0.0)

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

1. Añade una base desde **Archivo → Añadir base de referencia BCCA…**.
2. Localiza una partida en la referencia y añádela al presupuesto con la flecha
   hacia la izquierda o arrastrándola al proyecto.
3. Usa **+** para añadir capítulos y **×** para eliminar la selección.
4. Las flechas **↑** y **↓** cambian el orden.
5. Selecciona una partida para consultar sus mediciones, descripción y recursos.
6. Usa **↻** para editar los datos de la partida seleccionada.

La columna **Tipo** permite modificar la naturaleza de partidas y recursos.
Revisa los tipos porque intervienen en los listados de materiales y precios.
Al pasar el ratón por los botones se muestra su ayuda.

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
- Empresa contratista y representante.
- Gastos generales, beneficio industrial e IVA.
- Baja de adjudicación para certificaciones.

Los porcentajes deben estar entre 0 y 100. **Guardar** conserva los valores como
configuración inicial para nuevos proyectos y dentro del proyecto actual.
Los proyectos ya guardados mantienen su configuración propia.

Los gastos generales y el beneficio industrial se calculan sobre el presupuesto
de ejecución material (PEM). El IVA se aplica después de sumar ambos.
En certificaciones, se descuenta primero la baja de adjudicación y después se
aplica el IVA. La baja no altera el presupuesto base de licitación del informe
de presupuesto.

## 6. Guardar

Usa **Archivo → Guardar proyecto (JSON)…**. El JSON conserva partidas, mediciones,
precios editados, planificación, certificaciones y configuración de informes.

Los cambios de medición deben confirmarse antes de guardar. Al cerrar la app,
si quedan cambios pendientes, puedes guardarlos, descartarlos o cancelar el cierre.
El guardado no sobrescribe el BC3 original.

Si tras trasladar el proyecto falta su BC3 de origen, puede abrirse el JSON,
pero algunos informes de recursos necesitarán la base original o los precios
guardados. Conserva los archivos asociados junto al proyecto.

## 7. Generar presupuestos e informes

En el menú **Informes** de la ventana principal están:

| Opción | Contenido |
|---|---|
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
la impresión, la exportación del presupuesto y recursos a Excel y las mediciones
a CSV. La vista HTML es una salida adicional, no el modelo PDF maquetado.

Los informes no inventan precios ausentes. Si falta un recurso, carga su base de
referencia o completa los precios necesarios antes de volver a generarlo.

## 8. Planificar la obra

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

## 9. Registrar certificaciones

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

**Informe de desviaciones** abre una vista HTML adicional para comparar previsión,
certificado y justificación.

## 10. Resolver problemas frecuentes

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

## 11. Licencia y precauciones

En **Información → Licencia y responsabilidad…** puedes consultar el aviso MIT
y la limitación de responsabilidad.

Verifica profesionalmente todos los resultados antes de emplearlos en un
expediente o una decisión económica. Guarda copias de seguridad y protege los
datos de los proyectos; no los subas al repositorio de código.
