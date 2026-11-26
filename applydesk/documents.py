"""Immutable local document versions with template and preview provenance."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import threading
import uuid

from .document_import import read_docx, read_pdf, reconstruct_pdf, replace_docx, validate_upload
from .document_render import render_docx, render_pdf
from .document_cleanup import recover_deletions, remove_unused


class DocumentService:
    def __init__(self, root: Path):
        # A private directory contains originals, previews, and immutable versions.
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.manifest_path = self.root / "manifest.json"
        self.lock = threading.RLock()
        self.manifest = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else {}
        recover_deletions(self.root, self.manifest)

    def _save(self, record):
        # Atomic replacement prevents a partial manifest after an interrupted save.
        with self.lock:
            self._write_manifest({**self.manifest, record["id"]: record})
        return deepcopy(record)

    def _write_manifest(self, records):
        """Publish metadata only after its atomic disk replacement succeeds."""
        temporary = self.root / "manifest.tmp"
        temporary.write_text(json.dumps(records, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.manifest_path)
        self.manifest = records

    def _paths(self, record, pdf, images):
        # Only relative paths leave the service, so HTTP callers need no disk access.
        record.update(pdf_path=str(pdf.relative_to(self.root)),
                      image_paths=[str(path.relative_to(self.root)) for path in images])
        for path in (self.root / record["id"]).rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)

    def import_file(self, filename: str, data: bytes, kind: str) -> dict:
        # Original filenames are display metadata; UUIDs select every disk location.
        if kind not in {"profile", "resume", "resume_template", "cover_template", "transcript"}:
            raise ValueError("Unknown document import kind.")
        extension = validate_upload(filename, data)
        identity = uuid.uuid4().hex
        directory = self.root / identity
        directory.mkdir(mode=0o700)
        source = directory / ("original" + extension)
        try:
            source.write_bytes(data)
            record = {"id": identity, "kind": kind, "filename": filename,
                      "created_at": datetime.now(timezone.utc).isoformat(),
                      "source_path": str(source.relative_to(self.root)),
                      "source_sha256": hashlib.sha256(data).hexdigest()}
            self._prepare_import(record, source)
            if kind == "resume":
                self._prepare_resume(record)
            return self._save(record)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def _prepare_resume(self, record):
        """Keep uploaded bytes intact and expose complete text solely for independent review."""
        blocks = record.get("blocks") or [{"id": f"line:{index}", "text": line}
                                         for index, line in enumerate(record.get("text", "").splitlines()) if line.strip()]
        record.update(document_type="resume", title=record["filename"],
                      blocks=[{**block, "editable": False} for block in blocks],
                      sha256=hashlib.sha256(self.asset(record["pdf_path"]).read_bytes()).hexdigest())

    def _prepare_import(self, record, source):
        # Templates get both source and editable previews for the fidelity decision.
        is_template = record["kind"].endswith("_template")
        if source.suffix == ".docx":
            record.update(read_docx(source), needs_manual_entry=False)
            pdf, images = render_docx(source, source.parent / "preview")
            record.update(docx_path=str(source.relative_to(self.root)), confirmed=True,
                          needs_fidelity_confirmation=False)
        else:
            extracted = read_pdf(source)
            record.update(text=extracted["text"], needs_manual_entry=extracted["needs_manual_entry"])
            images = render_pdf(source, source.parent / "source-preview")
            pdf = source
            if is_template:
                record["source_image_paths"] = [str(path.relative_to(self.root)) for path in images]
                editable = source.parent / "editable.docx"
                reconstruct_pdf(source, editable)
                record.update(read_docx(editable), docx_path=str(editable.relative_to(self.root)))
                pdf, images = render_docx(editable, source.parent / "preview")
            record.update(confirmed=not is_template, needs_fidelity_confirmation=is_template)
        record["warnings"] = self._warnings(record)
        self._paths(record, pdf, images)
        if is_template:
            record["template_docx_sha256"] = hashlib.sha256(self.asset(record["docx_path"]).read_bytes()).hexdigest()

    def _warnings(self, record):
        # Surface limitations before a template can be trusted or uploaded.
        warnings = []
        if record.get("needs_fidelity_confirmation"):
            warnings.append("PDF layout was reconstructed. Compare every page with the original before confirming.")
        if any(block.get("mixed_runs") for block in record.get("blocks", [])):
            warnings.append("New text inherits formatting from its source span. Review mixed bold, tabs, and line breaks after editing.")
        if any(not block.get("editable", True) for block in record.get("blocks", [])):
            warnings.append("Word fields, linked paragraphs, and tracked changes are protected. Edit these in the source DOCX before importing.")
        if record.get("needs_manual_entry"):
            warnings.append("No extractable text was found. OCR is not included; enter profile facts manually.")
        return warnings

    def confirm_template(self, identity):
        # Confirmation applies to this exact imported source and reconstruction.
        record = self.get_template(identity)
        self._verify_template(record)
        record.update(confirmed=True, needs_fidelity_confirmation=False)
        return self._save(record)

    def get_template(self, identity, *, include_removed=False):
        # Reject document IDs where callers expect a design source.
        record = self.get(identity)
        if not record["kind"].endswith("_template"):
            raise ValueError("Choose an imported resume or cover letter template.")
        if record.get("removed_from_library_at") and not include_removed:
            raise ValueError("This template was deleted from Documents. Import a template before creating another letter.")
        return record

    def list_templates(self):
        # Return isolated metadata so a caller cannot silently change provenance.
        with self.lock:
            return deepcopy([record for record in self.manifest.values()
                             if record["kind"].endswith("_template") and not record.get("removed_from_library_at")])

    def create(self, template_id, edits, title, category="", job_id=None):
        # Reusable categories and job copies share templates but never mutable files.
        template = self.get_template(template_id)
        self._verify_template(template)
        if not template.get("confirmed"):
            raise ValueError("Confirm the reconstructed PDF template before creating documents.")
        return self._version(template, template, edits, title, category, job_id)

    def edit(self, document_id, edits):
        # Editing always starts from the chosen version and leaves old uploads intact.
        self.verify(document_id)
        previous = self.get(document_id)
        if previous["kind"] != "document":
            raise ValueError("Only a generated document can be versioned.")
        template = self.get_template(previous["template_id"], include_removed=True)
        self._verify_template(template)
        return self._version(template, previous, edits, previous["title"],
                             previous.get("category", ""), previous.get("job_id"))

    def copy_for_job(self, document_id, job_id):
        # Copy verified bytes so a job edit never mutates a reusable category master.
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("Choose a job for this document copy.")
        self.verify(document_id)
        previous = self.get(document_id)
        identity = uuid.uuid4().hex
        directory = self.root / identity
        directory.mkdir(mode=0o700)
        record = deepcopy(previous)
        try:
            paths = [previous["docx_path"], previous["pdf_path"], *previous["image_paths"]]
            for path in paths:
                source = self.asset(path)
                target = directory / source.relative_to(self.root / document_id)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            record.update(id=identity, parent_id=document_id, job_id=job_id,
                          created_at=datetime.now(timezone.utc).isoformat(),
                          docx_path=previous["docx_path"].replace(document_id, identity, 1))
            pdf = directory / Path(previous["pdf_path"]).relative_to(document_id)
            images = [directory / Path(path).relative_to(document_id) for path in previous["image_paths"]]
            self._paths(record, pdf, images)
            return self._save(record)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def verify(self, document_id):
        # Rehash immediately before upload; a stored verdict cannot approve changed bytes.
        record = self.get(document_id)
        if record["kind"] == "resume":
            for key, digest in (("source_path", "source_sha256"), ("pdf_path", "sha256")):
                if hashlib.sha256(self.asset(record[key]).read_bytes()).hexdigest() != record[digest]:
                    raise ValueError("The uploaded resume changed. Upload it again.")
            return True
        if record["kind"] != "document":
            raise ValueError("Choose a generated document version.")
        for path_key, hash_key in (("pdf_path", "sha256"), ("docx_path", "docx_sha256")):
            actual = hashlib.sha256(self.asset(record[path_key]).read_bytes()).hexdigest()
            if actual != record[hash_key]:
                raise ValueError("The saved document changed outside the editor. Generate a new version before uploading.")
        return True

    def _verify_template(self, record):
        # An external template edit invalidates the original fidelity decision.
        for path_key, hash_key in (("source_path", "source_sha256"), ("docx_path", "template_docx_sha256")):
            actual = hashlib.sha256(self.asset(record[path_key]).read_bytes()).hexdigest()
            if actual != record[hash_key]:
                raise ValueError("The template changed outside the app. Import it again before use.")

    def _version(self, template, previous, edits, title, category, job_id):
        # Rendering and hashing complete before the immutable record becomes visible.
        if not isinstance(edits, dict) or not isinstance(title, str) or not title.strip():
            raise ValueError("Provide document text edits and a title.")
        identity = uuid.uuid4().hex
        directory = self.root / identity
        directory.mkdir(mode=0o700)
        destination = directory / "document.docx"
        try:
            replace_docx(self.asset(previous["docx_path"]), destination, edits)
            pdf, images = render_docx(destination, directory / "preview")
            record = {"id": identity, "kind": "document", "template_id": template["id"],
                      "title": title.strip(), "category": category, "job_id": job_id,
                      "document_type": template["kind"].removesuffix("_template"),
                      "parent_id": previous["id"] if previous["kind"] == "document" else None,
                      "created_at": datetime.now(timezone.utc).isoformat(), "format_review": "pending",
                      "docx_path": str(destination.relative_to(self.root)), **read_docx(destination),
                      "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                      "docx_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
            self._paths(record, pdf, images)
            record["warnings"] = self._warnings(record)
            return self._save(record)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def get(self, identity):
        # IDs resolve only through the manifest, not arbitrary user-supplied paths.
        with self.lock:
            if identity not in self.manifest:
                raise ValueError("Document not found.")
            return deepcopy(self.manifest[identity])

    def remove_upload(self, identity):
        """Delete any selected document's original, previews and manifest entry."""
        with self.lock:
            self.get(identity)
            return remove_unused(self, identity)

    def list_documents(self):
        # All versions are retained so applications can name the exact uploaded file.
        with self.lock:
            return deepcopy([record for record in self.manifest.values() if record["kind"] in {"document", "resume"}])

    def asset(self, path):
        # Relative paths must point at an existing file inside the private document root.
        candidate = (self.root / path).resolve()
        if not candidate.is_relative_to(self.root) or not candidate.is_file():
            raise ValueError("Document asset not found.")
        return candidate
