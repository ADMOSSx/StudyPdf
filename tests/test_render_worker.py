import pytest

pytest.importorskip("PySide6")
import pymupdf
from PySide6.QtGui import QImage

from client.pdf.render_worker import PageRenderTask


def test_background_page_render_task_returns_an_image(tmp_path):
    path = tmp_path / "tiny.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=300)
    page.insert_text((30, 50), "StudyPDF render test")
    doc.save(path)
    doc.close()

    received = []
    task = PageRenderTask(str(path), 0, 1.0, (0, 1.0, 1.0), 7)
    task.signals.finished.connect(lambda key, generation, image, error: received.append((key, generation, image, error)))
    task.run()

    assert len(received) == 1
    key, generation, image, error = received[0]
    assert key == (0, 1.0, 1.0)
    assert generation == 7
    assert isinstance(image, QImage)
    assert not image.isNull()
    assert error == ""
