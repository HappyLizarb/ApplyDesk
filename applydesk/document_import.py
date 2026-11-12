"""Read local documents and preserve DOCX packages while editing text slots."""

from collections import Counter
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.shared import Inches, Pt
from lxml import etree
import pdfplumber

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{" + NS["w"] + "}"
MAX_UPLOAD = 20 * 1024 * 1024
MAX_PAGES = 40
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
    # Exclude nested text-box paragraphs so their text is never replaced twice.
    return [node for node in paragraph.iter() if node.tag in {W + "t", W + "tab", W + "br"}
            if next(node.iterancestors(W + "p"), None) is paragraph]


def read_docx(path):
    # Part names plus paragraph positions remain stable across text-only versions.
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


def replace_docx(source, destination, edits):
    # Copy opaque ZIP parts unchanged; only the requested paragraphs are patched.
    known = {block["id"]: block for block in read_docx(source)["blocks"]}
    if set(edits) - set(known):
        raise ValueError("An edited block no longer exists in this document.")
    if any(not isinstance(text, str) or len(text) > 20000 for text in edits.values()):
        raise ValueError("Each document block must contain at most 20,000 text characters.")
    if any(not known[key]["editable"] and value != known[key]["text"] for key, value in edits.items()):
        raise ValueError("Word fields, linked paragraphs, and tracked changes must be edited in the source DOCX.")
    with ZipFile(source) as original, ZipFile(destination, "w") as output:
        for entry in original.infolist():
            data = original.read(entry.filename)
            selected = {key: value for key, value in edits.items() if key.split("#")[0] == entry.filename}
            if selected:
                root = parse_xml(data)
                for index, paragraph in enumerate(root.iter(W + "p")):
                    key = f"{entry.filename}#p{index}"
                    if key in selected and selected[key] != known[key]["text"]:
                        replace_paragraph(paragraph, selected[key])
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            output.writestr(entry, data)


def replace_paragraph(paragraph, text):
    # Preserve styling on unchanged spans; replacement text inherits its source span.
    nodes = paragraph_nodes(paragraph)
    source = "".join(node_text(node) for node in nodes)
    owners = [index for index, node in enumerate(nodes) for _ in node_text(node)]
    fragments = ["" for _ in nodes]
    for operation, start, end, new_start, new_end in SequenceMatcher(None, source, text).get_opcodes():
        if operation == "equal":
            for offset in range(end - start):
                fragments[owners[start + offset]] += text[new_start + offset]
        elif operation in {"replace", "insert"}:
            owner = owners[min(start, len(owners) - 1)] if owners else 0
            fragments[owner] += text[new_start:new_end]
    for node, fragment in zip(nodes, fragments):
        replace_node_text(node, fragment)


def node_text(node):
    # Represent layout separators in the editable text rather than dropping them.
    return "\t" if node.tag == W + "tab" else "\n" if node.tag == W + "br" else node.text or ""


def replace_node_text(node, text):
    # Keep the original run and its formatting while rebuilding only its text tokens.
    parent, position = node.getparent(), node.getparent().index(node)
    for token in re.split(r"([\n\t])", text):
        tag = "br" if token == "\n" else "tab" if token == "\t" else "t"
        element = etree.Element(W + tag)
        if tag == "t":
            element.text = token
            element.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        parent.insert(position, element)
        position += 1
    parent.remove(node)


def read_pdf(path):
    # Text extraction is intentionally separate from OCR and never invents scan text.
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > MAX_PAGES:
            raise ValueError(f"Documents may contain at most {MAX_PAGES} pages.")
        pages = [{"width": float(page.width), "height": float(page.height),
                  "lines": page.extract_text_lines(return_chars=True)} for page in pdf.pages]
    text = "\n\n".join("\n".join(line["text"] for line in page["lines"]) for page in pages)
    return {"pages": pages, "text": text, "needs_manual_entry": not bool(text.strip())}


def reconstruct_pdf(source, destination):
    # PDF geometry is approximate; the service blocks use until the user confirms it.
    extracted = read_pdf(source)
    if extracted["needs_manual_entry"]:
        raise ValueError("This PDF has no extractable text. Choose a text PDF or DOCX template.")
    document = Document()
    for page_index, page in enumerate(extracted["pages"]):
        section = document.sections[0] if page_index == 0 else document.add_section()
        section.page_width, section.page_height = Pt(page["width"]), Pt(page["height"])
        section.left_margin = section.right_margin = Inches(0.65)
        section.top_margin = section.bottom_margin = Inches(0.6)
        for line in page["lines"]:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.paragraph_format.line_spacing = 1
            run = paragraph.add_run(line["text"])
            characters = line.get("chars", [])
            if characters:
                font = Counter(char.get("fontname", "") for char in characters).most_common(1)[0][0]
                run.font.name = font.split("+")[-1].split("-")[0] or "Arial"
                run.font.size = Pt(max(7, min(28, sum(char["size"] for char in characters) / len(characters))))
                run.bold = "bold" in font.lower()
    document.save(destination)
    return extracted
