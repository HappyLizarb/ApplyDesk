"""Document provenance and real rendering checks, using temporary private files."""

import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from reportlab.pdfgen import canvas

from applydesk.document_import import read_docx, validate_upload
from applydesk.documents import DocumentService


def sample_docx():
    # This synthetic fixture includes table and header text missed by simple readers.
    document = Document()
    section = document.sections[0]
    section.top_margin = section.bottom_margin = Inches(0.65)
    document.styles["Normal"].font.name = "Arial"
    document.styles["Normal"].font.size = Pt(10)
    section.header.paragraphs[0].text = "SAMPLE PROFILE"
    document.add_heading("Alex Sample", 0)
    document.add_paragraph("alex@example.test | San Francisco")
    document.add_heading("Experience", 1)
    paragraph = document.add_paragraph("Software intern\t2025")
    paragraph.add_run("\nBuilt a tested scheduling tool.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Education"
    table.cell(0, 1).text = "Example University"
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def sample_pdf():
    # Generate real PDF bytes for extraction, integrity, and preview checks.
    stream = BytesIO()
    page = canvas.Canvas(stream)
    page.setFont("Helvetica-Bold", 20)
    page.drawString(48, 780, "Alex Sample")
    page.setFont("Helvetica", 11)
    page.drawString(48, 750, "Software engineering student")
    page.drawString(48, 725, "Built a scheduling tool with Python")
    page.save()
    return stream.getvalue()


def saved_letter(service, job_id):
    """Populate the former manifest layout using independent template and generated file records."""
    docx = sample_docx()
    pdf = sample_pdf()
    template_id = "archived-template"
    records = []
    for identity, kind in [(template_id, "cover_template"), ("archived-letter", "document")]:
        directory = service.root / identity
        directory.mkdir()
        (directory / "original.docx").write_bytes(docx)
        (directory / "preview.pdf").write_bytes(pdf)
        (directory / "page.png").write_bytes(b"synthetic archived preview")
        docx_file = f"{identity}/original.docx"
        docx_hash = hashlib.sha256(docx).hexdigest()
        record = {"id": identity, "kind": kind, "filename": "Letter.docx", "title": "Saved letter",
                  "docx_path": docx_file, "pdf_path": f"{identity}/preview.pdf",
                  "image_paths": [f"{identity}/page.png"], "text": "Saved letter text",
                  "blocks": [{"id": "word/document.xml#p0", "text": "Saved letter text"}]}
        if kind == "cover_template":
            record.update(source_path=docx_file, source_sha256=docx_hash,
                          template_docx_sha256=docx_hash, confirmed=True)
        else:
            record.update(document_type="cover", job_id=job_id, template_id=template_id,
                          sha256=hashlib.sha256(pdf).hexdigest(), docx_sha256=docx_hash)
        records.append(service._save(record))
    return records


class DocumentTests(unittest.TestCase):
    def setUp(self):
        # Each service has a private root and no real user documents.
        self.temporary = tempfile.TemporaryDirectory(prefix="applydesk-doc-test-")
        self.service = DocumentService(Path(self.temporary.name))

    def tearDown(self):
        # Do not retain synthetic identity information in the application workspace.
        self.temporary.cleanup()

    def test_filename_and_package_validation(self):
        # Upload validation happens before any renderer touches untrusted files.
        for filename, data in (("../resume.pdf", b"%PDF-test"), ("fake.docx", b"wrong"),
                               ("resume.txt", b"text"), ("empty.pdf", b"")):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                validate_upload(filename, data)

    def test_word_fields_keep_their_protected_status_in_extracted_text(self):
        """Cross-paragraph Word fields retain their metadata without changing the original file."""
        document = Document()
        paragraph = document.add_paragraph()
        field = OxmlElement("w:fldChar")
        field.set(qn("w:fldCharType"), "begin")
        paragraph.add_run()._r.append(field)
        paragraph.add_run("2026")
        document.add_paragraph("A field continues across paragraphs")
        source = Path(self.temporary.name) / "field.docx"
        document.save(source)
        blocks = read_docx(source)["blocks"]
        self.assertTrue(blocks[0]["uneditable"])
        self.assertTrue(blocks[1]["uneditable"])
        self.assertEqual(blocks[1]["text"], "A field continues across paragraphs")

    def test_profile_docx_preserves_original_header_and_table_with_rendered_preview(self):
        """Profile extraction retains content in the header and table, plus every original byte."""
        data = sample_docx()
        record = self.service.import_file("profile.docx", data, "profile")
        self.assertEqual(self.service.asset(record["source_path"]).read_bytes(), data)
        self.assertIn("Education", record["text"])
        self.assertIn("SAMPLE PROFILE", record["text"])
        self.assertEqual(len(record["image_paths"]), 1)
        self.assertTrue(self.service.asset(record["image_paths"][0]).read_bytes().startswith(b"\x89PNG"))

    def test_transcript_pdf_remains_the_original_after_text_extraction(self):
        """Transcript import keeps the same PDF for download and records a readable preview."""
        data = sample_pdf()
        record = self.service.import_file("transcript.pdf", data, "transcript")
        self.assertEqual(self.service.asset(record["source_path"]).read_bytes(), data)
        self.assertEqual(record["pdf_path"], record["source_path"])
        self.assertIn("Built a scheduling tool with Python", record["text"])
        self.assertFalse(record["needs_manual_entry"])
        self.assertTrue(self.service.asset(record["image_paths"][0]).is_file())

    def test_archived_files_are_readable_and_hashes_detect_tampering_after_restart(self):
        """Old document and template manifests still resolve to their independently stored files."""
        template, letter = saved_letter(self.service, "example-job")
        reopened = DocumentService(self.service.root)
        self.assertEqual(reopened.list_templates(), [template])
        self.assertEqual(reopened.list_documents(), [letter])
        self.assertEqual(reopened.get(letter["id"])["job_id"], "example-job")
        self.assertTrue(reopened.verify(letter["id"]))
        reopened.asset(letter["pdf_path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed on disk"):
            reopened.verify(letter["id"])

    def test_legacy_application_preview_recovers_pages_without_changing_saved_files(self):
        """Recover a missing legacy PNG from the exact saved version, keeping downloads intact."""
        _, letter = saved_letter(self.service, "example-job")
        self.service.asset(letter["image_paths"][0]).unlink()
        pdf = self.service.asset(letter["pdf_path"])
        original = pdf.read_bytes()
        preview = self.service.preview(letter["id"])
        page = self.service.asset(preview["image_paths"][0])
        self.assertEqual(page.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(pdf.read_bytes(), original)
        self.assertEqual(DocumentService(self.service.root).preview(letter["id"]), preview)
        # An interrupted write can leave a valid header on an unreadable page.
        page.write_bytes(page.read_bytes()[:16])
        pdf.write_bytes(b"changed")
        self.assertEqual(self.service.preview(letter["id"])["image_paths"], [])

    def test_template_uploads_and_paths_outside_the_document_root_are_rejected(self):
        """Only current upload kinds enter the manifest, and downloads stay in the private root."""
        for kind in ("resume_template", "cover_template"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.service.import_file("template.pdf", sample_pdf(), kind)
        self.assertEqual(self.service.list_templates(), [])
        with self.assertRaises(ValueError):
            self.service.asset("../../../../etc/passwd")
