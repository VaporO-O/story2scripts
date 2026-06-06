from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "story2script" / "static"


def test_workbench_exposes_full_conversion_mode_selector() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="convertModeInput"' in html
    assert '<option value="demo" selected>本地</option>' in html
    assert '<option value="ai">AI</option>' in html


def test_workbench_sends_selected_full_conversion_mode() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'const convertModeInput = document.querySelector("#convertModeInput");' in script
    assert "mode: convertModeInput.value" in script


def test_workbench_exposes_novel_file_import() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="importNovelFileButton"' in html
    assert 'id="novelFileInput"' in html
    assert 'type="file"' in html
    assert ".txt,.text,.md,.markdown,.csv,.log,.epub,.mobi,.azw,.azw3,text/*" in html


def test_workbench_reads_imported_novel_file() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'const novelFileInput = document.querySelector("#novelFileInput");' in script
    assert "new FileReader()" in script
    assert 'new TextDecoder("utf-8", { fatal: true })' in script
    assert 'new TextDecoder("gb18030")' in script
    assert "reader.readAsArrayBuffer(file)" in script
    assert 'fetch("/api/novels/import"' in script
    assert "content_base64: arrayBufferToBase64(buffer)" in script
    assert "暂不支持直接解析 MOBI/AZW/AZW3" in script
    assert "novelInput.value = content" in script
    assert "updateChapterCount()" in script
