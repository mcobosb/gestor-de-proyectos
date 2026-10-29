"""Traducciones de la interfaz y utilidades para aplicar el idioma seleccionado."""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import filedialog as _tk_filedialog
from tkinter import messagebox as _tk_messagebox
from tkinter import simpledialog as _tk_simpledialog
from tkinter import ttk


LANGUAGES = {
    "es": "Español",
    "en": "English",
    "fr": "Français",
}

_TEXT = {
    "Archivo": ("File", "Fichier"),
    "Nuevo proyecto": ("New project", "Nouveau projet"),
    "Importar presupuesto BC3…": ("Import BC3 budget…", "Importer un budget BC3…"),
    "Añadir base de referencia BCCA…": ("Add BCCA reference database…", "Ajouter une base de référence BCCA…"),
    "Quitar base de referencia…": ("Remove reference database…", "Retirer la base de référence…"),
    "Abrir proyecto (JSON)…": ("Open project (JSON)…", "Ouvrir un projet (JSON)…"),
    "Guardar proyecto (JSON)…": ("Save project (JSON)…", "Enregistrer le projet (JSON)…"),
    "Configuración…": ("Settings…", "Paramètres…"),
    "Costes Indirectos (%)": ("Indirect costs (%)", "Coûts indirects (%)"),
    "Precio directo": ("Direct price", "Prix direct"),
    "Imprimir y exportar": ("Print and export", "Imprimer et exporter"),
    "Vista previa del presupuesto…": ("Budget preview…", "Aperçu du budget…"),
    "Imprimir presupuesto": ("Print budget", "Imprimer le budget"),
    "Exportar presupuesto a ODS…": ("Export budget to ODS…", "Exporter le budget au format ODS…"),
    "Exportar mediciones a CSV…": ("Export measurements to CSV…", "Exporter les métrés en CSV…"),
    "Informes": ("Reports", "Rapports"),
    "Exportar recursos a ODS…": ("Export resources to ODS…", "Exporter les ressources au format ODS…"),
    "Exportar descompuestos a ODS…": ("Export breakdowns to ODS…", "Exporter les décompositions au format ODS…"),
    "Herramientas": ("Tools", "Outils"),
    "RCDs": ("Construction waste", "Déchets de chantier"),
    "Datos de cálculo": ("Calculation inputs", "Données de calcul"),
    "Llevar mediciones de demolición a transporte a vertedero": (
        "Copy demolition measurements to landfill transport",
        "Copier les métrés de démolition vers le transport en décharge",
    ),
    "Demoliciones a transporte a vertedero": (
        "Demolition to landfill transport", "Démolitions vers le transport en décharge",
    ),
    "Partida de destino": ("Destination item", "Poste de destination"),
    "Partida / medición de origen": ("Source item / measurement", "Poste / métré d'origine"),
    "Factor a m³": ("Conversion factor to m³", "Facteur de conversion en m³"),
    "Material / LER": ("Material / waste code", "Matériau / code déchet"),
    "Copiar mediciones y usar volúmenes en RCDs": (
        "Copy measurements and use volumes for waste",
        "Copier les métrés et utiliser les volumes pour les déchets",
    ),
    "Aceptar y revisar residuos": ("Accept and review waste", "Valider et vérifier les déchets"),
    "Generar informe RCDs": ("Generate waste report", "Générer le rapport de déchets"),
    "RCDs y capítulo Gestión de residuos guardados con el proyecto.": (
        "Waste data and waste management chapter saved with the project.",
        "Déchets et chapitre de gestion des déchets enregistrés avec le projet.",
    ),
    "LER (modelo ODS)": ("Waste code (ODS model)", "Code déchet (modèle ODS)"),
    "Residuo": ("Waste", "Déchet"),
    "Grupo": ("Group", "Groupe"),
    "Detectado en partidas / revisión": ("Detected in items / review", "Détecté dans les postes / révision"),
    "Tipología RCDs": ("Waste category", "Catégorie de déchets"),
    "Estimación volumen (m³)": ("Estimated volume (m³)", "Volume estimé (m³)"),
    "Precio gestión (€/m³)": ("Management price (€/m³)", "Prix de gestion (€/m³)"),
    "% presupuesto de obra": ("% of project budget", "% du budget du projet"),
    "Guardar los cambios RCDs en el proyecto (Ctrl+S).": (
        "Save waste changes in the project (Ctrl+S).",
        "Enregistrer les modifications des déchets dans le projet (Ctrl+S).",
    ),
    "Planificación": ("Planning", "Planification"),
    "Certificaciones": ("Certifications", "Situations de travaux"),
    "Facturación": ("Invoicing", "Facturation"),
    "Factura": ("Invoice", "Facture"),
    "Nombre del fichero (clic para abrir)": ("File name (click to open)", "Nom du fichier (cliquer pour ouvrir)"),
    "Guardar los cambios de facturación en el proyecto (Ctrl+S).": (
        "Save invoicing changes in the project (Ctrl+S).",
        "Enregistrer les modifications de facturation dans le projet (Ctrl+S).",
    ),
    "Carpeta de facturas PDF": ("PDF invoice folder", "Dossier des factures PDF"),
    "Continuar": ("Continue", "Continuer"),
    "Carpeta…": ("Folder…", "Dossier…"),
    "Leer PDF": ("Read PDFs", "Lire les PDF"),
    "Añadir factura": ("Add invoice", "Ajouter une facture"),
    "Editar factura": ("Edit invoice", "Modifier la facture"),
    "Eliminar factura": ("Delete invoice", "Supprimer la facture"),
    "Revisar líneas": ("Review lines", "Vérifier les lignes"),
    "Añadir coste": ("Add cost", "Ajouter un coût"),
    "Editar coste": ("Edit cost", "Modifier le coût"),
    "Eliminar coste": ("Delete cost", "Supprimer le coût"),
    "Previsto": ("Budgeted", "Prévu"),
    "Facturado": ("Invoiced", "Facturé"),
    "Restante": ("Remaining", "Restant"),
    "Nombre de columna": ("Column name", "Nom de colonne"),
    "Obra": ("Project", "Chantier"),
    "Expediente": ("Reference", "Dossier"),
    "Archivo PDF": ("PDF file", "Fichier PDF"),
    "Importe leído/declarado": ("Read/declared amount", "Montant lu/déclaré"),
    "Coste previsto (€)": ("Budgeted cost (€)", "Coût prévu (€)"),
    "Revisar líneas de factura": ("Review invoice lines", "Vérifier les lignes de facture"),
    "Partida leída": ("Read item", "Poste lu"),
    "Asignación": ("Allocation", "Affectation"),
    "Asignar al coste seleccionado": ("Allocate to selected cost", "Affecter au coût sélectionné"),
    "Desasignar": ("Unassign", "Désaffecter"),
    "Añadir línea": ("Add line", "Ajouter une ligne"),
    "Editar línea": ("Edit line", "Modifier la ligne"),
    "Eliminar línea": ("Delete line", "Supprimer la ligne"),
    "Editar línea de factura": ("Edit invoice line", "Modifier la ligne de facture"),
    "Información": ("About", "À propos"),
    "Licencia y responsabilidad…": ("License and disclaimer…", "Licence et responsabilité…"),
    "Añadir un capítulo al presupuesto.": ("Add a chapter to the budget.", "Ajouter un chapitre au budget."),
    "Eliminar la partida o el capítulo seleccionado.": ("Delete the selected item or chapter.", "Supprimer le poste ou le chapitre sélectionné."),
    "Añadir al presupuesto la partida seleccionada en la base de referencia.": ("Add the selected reference item to the budget.", "Ajouter au budget le poste sélectionné dans la base de référence."),
    "Subir la partida o el capítulo seleccionado.": ("Move the selected item or chapter up.", "Monter le poste ou le chapitre sélectionné."),
    "Bajar la partida o el capítulo seleccionado.": ("Move the selected item or chapter down.", "Descendre le poste ou le chapitre sélectionné."),
    "Mostrar u ocultar el panel de la base de referencia.": ("Show or hide the reference database panel.", "Afficher ou masquer le panneau de la base de référence."),
    "Editar los datos de la partida seleccionada.": ("Edit the selected item's details.", "Modifier les données du poste sélectionné."),
    "Proyecto": ("Project", "Projet"),
    "Base de referencia": ("Reference database", "Base de référence"),
    "Capítulo": ("Chapter", "Chapitre"),
    "Código": ("Code", "Code"),
    "Unidad": ("Unit", "Unité"),
    "Resumen": ("Summary", "Résumé"),
    "Medición": ("Measurement", "Métré"),
    "Precio": ("Price", "Prix"),
    "Rendimiento": ("Performance", "Rendement"),
    "Tipo": ("Type", "Type"),
    "Texto": ("Text", "Texte"),
    "Importe": ("Amount", "Montant"),
    "Mediciones": ("Measurements", "Métrés"),
    "Texto de la partida": ("Item description", "Description du poste"),
    "Costes y rendimientos": ("Costs and performance", "Coûts et rendements"),
    "Añadir medición": ("Add measurement", "Ajouter un métré"),
    "Eliminar medición": ("Delete measurement", "Supprimer le métré"),
    "Pulsa sobre una celda para editarla.": ("Click a cell to edit it.", "Cliquez sur une cellule pour la modifier."),
    "Detalle de la selección": ("Selection details", "Détails de la sélection"),
    "Precio unit.": ("Unit price", "Prix unitaire"),
    "Cantidad": ("Quantity", "Quantité"),
    "Coste": ("Cost", "Coût"),
    "Doble clic en cantidad o precio unitario para modificar el coste. El rendimiento se edita con el botón ↻.": (
        "Double-click quantity or unit price to edit the cost. Performance is edited with the ↻ button.",
        "Double-cliquez sur la quantité ou le prix unitaire pour modifier le coût. Le rendement se modifie avec le bouton ↻.",
    ),
    "Pulsa en la columna Tipo para cambiar la naturaleza de una partida o recurso.": (
        "Click the Type column to change the kind of item or resource.",
        "Cliquez sur la colonne Type pour modifier la nature d'un poste ou d'une ressource.",
    ),
    "Capítulo / partida": ("Chapter / item", "Chapitre / poste"),
    "Ud / cantidad": ("Unit / quantity", "Unité / quantité"),
    "Rend. (h)": ("Performance (h)", "Rendement (h)"),
    "Total del presupuesto: 0,00 €": ("Budget total: €0.00", "Total du budget : 0,00 €"),
    "Quitar": ("Remove", "Retirer"),
    "Selecciona las bases a quitar:": ("Select the databases to remove:", "Sélectionnez les bases à retirer :"),
    "Selecciona un presupuesto .bc3": ("Select a .bc3 budget", "Sélectionnez un budget .bc3"),
    "Selecciona una o varias bases de precios .bc3": ("Select one or more .bc3 price databases", "Sélectionnez une ou plusieurs bases de prix .bc3"),
    "Abrir proyecto guardado": ("Open saved project", "Ouvrir un projet enregistré"),
    "Guardar proyecto": ("Save project", "Enregistrer le projet"),
    "Exportar presupuesto": ("Export budget", "Exporter le budget"),
    "Exportar mediciones": ("Export measurements", "Exporter les métrés"),
    "Descripción": ("Description", "Description"),
    "Configuración": ("Settings", "Paramètres"),
    "Preferencias de idioma": ("Language preferences", "Préférences linguistiques"),
    "Presupuesto vacío": ("Empty budget", "Budget vide"),
    "Sin bases": ("No databases", "Aucune base"),
    "Sin selección": ("No selection", "Aucune sélection"),
    "Icono de la aplicación": ("Application icon", "Icône de l'application"),
    "No se pudo abrir el proyecto": ("Could not open project", "Impossible d'ouvrir le projet"),
    "No se pudo guardar el proyecto": ("Could not save project", "Impossible d'enregistrer le projet"),
    "No se pudo generar el informe": ("Could not generate report", "Impossible de générer le rapport"),
    "Planificación guardada no válida": ("Saved planning is invalid", "Le planning enregistré n'est pas valide"),
    "Festivo": ("Holiday", "Jour férié"),
    "Parámetros": ("Parameters", "Paramètres"),
    "Valor no válido": ("Invalid value", "Valeur non valide"),
    "Sin partida seleccionada.": ("No item selected.", "Aucun poste sélectionné."),
    "Recursos": ("Resources", "Ressources"),
    "Descompuestos": ("Breakdowns", "Décompositions"),
    "Cuadro de precios 1 y 2 por capitulos": ("Price list 1 and 2 by chapters", "Bordereau de prix 1 et 2 par chapitres"),
    "Materiales": ("Materials", "Matériaux"),
    "Precios Auxiliares": ("Auxiliary prices", "Prix auxiliaires"),
    "Presupuesto mediciones": ("Measured budget", "Budget quantitatif"),
    "Presupuesto resumen": ("Budget summary", "Récapitulatif du budget"),
    "Resumen certificaciones": ("Certification summary", "Récapitulatif des situations"),
    "Certificaciones con mediciones": ("Certifications with measurements", "Situations avec métrés"),
    "Aplicación": ("Application", "Application"),
    "Configuración de informes": ("Report settings", "Paramètres des rapports"),
    "Autoría / nombre": ("Author / name", "Auteur / nom"),
    "Cargo / titulación": ("Role / title", "Fonction / titre"),
    "Entidad / servicio": ("Organization / department", "Organisme / service"),
    "Localidad": ("Location", "Localité"),
    "Texto de firma / fecha": ("Signature / date text", "Texte de signature / date"),
    "Empresa contratista": ("Contractor", "Entreprise titulaire"),
    "Representante de la empresa": ("Company representative", "Représentant de l'entreprise"),
    "Gastos generales (%)": ("Overheads (%)", "Frais généraux (%)"),
    "Beneficio industrial (%)": ("Industrial profit (%)", "Bénéfice industriel (%)"),
    "IVA (%)": ("VAT (%)", "TVA (%)"),
    "Baja de adjudicación (%)": ("Tender discount (%)", "Rabais d'adjudication (%)"),
    "Plantilla de cabecera y pie (ODT)": (
        "Header and footer template (ODT)",
        "Modèle d'en-tête et de pied de page (ODT)",
    ),
    "Examinar…": ("Browse…", "Parcourir…"),
    "Selecciona la plantilla de informes": (
        "Select the report template",
        "Sélectionner le modèle des rapports",
    ),
    "Formato de informes": ("Report format", "Format des rapports"),
    "La plantilla aporta la cabecera y el pie de todos los informes de\n"
    "presupuesto y certificaciones. Déjala vacía para usar la incluida.\n"
    "GG y BI se aplican al PEM. En certificaciones, se deduce la baja\nantes del IVA. La baja no modifica el presupuesto de licitación.\n"
    "Guardar conserva estos datos para este proyecto y como valores\n"
    "iniciales de nuevos proyectos. Los proyectos guardados mantienen su configuración.": (
        "The template provides the header and footer for all budget and certification\n"
        "reports. Leave it empty to use the included template.\n"
        "Overheads and industrial profit apply to the construction budget. For certifications, "
        "the discount is deducted before VAT. The discount does not change the tender budget.\n"
        "Saving keeps these values for this project and as defaults for new projects. "
        "Saved projects retain their settings.",
        "Le modèle fournit l'en-tête et le pied de page de tous les rapports de budget et de\n"
        "situation. Laissez le champ vide pour utiliser le modèle inclus.\n"
        "Les frais généraux et le bénéfice industriel s'appliquent au montant des travaux. "
        "Pour les situations, le rabais est déduit avant la TVA. Le rabais ne modifie pas le budget d'appel d'offres.\n"
        "L'enregistrement conserve ces valeurs pour ce projet et comme valeurs par défaut des nouveaux projets. "
        "Les projets enregistrés gardent leurs paramètres.",
    ),
    "Cancelar": ("Cancel", "Annuler"),
    "Guardar": ("Save", "Enregistrer"),
    "Guardar informes": ("Save report settings", "Enregistrer les paramètres des rapports"),
    "Idioma": ("Language", "Langue"),
    "Español": ("Spanish", "Espagnol"),
    "El idioma se guarda como preferencia de la aplicación y se aplica inmediatamente.": (
        "The language is saved as an application preference and applied immediately.",
        "La langue est enregistrée comme préférence de l'application et appliquée immédiatement.",
    ),
    "Guardar idioma": ("Save language", "Enregistrer la langue"),
    "No se pudo guardar el idioma": ("Could not save language", "Impossible d'enregistrer la langue"),
    "Idioma cambiado": ("Language changed", "Langue modifiée"),
    "1. Parámetros": ("1. Parameters", "1. Paramètres"),
    "2. Secuencia y plan de trabajo": ("2. Sequence and work plan", "2. Séquence et planning des travaux"),
    "Festivos (no laborables)": ("Holidays (non-working days)", "Jours fériés (jours non ouvrés)"),
    "Fecha": ("Date", "Date"),
    "Fecha:": ("Date:", "Date :"),
    "Descripción:": ("Description:", "Description :"),
    "Fecha de inicio de obra (dd/mm/aaaa)": ("Project start date (dd/mm/yyyy)", "Date de début du chantier (jj/mm/aaaa)"),
    "Horas por jornada": ("Working hours per day", "Heures par jour"),
    "Escala del Gantt (1 = día, 7 = semana)": ("Gantt scale (1 = day, 7 = week)", "Échelle du Gantt (1 = jour, 7 = semaine)"),
    "Detalle de la figura": ("Chart detail", "Détail du graphique"),
    "Resumen": ("Summary", "Résumé"),
    "Completo": ("Complete", "Complet"),
    "Rend. (h/ud)": ("Performance (h/unit)", "Rendement (h/unité)"),
    "Equipos": ("Teams", "Équipes"),
    "Duración": ("Duration", "Durée"),
    "Dur. manual": ("Manual duration", "Durée manuelle"),
    "Toda la obra": ("Entire project", "Tout le chantier"),
    "Pred.": ("Pred.", "Préd."),
    "Desfase": ("Lag", "Décalage"),
    "Inicio": ("Start", "Début"),
    "Fin": ("Finish", "Fin"),
    "Actividad": ("Activity", "Activité"),
    "Añadir festivo": ("Add holiday", "Ajouter un jour férié"),
    "Eliminar festivo": ("Delete holiday", "Supprimer le jour férié"),
    "Aplicar parámetros y continuar  →": ("Apply parameters and continue  →", "Appliquer les paramètres et continuer  →"),
    "Secuenciar una tras otra": ("Sequence one after another", "Enchaîner les activités"),
    "Recalcular fechas": ("Recalculate dates", "Recalculer les dates"),
    "Dibujar plan de trabajo": ("Draw work plan", "Dessiner le planning des travaux"),
    "Doble clic sobre equipos, duración, continuidad, predecesora, tipo o desfase para editarlos.": (
        "Double-click teams, duration, continuity, predecessor, type or lag to edit.",
        "Double-cliquez sur les équipes, la durée, la continuité, le prédécesseur, le type ou le décalage pour modifier.",
    ),
    "Nº equipos": ("No. of teams", "Nº d'équipes"),
    "Duración manual": ("Manual duration", "Durée manuelle"),
    "Toda la obra (S/N)": ("Entire project (Y/N)", "Tout le chantier (O/N)"),
    "Predecesora (ID)": ("Predecessor (ID)", "Prédécesseur (ID)"),
    "Tipo (FC/CC/FF/CF)": ("Type (FC/CC/FF/CF)", "Type (FC/CC/FF/CF)"),
    "Desfase (días)": ("Lag (days)", "Décalage (jours)"),
    "Copiar plan seleccionado a su certificación": ("Copy selected plan to its certification", "Copier le planning sélectionné dans sa situation"),
    "Informe de desviaciones": ("Deviation report", "Rapport des écarts"),
    "Selecciona una cabecera «Plan» para copiar esa columna.": ("Select a “Plan” heading to copy that column.", "Sélectionnez un en-tête « Planning » pour copier cette colonne."),
    "Desviación y justificación": ("Deviation and justification", "Écart et justification"),
    "Selecciona una celda de certificación.": ("Select a certification cell.", "Sélectionnez une cellule de situation."),
    "Justificación:": ("Justification:", "Justification :"),
    "Plan": ("Plan", "Planning"),
    "Certificación": ("Certification", "Situation"),
    "TOTAL": ("TOTAL", "TOTAL"),
    "Plan de trabajo": ("Work plan", "Planning des travaux"),
    "No hay actividades que dibujar.": ("There are no activities to draw.", "Aucune activité à dessiner."),
    "Abrir PNG": ("Open PNG", "Ouvrir le PNG"),
    "Abrir PDF": ("Open PDF", "Ouvrir le PDF"),
    "Guardar como…": ("Save as…", "Enregistrer sous…"),
    "Cerrar": ("Close", "Fermer"),
    "Guardar plan de trabajo": ("Save work plan", "Enregistrer le planning des travaux"),
}

_LANGUAGE = "es"
_PREFIXES = {
    "Proyecto: ": ("Project: ", "Projet : "),
    "Base de referencia: ": ("Reference database: ", "Base de référence : "),
    "Bases de referencia: ": ("Reference databases: ", "Bases de référence : "),
    "Total del presupuesto: ": ("Budget total: ", "Total du budget : "),
    "Informe guardado: ": ("Report saved: ", "Rapport enregistré : "),
    "Proyecto abierto: ": ("Project opened: ", "Projet ouvert : "),
    "BCCA cargado: ": ("BCCA loaded: ", "BCCA chargée : "),
    "Presupuesto cargado: ": ("Budget loaded: ", "Budget chargé : "),
    "Quitadas ": ("Removed ", "Retirées "),
    "Actividades: ": ("Activities: ", "Activités : "),
    "Plan de trabajo guardado en ": ("Work plan saved to ", "Planning enregistré dans "),
    "Figura generada en:\n": ("Chart generated at:\n", "Graphique généré dans :\n"),
    "Plan de trabajo generado: ": ("Work plan generated: ", "Planning généré : "),
    "No se pudieron leer las preferencias; se usará español:\n": (
        "Could not read preferences; Spanish will be used:\n",
        "Impossible de lire les préférences ; l'espagnol sera utilisé :\n",
    ),
    "No se pudo leer la configuración de informes:\n": (
        "Could not read report settings:\n",
        "Impossible de lire les paramètres des rapports :\n",
    ),
    "No se pudo guardar la configuración:\n": (
        "Could not save settings:\n",
        "Impossible d'enregistrer les paramètres :\n",
    ),
}
_REVERSE = {
    translated: source
    for source, translations in _TEXT.items()
    for translated in translations
}


def set_language(language: str) -> None:
    """Activa el idioma indicado, rechazando códigos no admitidos."""
    global _LANGUAGE
    if language not in LANGUAGES:
        raise ValueError(f"Idioma no admitido: {language}")
    _LANGUAGE = language


def get_language() -> str:
    """Devuelve el código del idioma activo."""
    return _LANGUAGE


def translate(text: str, language: str | None = None) -> str:
    """Traduce una etiqueta conocida desde o hacia cualquiera de los idiomas."""
    target = language or _LANGUAGE
    source = _REVERSE.get(text, text)
    activity_summary = re.fullmatch(
        r"Actividades: (\d+)  ·  Plazo: (\d+) días laborables", source
    ) or re.fullmatch(r"Activities: (\d+)  ·  Duration: (\d+) working days", source)
    if activity_summary is None:
        activity_summary = re.fullmatch(
            r"Activités : (\d+)  ·  Durée : (\d+) jours ouvrés", source
        )
    if activity_summary:
        count, duration = activity_summary.groups()
        if target == "es":
            return f"Actividades: {count}  ·  Plazo: {duration} días laborables"
        if target == "en":
            return f"Activities: {count}  ·  Duration: {duration} working days"
        if target == "fr":
            return f"Activités : {count}  ·  Durée : {duration} jours ouvrés"
    certification_column = (
        re.fullmatch(r"Certificación (\d+)", source)
        or re.fullmatch(r"Certification (\d+)", source)
        or re.fullmatch(r"Situation (\d+)", source)
    )
    if certification_column:
        number = certification_column.group(1)
        if target == "es":
            return f"Certificación {number}"
        if target == "en":
            return f"Certification {number}"
        if target == "fr":
            return f"Situation {number}"
    plan_column = re.fullmatch(r"(?:Plan|Planning) (.+)", source)
    if plan_column and target == "es":
        return f"Plan {plan_column.group(1)}"
    if plan_column and target != "es":
        return f"{'Plan' if target == 'en' else 'Planning'} {plan_column.group(1)}"
    for source_prefix, translations in _PREFIXES.items():
        if source.startswith(source_prefix):
            suffix = source[len(source_prefix):]
            if target == "es":
                return source
            return translations[0 if target == "en" else 1] + suffix
        for translated_prefix in translations:
            if text.startswith(translated_prefix):
                suffix = text[len(translated_prefix):]
                if target == "es":
                    return source_prefix + suffix
                return translations[0 if target == "en" else 1] + suffix
    if target == "es":
        return source
    translations = _TEXT.get(source)
    if translations is None:
        return text
    return translations[0 if target == "en" else 1]


def localize_widget_tree(root: tk.Misc) -> None:
    """Traduce las etiquetas estáticas de widgets, menús, pestañas y tablas."""
    for widget in _walk_widgets(root):
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            widget.title(translate(widget.title()))
        if isinstance(widget, tk.Menu):
            end = widget.index("end")
            if end is not None:
                for index in range(end + 1):
                    if widget.type(index) in {"command", "cascade", "checkbutton", "radiobutton"}:
                        label = widget.entrycget(index, "label")
                        widget.entryconfigure(index, label=translate(label))
        elif isinstance(widget, ttk.Notebook):
            for tab in widget.tabs():
                widget.tab(tab, text=translate(widget.tab(tab, "text")))
        elif isinstance(widget, ttk.Treeview):
            for column in widget["columns"]:
                widget.heading(column, text=translate(widget.heading(column, "text")))
        elif isinstance(widget, ttk.Combobox):
            values = tuple(widget.cget("values"))
            if not all(value in LANGUAGES.values() for value in values):
                widget.configure(values=tuple(translate(value) for value in values))
        if "text" in widget.keys():
            widget.configure(text=translate(str(widget.cget("text"))))
        if (
            widget.winfo_class() in {"TLabel", "Label"}
            and "textvariable" in widget.keys()
            and widget.cget("textvariable")
        ):
            variable_name = widget.cget("textvariable")
            if widget.tk.call("info", "exists", variable_name):
                value = str(widget.tk.globalgetvar(variable_name))
                translated = translate(value)
                if translated != value:
                    widget.tk.globalsetvar(variable_name, translated)
        if "values" in widget.keys() and not isinstance(widget, ttk.Combobox):
            values = widget.cget("values")
            if values:
                widget.configure(values=tuple(translate(value) for value in values))


def _walk_widgets(root: tk.Misc):
    """Recorre todos los widgets descendientes de una ventana Tk."""
    yield root
    for child in root.winfo_children():
        yield from _walk_widgets(child)


class _LocalizedMessagebox:
    """Aplica las traducciones conocidas a los diálogos estándar de Tk."""

    def __getattr__(self, name: str):
        """Devuelve el diálogo solicitado con sus textos traducidos."""
        method = getattr(_tk_messagebox, name)
        if name not in {"showinfo", "showwarning", "showerror", "askyesno", "askokcancel", "askyesnocancel"}:
            return method

        def show(*args, **kwargs):
            """Muestra un diálogo estándar con título y mensaje localizados."""
            if len(args) >= 2:
                localized_args = (translate(str(args[0])), translate(str(args[1])), *args[2:])
                return method(*localized_args, **kwargs)
            if "title" in kwargs:
                kwargs["title"] = translate(str(kwargs["title"]))
            if "message" in kwargs:
                kwargs["message"] = translate(str(kwargs["message"]))
            return method(*args, **kwargs)

        return show


messagebox = _LocalizedMessagebox()


class _LocalizedNativeDialog:
    """Localiza títulos y etiquetas de los diálogos nativos de Tk."""

    def __init__(self, module, *, translate_prompt: bool = False):
        """Asocia un módulo de diálogos y configura si se traduce su indicación."""
        self.module = module
        self.translate_prompt = translate_prompt

    def __getattr__(self, name: str):
        """Devuelve la función del diálogo con sus textos localizados."""
        method = getattr(self.module, name)

        def show(*args, **kwargs):
            """Muestra el diálogo con título, filtros e indicaciones traducidos."""
            if len(args) >= 1:
                localized_args = [translate(str(args[0])), *args[1:]]
                if self.translate_prompt and len(args) >= 2:
                    localized_args[1] = translate(str(args[1]))
                args = tuple(localized_args)
            if "title" in kwargs:
                kwargs["title"] = translate(str(kwargs["title"]))
            if self.translate_prompt and "prompt" in kwargs:
                kwargs["prompt"] = translate(str(kwargs["prompt"]))
            if "filetypes" in kwargs:
                kwargs["filetypes"] = tuple(
                    (translate(str(label)), patterns)
                    for label, patterns in kwargs["filetypes"]
                )
            return method(*args, **kwargs)

        return show


filedialog = _LocalizedNativeDialog(_tk_filedialog)
simpledialog = _LocalizedNativeDialog(_tk_simpledialog, translate_prompt=True)
