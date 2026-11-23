"""Navegador de pendientes Writer, reutilizable en paquetes ODT y ODM."""
from xml.etree import ElementTree as ET

LIBRARY_NAME = "GestorPendientes"
SCRIPT_URI = (
    "vnd.sun.star.script:GestorPendientes.Main.ShowPending"
    "?language=Basic&location=document"
)
_LIBRARY_NS = "http://openoffice.org/2000/library"
_SCRIPT_NS = "http://openoffice.org/2000/script"

BASIC_SOURCE = r'''Option Explicit

Global gPendingDocument As Object
Global gPendingDialog As Object
Global gPendingMatches As Object
Global gPendingDocumentListener As Object
Global gPendingCloseListener As Object
Global gPendingListListener As Object
Global gPendingRefreshListener As Object
Global gPendingWindowListener As Object
Global gPendingOpen As Boolean
Global gPendingBusy As Boolean
Global gPendingListening As Boolean

Function FindPending(oDocument As Object) As Object
    Dim oSearch As Object
    oSearch = oDocument.createSearchDescriptor()
    oSearch.SearchRegularExpression = True
    oSearch.SearchCaseSensitive = False
    oSearch.SearchString = "\[PENDIENTE[^\]\r\n]*\]"
    FindPending = oDocument.findAll(oSearch)
End Function

Function PendingControl(oModel As Object, sType As String, sName As String, _
                        x As Long, y As Long, w As Long, h As Long) As Object
    Dim oControl As Object
    oControl = oModel.createInstance("com.sun.star.awt.UnoControl" & sType & "Model")
    oControl.PositionX = x
    oControl.PositionY = y
    oControl.Width = w
    oControl.Height = h
    oModel.insertByName(sName, oControl)
    PendingControl = oControl
End Function

Sub ShowPending()
    On Local Error GoTo Failed
    Dim oModel As Object, oControl As Object, oToolkit As Object
    If gPendingOpen Then
        If EqualUnoObjects(gPendingDocument, ThisComponent) Then
            RefreshPending
            gPendingDialog.setVisible(True)
            Exit Sub
        End If
        ClosePending
    End If
    If Not ThisComponent.supportsService("com.sun.star.text.TextDocument") And _
       Not ThisComponent.supportsService("com.sun.star.text.GlobalDocument") Then
        MsgBox "Abra un documento Writer o un maestro ODM.", 48, "Pendientes"
        Exit Sub
    End If
    gPendingDocument = ThisComponent
    oModel = CreateUnoService("com.sun.star.awt.UnoControlDialogModel")
    oModel.Title = "Pendientes del proyecto"
    oModel.Width = 380
    oModel.Height = 205
    oModel.Closeable = True
    oModel.Moveable = True
    oControl = PendingControl(oModel, "FixedText", "PendingCount", 8, 6, 364, 12)
    oControl = PendingControl(oModel, "ListBox", "PendingList", 8, 22, 364, 137)
    oControl.MultiSelection = False
    oControl = PendingControl(oModel, "FixedText", "PendingHelp", 8, 163, 364, 18)
    oControl.Label = "Seleccione un aviso para localizarlo. Sustituya el marcador completo."
    oControl.MultiLine = True
    oControl = PendingControl(oModel, "Button", "PendingRefresh", 8, 183, 166, 16)
    oControl.Label = "Actualizar vínculos y pendientes"
    gPendingDialog = CreateUnoService("com.sun.star.awt.UnoControlDialog")
    gPendingDialog.setModel(oModel)
    oToolkit = CreateUnoService("com.sun.star.awt.Toolkit")
    gPendingDialog.createPeer(oToolkit, gPendingDocument.CurrentController.Frame.ContainerWindow)
    gPendingListListener = CreateUnoListener("PendingList_", "com.sun.star.awt.XItemListener")
    gPendingDialog.getControl("PendingList").addItemListener(gPendingListListener)
    gPendingRefreshListener = CreateUnoListener("PendingRefresh_", "com.sun.star.awt.XActionListener")
    gPendingDialog.getControl("PendingRefresh").addActionListener(gPendingRefreshListener)
    gPendingWindowListener = CreateUnoListener("PendingWindow_", "com.sun.star.awt.XTopWindowListener")
    gPendingDialog.addTopWindowListener(gPendingWindowListener)
    gPendingDocumentListener = CreateUnoListener("PendingDocument_", "com.sun.star.util.XModifyListener")
    gPendingDocument.addModifyListener(gPendingDocumentListener)
    gPendingCloseListener = CreateUnoListener("PendingClosing_", "com.sun.star.util.XCloseListener")
    gPendingDocument.addCloseListener(gPendingCloseListener)
    gPendingListening = True
    gPendingOpen = True
    RefreshPending
    gPendingDialog.setVisible(True)
    Exit Sub
Failed:
    Dim sMessage As String
    sMessage = Error$
    If gPendingOpen Then ClosePending
    MsgBox "No se pudo abrir el navegador de pendientes: " & sMessage, 16, "Pendientes"
End Sub

Sub RefreshPending()
    If Not gPendingOpen Or gPendingBusy Then Exit Sub
    On Local Error GoTo Failed
    Dim n As Long, i As Long, selected As Long, labels() As String, sLabel As String
    gPendingBusy = True
    selected = gPendingDialog.getControl("PendingList").getSelectedItemPos()
    gPendingMatches = FindPending(gPendingDocument)
    n = gPendingMatches.getCount()
    If n > 0 Then
        ReDim labels(n - 1)
        For i = 0 To n - 1
            sLabel = gPendingMatches.getByIndex(i).getString()
            sLabel = Replace(Replace(Replace(sLabel, Chr(13), " "), Chr(10), " "), Chr(9), " ")
            labels(i) = CStr(i + 1) & ". " & sLabel
        Next i
        gPendingDialog.getControl("PendingList").Model.StringItemList = labels()
        If selected >= n Then selected = n - 1
        If selected >= 0 Then gPendingDialog.getControl("PendingList").selectItemPos(selected, True)
        gPendingDialog.getControl("PendingCount").Model.Label = "Pendientes detectados: " & CStr(n)
    Else
        gPendingDialog.getControl("PendingList").Model.StringItemList = Array()
        gPendingDialog.getControl("PendingCount").Model.Label = "No quedan pendientes."
    End If
    gPendingBusy = False
    Exit Sub
Failed:
    gPendingBusy = False
    MsgBox "No se pudieron revisar los pendientes: " & Error$, 16, "Pendientes"
End Sub

Sub GoToPending(n As Long)
    If Not gPendingOpen Or gPendingBusy Then Exit Sub
    If n < 0 Or n >= gPendingMatches.getCount() Then Exit Sub
    On Local Error GoTo Failed
    gPendingDocument.CurrentController.select(gPendingMatches.getByIndex(n))
    gPendingDocument.CurrentController.Frame.ContainerWindow.setFocus()
    Exit Sub
Failed:
    MsgBox "No se pudo localizar el pendiente. Actualice la lista: " & Error$, 16, "Pendientes"
End Sub

Sub UpdatePendingLinks()
    If Not gPendingOpen Or gPendingBusy Then Exit Sub
    On Local Error GoTo Failed
    gPendingBusy = True
    gPendingDocument.updateLinks()
    gPendingBusy = False
    RefreshPending
    Exit Sub
Failed:
    gPendingBusy = False
    MsgBox "No se pudieron actualizar los documentos vinculados: " & Error$, 16, "Pendientes"
End Sub

Sub ClosePending()
    If Not gPendingOpen Then Exit Sub
    gPendingOpen = False
    If gPendingListening Then
        gPendingListening = False
        gPendingDocument.removeModifyListener(gPendingDocumentListener)
        gPendingDocument.removeCloseListener(gPendingCloseListener)
    End If
    gPendingDialog.dispose()
    gPendingBusy = False
End Sub

Sub PendingDocument_modified(oEvent)
    RefreshPending
End Sub

Sub PendingDocument_disposing(oEvent)
    gPendingListening = False
    ClosePending
End Sub

Sub PendingClosing_queryClosing(oEvent, bOwnership)
End Sub

Sub PendingClosing_notifyClosing(oEvent)
    ClosePending
End Sub

Sub PendingClosing_disposing(oEvent)
    gPendingListening = False
    ClosePending
End Sub

Sub PendingList_itemStateChanged(oEvent)
    If gPendingBusy Or Not gPendingOpen Then Exit Sub
    GoToPending(gPendingDialog.getControl("PendingList").getSelectedItemPos())
End Sub

Sub PendingList_disposing(oEvent)
End Sub

Sub PendingRefresh_actionPerformed(oEvent)
    UpdatePendingLinks
End Sub

Sub PendingRefresh_disposing(oEvent)
End Sub

Sub PendingWindow_windowClosing(oEvent)
    ClosePending
End Sub

Sub PendingWindow_windowOpened(oEvent)
End Sub

Sub PendingWindow_windowClosed(oEvent)
End Sub

Sub PendingWindow_windowMinimized(oEvent)
End Sub

Sub PendingWindow_windowNormalized(oEvent)
End Sub

Sub PendingWindow_windowActivated(oEvent)
End Sub

Sub PendingWindow_windowDeactivated(oEvent)
End Sub

Sub PendingWindow_disposing(oEvent)
End Sub
'''


def macro_entries(
    registry: bytes | None = None, library: bytes | None = None,
) -> dict[str, bytes]:
    """Empaqueta Basic sin perder las otras bibliotecas que tenga la plantilla."""
    libraries = ET.fromstring(registry) if registry is not None else ET.Element(
        f"{{{_LIBRARY_NS}}}libraries",
    )
    registered = next(
        (node for node in libraries if node.get(f"{{{_LIBRARY_NS}}}name") == LIBRARY_NAME),
        None,
    )
    if registered is not None and registered.get(f"{{{_LIBRARY_NS}}}link") == "true":
        raise ValueError(f"La plantilla ya vincula una biblioteca externa llamada {LIBRARY_NAME}.")
    if registered is None:
        ET.SubElement(libraries, f"{{{_LIBRARY_NS}}}library", {
            f"{{{_LIBRARY_NS}}}name": LIBRARY_NAME, f"{{{_LIBRARY_NS}}}link": "false",
        })
    definition = ET.fromstring(library) if library is not None else ET.Element(
        f"{{{_LIBRARY_NS}}}library",
    )
    definition.attrib.update({
        f"{{{_LIBRARY_NS}}}name": LIBRARY_NAME,
        f"{{{_LIBRARY_NS}}}readonly": "false",
        f"{{{_LIBRARY_NS}}}passwordprotected": "false",
    })
    if not any(node.get(f"{{{_LIBRARY_NS}}}name") == "Main" for node in definition):
        ET.SubElement(definition, f"{{{_LIBRARY_NS}}}element", {f"{{{_LIBRARY_NS}}}name": "Main"})
    module = ET.Element(f"{{{_SCRIPT_NS}}}module", {
        f"{{{_SCRIPT_NS}}}name": "Main", f"{{{_SCRIPT_NS}}}language": "StarBasic",
    })
    module.text = BASIC_SOURCE
    return {
        path: ET.tostring(root, encoding="utf-8", xml_declaration=True)
        for path, root in (
            ("Basic/script-lc.xml", libraries),
            (f"Basic/{LIBRARY_NAME}/script-lb.xml", definition),
            (f"Basic/{LIBRARY_NAME}/Main.xml", module),
        )
    }
