"""Comprueba índices de anejos en una instancia aislada de Writer."""
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import uno

from _libreoffice_pending_check import property_value


def check(target, expected):
    """Actualiza vínculos e índices y contrasta sus páginas con los títulos."""
    pipe = "contents_" + uuid.uuid4().hex
    process = subprocess.Popen([
        str(Path(sys.executable).with_name("soffice.com")),
        f"-env:UserInstallation={(target.parent / 'contents-profile').as_uri()}",
        "--headless", "--norestore", "--nodefault", "--nofirststartwizard",
        f"--pidfile={target.parent / 'soffice.pid'}",
        f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    desktop = document = None
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local,
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                context = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except uno.getClass("com.sun.star.connection.NoConnectException"):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
        document = desktop.loadComponentFromURL(target.as_uri(), "_blank", 0, (
            property_value("Hidden", True), property_value("MacroExecutionMode", 0),
            property_value("UpdateDocMode", 0),
        ))
        for refreshed in (False, True):
            if refreshed:
                document.updateLinks()
            for _ in range(2):
                document.TextFields.refresh()
                for index in document.DocumentIndexes:
                    index.update()
                document.refresh()
            document.storeToURL(target.with_suffix(".pdf").as_uri(), (
                property_value("FilterName", "writer_globaldocument_pdf_Export"),
                property_value("Overwrite", True),
            ))
            indices = {index.Name: index for index in document.DocumentIndexes}
            assert set(indices) == set(expected), (set(indices), set(expected))
            all_titles = {title for titles in expected.values() for title in titles}
            pages = set()
            for name, titles in expected.items():
                index = indices[name]
                contents = index.Anchor.String
                assert not index.CreateFromOutline, name
                for other in all_titles.difference(titles):
                    assert other not in contents, (name, other, contents)
                for title in titles:
                    match = re.search(re.escape(title) + r"\s+(\d+)\b", contents)
                    assert match, (name, title, contents)
                    search = document.createSearchDescriptor()
                    search.SearchString = title
                    found = document.findAll(search)
                    assert found.Count >= 2, (title, found.Count)
                    cursor = document.CurrentController.ViewCursor
                    cursor.gotoRange(found.getByIndex(found.Count - 1), False)
                    page = cursor.Page
                    assert int(match[1]) == page, (title, match[1], page)
                    pages.add(page)
            assert len(pages) > 2 and max(pages) > 3, pages
            header_count = 0
            for table in document.TextTables:
                if table.getCellByName("A1").String != "HEADER_PLAIN":
                    continue
                for name in ("A1", "B1", "C1"):
                    cell = table.getCellByName(name)
                    cursor = cell.createTextCursor()
                    cursor.goRight(1, True)
                    actual = (cursor.CharFontName, cursor.CharHeight, cursor.CharWeight)
                    assert actual == ("Arial Narrow", 8.0, 150.0), actual
                    assert cell.BackColor == 0x4472C4, cell.BackColor
                    header_count += 1
            assert header_count == 3, header_count
            print(f"OK: {len(indices)} índices locales y páginas reales; vínculos actualizados={refreshed}.",
                  flush=True)
    finally:
        if document is not None:
            document.close(True)
        if desktop is not None:
            desktop.terminate()
        else:
            process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    check(Path(sys.argv[1]).resolve(), json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
