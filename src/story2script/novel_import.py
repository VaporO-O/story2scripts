import base64
import binascii
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree


MAX_IMPORT_BYTES = 25 * 1024 * 1024
TEXT_EXTENSIONS = {".txt", ".text", ".md", ".markdown", ".csv", ".log"}
UNSUPPORTED_EBOOK_EXTENSIONS = {".mobi", ".azw", ".azw3"}
EPUB_DOCUMENT_MEDIA_TYPES = {
    "application/xhtml+xml",
    "text/html",
}


@dataclass(frozen=True)
class ImportedNovel:
    file_name: str
    file_type: str
    title: str
    novel_text: str

    @property
    def character_count(self) -> int:
        return len(self.novel_text)


class XHTMLTextExtractor(HTMLParser):
    block_tags = {
        "article",
        "blockquote",
        "body",
        "br",
        "chapter",
        "dd",
        "div",
        "dt",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "section",
        "td",
        "th",
        "tr",
    }
    skipped_tags = {"head", "script", "style", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.skipped_tags:
            self.skip_depth += 1
            return
        if tag in self.block_tags:
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.skipped_tags and self.skip_depth:
            self.skip_depth -= 1
            return
        if tag in self.block_tags:
            self._append_break()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self.parts and self.parts[-1] not in {" ", "\n"}:
            self.parts.append(" ")
        self.parts.append(text)

    def get_text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    def _append_break(self) -> None:
        if self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")


def import_novel_content(file_name: str, content_base64: str) -> ImportedNovel:
    data = _decode_base64_payload(content_base64)
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError("文件过大，请导入 25MB 以内的小说文件。")

    extension = Path(file_name).suffix.lower()
    if extension == ".epub":
        title, novel_text = _extract_epub(data, file_name)
        return ImportedNovel(
            file_name=file_name,
            file_type="epub",
            title=title,
            novel_text=novel_text,
        )

    if extension in TEXT_EXTENSIONS:
        return ImportedNovel(
            file_name=file_name,
            file_type=extension.lstrip("."),
            title=_fallback_title(file_name),
            novel_text=_decode_text_bytes(data),
        )

    if extension in UNSUPPORTED_EBOOK_EXTENSIONS:
        raise ValueError("暂不支持直接解析 MOBI/AZW/AZW3，请先转换为 EPUB 或 TXT 后导入。")

    raise ValueError("暂支持导入 EPUB、TXT、Markdown、CSV 和 LOG 等小说文件。")


def _decode_base64_payload(content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("文件内容不是有效的 base64。") from exc


def _extract_epub(data: bytes, file_name: str) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            rootfile_path = _epub_rootfile_path(archive)
            opf_root = ElementTree.fromstring(archive.read(rootfile_path))
            title = _epub_title(opf_root) or _fallback_title(file_name)
            document_paths = _epub_document_paths(opf_root, rootfile_path)
            if not document_paths:
                raise ValueError("EPUB 中未找到正文目录。")

            sections = [
                _html_to_text(_decode_text_bytes(archive.read(document_path)))
                for document_path in document_paths
                if document_path in archive.namelist()
            ]
    except zipfile.BadZipFile as exc:
        raise ValueError("EPUB 文件不是有效的电子书压缩包。") from exc
    except ElementTree.ParseError as exc:
        raise ValueError("EPUB 目录文件解析失败。") from exc
    except KeyError as exc:
        raise ValueError("EPUB 目录引用的正文文件不存在。") from exc

    novel_text = "\n\n".join(section for section in sections if section)
    if not novel_text:
        raise ValueError("EPUB 中未找到可读取的正文。")
    return title, novel_text


def _epub_rootfile_path(archive: zipfile.ZipFile) -> str:
    try:
        container_xml = archive.read("META-INF/container.xml")
    except KeyError as exc:
        raise ValueError("EPUB 缺少 META-INF/container.xml。") from exc

    container = ElementTree.fromstring(container_xml)
    rootfile = container.find(".//{*}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise ValueError("EPUB container.xml 未声明 OPF 入口。")
    return rootfile.get("full-path", "")


def _epub_title(opf_root: ElementTree.Element) -> str:
    title = opf_root.find(".//{http://purl.org/dc/elements/1.1/}title")
    if title is None:
        title = opf_root.find(".//{*}title")
    return title.text.strip() if title is not None and title.text else ""


def _epub_document_paths(opf_root: ElementTree.Element, rootfile_path: str) -> list[str]:
    base_dir = posixpath.dirname(rootfile_path)
    manifest = {
        item.get("id", ""): item
        for item in opf_root.findall(".//{*}manifest/{*}item")
        if item.get("id") and item.get("href")
    }
    paths: list[str] = []
    for itemref in opf_root.findall(".//{*}spine/{*}itemref"):
        item = manifest.get(itemref.get("idref", ""))
        if item is None or not _is_epub_document(item):
            continue
        paths.append(_resolve_epub_path(base_dir, item.get("href", "")))

    if paths:
        return paths

    return [
        _resolve_epub_path(base_dir, item.get("href", ""))
        for item in manifest.values()
        if _is_epub_document(item)
    ]


def _is_epub_document(item: ElementTree.Element) -> bool:
    media_type = item.get("media-type", "")
    href = item.get("href", "").lower()
    return media_type in EPUB_DOCUMENT_MEDIA_TYPES or href.endswith((".xhtml", ".html", ".htm"))


def _resolve_epub_path(base_dir: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(base_dir, href))


def _html_to_text(html_text: str) -> str:
    parser = XHTMLTextExtractor()
    parser.feed(html_text)
    parser.close()
    return parser.get_text()


def _decode_text_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _fallback_title(file_name: str) -> str:
    return Path(file_name).stem or "未命名小说"
