import base64
import io
import zipfile

from fastapi.testclient import TestClient

from story2script.main import app
from story2script.novel_import import import_novel_content


client = TestClient(app)


def sample_epub_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>雾港 EPUB</dc:title>
  </metadata>
  <manifest>
    <item id="chapter-1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter-2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter-1"/>
    <itemref idref="chapter-2"/>
  </spine>
</package>
""",
        )
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1>第一章 雾起</h1>
    <p>林夏说：“出发吧。”</p>
  </body>
</html>
""",
        )
        archive.writestr(
            "OEBPS/chapter2.xhtml",
            """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1>第二章 潮声</h1>
    <p>雨落下来。</p>
  </body>
</html>
""",
        )
    return buffer.getvalue()


def encode_payload(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_import_novel_content_extracts_epub_text() -> None:
    imported = import_novel_content("雾港.epub", encode_payload(sample_epub_bytes()))

    assert imported.file_type == "epub"
    assert imported.title == "雾港 EPUB"
    assert "第一章 雾起" in imported.novel_text
    assert "林夏说：“出发吧。”" in imported.novel_text
    assert imported.novel_text.index("第一章 雾起") < imported.novel_text.index("第二章 潮声")


def test_import_novel_api_returns_epub_text() -> None:
    response = client.post(
        "/api/novels/import",
        json={
            "file_name": "雾港.epub",
            "content_base64": encode_payload(sample_epub_bytes()),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["file_type"] == "epub"
    assert body["title"] == "雾港 EPUB"
    assert "第二章 潮声" in body["novel_text"]
    assert body["character_count"] == len(body["novel_text"])


def test_import_novel_api_rejects_mobi() -> None:
    response = client.post(
        "/api/novels/import",
        json={
            "file_name": "雾港.mobi",
            "content_base64": encode_payload(b"not a supported ebook"),
        },
    )

    assert response.status_code == 422
    assert "MOBI" in response.json()["detail"]
