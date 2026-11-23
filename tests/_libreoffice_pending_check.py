"""Prueba UNO ejecutada exclusivamente con el Python incluido en LibreOffice."""
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import uno
import unohelper
from com.sun.star.lang import XEventListener


class DisposalListener(unohelper.Base, XEventListener):
    """Registra el cierre real del diálogo, no solo el estado global Basic."""

    def __init__(self):
        """Inicia el indicador del evento de liberación."""
        self.disposed = False

    def disposing(self, event):
        """Recibe el evento UNO al liberar la ventana."""
        self.disposed = True


def property_value(name, value):
    """Construye una propiedad UNO sin depender de paquetes externos."""
    result = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    result.Name = name
    result.Value = value
    return result


def check(target):
    """Comprueba búsquedas, navegación, eventos y actualización de vínculos."""
    pipe = "pending_" + uuid.uuid4().hex
    program = Path(sys.executable).parent
    process = subprocess.Popen([
        str(program / "soffice.com"),
        f"-env:UserInstallation={(target.parent / 'uno-profile').as_uri()}",
        "--headless", "--norestore", "--nodefault", "--nofirststartwizard",
        f"--pidfile={target.parent / 'soffice.pid'}",
        f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    desktop = document = first = None
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        no_connection = uno.getClass("com.sun.star.connection.NoConnectException")
        deadline = time.monotonic() + 30
        while True:
            try:
                context = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except no_connection:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
        print("UNO conectado; abriendo maestro.", flush=True)
        document = desktop.loadComponentFromURL(target.as_uri(), "_blank", 0, (
            property_value("Hidden", True),
            property_value("MacroExecutionMode", 4),
            property_value("UpdateDocMode", 0),
        ))
        libraries = document.BasicLibraries
        print("Maestro abierto; cargando Basic.", flush=True)
        libraries.loadLibrary("GestorPendientes")
        library = libraries.getByName("GestorPendientes")
        source = library.getByName("Main")
        # Captura únicamente las notificaciones modales para que un fallo no bloquee el test.
        source = source.replace("Option Explicit", "Option Explicit\nGlobal gTestError As String", 1)
        source = re.sub(r'(?m)^(\s*)MsgBox (.*), (?:16|48), "Pendientes"$',
                        r"\1gTestError = \2", source)
        library.replaceByName("Main", source)
        library.insertByName("Checks", '''Option Explicit
Function State()
    Dim result(2)
    result(0) = gTestError
    result(1) = gPendingOpen
    result(2) = -1
    If gPendingOpen Then result(2) = gPendingDialog.getControl("PendingList").getItemCount()
    State = result()
End Function
Function Panel()
    Panel = gPendingDialog
End Function
''')
        provider = document.getScriptProvider()

        def invoke(method, *args):
            """Ejecuta el mismo punto de entrada que el enlace del ODM."""
            print("Macro:", method, flush=True)
            uri = f"vnd.sun.star.script:GestorPendientes.{method}?language=Basic&location=document"
            return provider.getScript(uri).invoke(args, (), ())[0]

        def panel_count(expected):
            """Espera la notificación de modificación y comprueba el panel, no otra búsqueda."""
            deadline = time.monotonic() + 5
            while True:
                message, opened, count = invoke("Checks.State")
                assert not message, message
                assert opened, "El diálogo no está abierto."
                if count == expected:
                    return
                if time.monotonic() >= deadline:
                    raise AssertionError(f"Se esperaban {expected} avisos; el panel muestra {count}.")
                time.sleep(0.05)

        found = invoke("Main.FindPending", document)
        print("Coincidencias:", found.getCount(), flush=True)
        assert found.getCount() == 3, found.getCount()
        invoke("Main.ShowPending")
        panel_count(3)
        panel = invoke("Checks.Panel")
        panel.getControl("PendingList").selectItemPos(0, True)
        invoke("Main.PendingList_itemStateChanged", uno.createUnoStruct("com.sun.star.awt.ItemEvent"))
        selected = document.CurrentController.getSelection().getByIndex(0)
        assert selected.getString() == "[PENDIENTE: Datos del maestro]", selected.getString()
        selected.setString("Datos completos")
        panel_count(2)
        first = desktop.loadComponentFromURL((target.parent / "primero.odt").as_uri(), "_blank", 0, (
            property_value("Hidden", True),
        ))
        descriptor = first.createSearchDescriptor()
        descriptor.SearchString = "[PENDIENTE: Reutilización y destino]"
        first.findFirst(descriptor).setString("Se reutilizará en la propia obra.")
        first.store()
        first.dispose()
        first = None
        invoke("Main.UpdatePendingLinks")
        panel_count(1)
        invoke("Main.GoToPending", 0)
        selected = document.CurrentController.getSelection().getByIndex(0)
        assert selected.getString() == "[PENDIENTE DE CONCRETAR: Instalaciones]", selected.getString()
        first = desktop.loadComponentFromURL((target.parent / "segundo.odt").as_uri(), "_blank", 0, (
            property_value("Hidden", True),
        ))
        descriptor = first.createSearchDescriptor()
        descriptor.SearchString = "[PENDIENTE DE CONCRETAR: Instalaciones]"
        first.findFirst(descriptor).setString("Instalaciones definidas.")
        first.store()
        first.close(True)
        first = None
        invoke("Main.UpdatePendingLinks")
        panel_count(0)
        invoke("Main.ClosePending")
        assert not invoke("Checks.State")[1]
        invoke("Main.ShowPending")
        panel_count(0)
        listener = DisposalListener()
        invoke("Checks.Panel").addEventListener(listener)
        document.close(True)
        assert listener.disposed, "El diálogo no se libera al cerrar el maestro."
        document = None
        print("OK: 3 avisos, navegación, eliminación automática, vínculos y reapertura.")
    finally:
        if first is not None:
            first.dispose()
        if document is not None:
            document.close(True)
        if desktop is not None:
            print("Cerrando instancia aislada.", flush=True)
            desktop.terminate()
            print("Instancia cerrada.", flush=True)
        process.wait(timeout=10)


if __name__ == "__main__":
    check(Path(sys.argv[1]).resolve())
