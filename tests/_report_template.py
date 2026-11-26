"""Plantilla ODT sintética para probar cabeceras, pies y recursos."""
from io import BytesIO
from pathlib import Path

from PIL import Image

from odt_reports import _OdtReport


def write_report_template(path: Path) -> Path:
    """Crea una plantilla explícita sin depender de recursos distribuidos."""
    image = BytesIO()
    Image.new("RGB", (240, 80), (20, 70, 130)).save(image, format="JPEG")
    report = _OdtReport(
        "Plantilla sintética", "Proyecto de prueba", "",
        image_data=image.getvalue(), image_path="Pictures/logo.jpg", image_type="image/jpeg",
        image_width=100, image_height=33, footer="PIE DE PRUEBA",
    )
    report.write(path)
    return path
