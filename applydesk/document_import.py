"""Validate, read, and preview uploaded PDF and DOCX files."""

import os
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from lxml import etree
import pdfplumber

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{" + NS["w"] + "}"
MAX_UPLOAD = 20 * 1024 * 1024
MAX_PAGES = 40
RUNTIME = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies"
SOFFICE = os.environ.get("APPLYDESK_SOFFICE") or shutil.which("soffice") or RUNTIME / "bin/override/soffice"
PDFTOPPM = os.environ.get("APPLYDESK_PDFTOPPM") or shutil.which("pdftoppm") or RUNTIME / "bin/override/pdftoppm"
PART = re.compile(r"word/(document|header\d+|footer\d+|footnotes|endnotes)\.xml$")


def validate_upload(filename, data):
    # Never use an uploaded path as a filesystem destination.
    if not isinstance(filename, str) or Path(filename).name != filename or "\\" in filename:
        raise ValueError("Choose a filename without directory components.")
    extension = Path(filename).suffix.lower()
    if extension not in {".pdf", ".docx"} or not data or len(data) > MAX_UPLOAD:
        raise ValueError("Upload a nonempty PDF or DOCX no larger than 20 MB.")
    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("The file does not contain a PDF.")
    if extension == ".docx":
        validate_package(data)
    return extension


def validate_package(data):
    # Reject active or externally loaded content before LibreOffice sees the file.
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 3000 or sum(item.file_size for item in entries) > 100 * 1024 * 1024:
                raise ValueError("The DOCX package is too large to process safely.")
            if len({entry.filename for entry in entries}) != len(entries):
                raise ValueError("The DOCX package contains ambiguous duplicate entries.")
            if "word/document.xml" not in archive.namelist():
                raise ValueError("The file does not contain a DOCX document.")
            for entry in entries:
                if any(part in entry.filename.lower() for part in ("vbaproject", "embeddings/", "activex/")):
                    raise ValueError("Remove macros and embedded active objects before importing.")
                if entry.filename.endswith(".rels"):
                    relationships = parse_xml(archive.read(entry.filename))
                    for relationship in relationships:
                        if relationship.get("TargetMode") == "External" and not relationship.get("Type", "").endswith("/hyperlink"):
                            raise ValueError("Externally linked document content is not supported.")
    except BadZipFile as error:
        raise ValueError("The DOCX package is invalid.") from error


def parse_xml(data):
    # Disable entity resolution even though normal OOXML does not need entities.
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.fromstring(data, parser)


def paragraph_nodes(paragraph):
    # Exclude nested text-box paragraphs so their text appears only once.
    return [node for node in paragraph.iter() if node.tag in {W + "t", W + "tab", W + "br"}
            if next(node.iterancestors(W + "p"), None) is paragraph]


def read_docx(path):
    # Read body, header and table paragraphs using stable DOCX part and paragraph IDs.
    blocks = []
    with ZipFile(path) as archive:
        for name in sorted(filter(PART.fullmatch, archive.namelist())):
            root = parse_xml(archive.read(name))
            field_depth = 0
            for index, paragraph in enumerate(root.iter(W + "p")):
                starts = len(paragraph.xpath('.//w:fldChar[@w:fldCharType="begin"]', namespaces=NS))
                ends = len(paragraph.xpath('.//w:fldChar[@w:fldCharType="end"]', namespaces=NS))
                inside_field = field_depth > 0
                field_depth = max(0, field_depth + starts - ends)
                nodes = paragraph_nodes(paragraph)
                if not nodes:
                    continue
                text = "".join(node_text(node) for node in nodes)
                style = paragraph.find("w:pPr/w:pStyle", NS)
                protected = inside_field or bool(paragraph.xpath(".//w:fldChar | .//w:instrText | .//w:fldSimple | .//w:hyperlink | .//w:ins | .//w:del", namespaces=NS))
                blocks.append({"id": f"{name}#p{index}", "text": text,
                               "style": style.get(W + "val") if style is not None else "Normal",
                               "editable": not protected, "uneditable": protected,
                               "mixed_runs": len(nodes) > 1})
    return {"blocks": blocks, "text": "\n".join(block["text"] for block in blocks)}


def node_text(node):
    # Keep tabs and line breaks in extracted paragraph text.
    return "\t" if node.tag == W + "tab" else "\n" if node.tag == W + "br" else node.text or ""


def read_pdf(path):
    # Text extraction is intentionally separate from OCR and never invents scan text.
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > MAX_PAGES:
            raise ValueError(f"Documents may contain at most {MAX_PAGES} pages.")
        lines = ["\n".join(line["text"] for line in page.extract_text_lines(return_chars=False))
                 for page in pdf.pages]
    text = "\n\n".join(lines)
    return {"text": text, "needs_manual_entry": not bool(text.strip())}


def run_renderer(command):
    # Never interpolate document names into a shell command.
    try:
        result = subprocess.run([str(item) for item in command], capture_output=True,
                                text=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Document renderer is unavailable: {error}") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1200:]
        raise RuntimeError(f"Document rendering failed: {detail}")


def render_docx(source, directory):
    # Separate office profiles avoid profile locks when two documents render together.
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="applydesk-office-") as profile:
        run_renderer([SOFFICE, f"-env:UserInstallation={Path(profile).as_uri()}",
                      "--headless", "--convert-to", "pdf", "--outdir", directory, source])
    result = directory / (source.stem + ".pdf")
    if not result.is_file() or not result.stat().st_size:
        raise RuntimeError("LibreOffice did not produce a PDF preview.")
    return result, render_pdf(result, directory)


def render_pdf(source, directory):
    # Bound preview expansion; a valid PDF must render every page for later review.
    with pdfplumber.open(source) as pdf:
        page_count = len(pdf.pages)
    if not 1 <= page_count <= MAX_PAGES:
        raise ValueError(f"PDF previews require between 1 and {MAX_PAGES} pages.")
    directory.mkdir(parents=True, exist_ok=True)
    run_renderer([PDFTOPPM, "-scale-to", "1800", "-png", source, directory / "page"])
    images = sorted(directory.glob("page-*.png"), key=lambda path: int(path.stem.split("-")[-1]))
    if len(images) != page_count:
        raise RuntimeError("Some PDF preview pages did not render.")
    return images
