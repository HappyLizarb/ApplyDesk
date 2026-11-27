"""Store uploaded originals, render previews, and verify saved documents."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import threading
import uuid

from .document_import import read_docx, read_pdf, render_docx, render_pdf, validate_upload
from .document_deletion import recover_deletions, remove_unused


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
        if kind not in {"profile", "resume", "transcript"}:
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
        """Extract readable text and render previews without modifying the uploaded source."""
        if source.suffix == ".docx":
            record.update(read_docx(source), needs_manual_entry=False, docx_path=record["source_path"])
            pdf, images = render_docx(source, source.parent / "preview")
        else:
            record.update(read_pdf(source))
            pdf = source
            images = render_pdf(source, source.parent / "source-preview")
        record["confirmed"] = True
        record["needs_fidelity_confirmation"] = False
        record["warnings"] = (["No extractable text was found. OCR is not included; enter profile facts manually."]
                              if record["needs_manual_entry"] else [])
        self._paths(record, pdf, images)

    def list_templates(self):
        # Return isolated metadata so a caller cannot silently change provenance.
        with self.lock:
            return deepcopy([record for record in self.manifest.values()
                             if record["kind"].endswith("_template") and not record.get("removed_from_library_at")])

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
                raise ValueError("The saved document changed on disk. Restore the original file before using this attachment.")
        return True

    def _verify_template(self, record):
        # An external template edit invalidates the original fidelity decision.
        for path_key, hash_key in (("source_path", "source_sha256"), ("docx_path", "template_docx_sha256")):
            actual = hashlib.sha256(self.asset(record[path_key]).read_bytes()).hexdigest()
            if actual != record[hash_key]:
                raise ValueError("The archived template changed on disk. Restore the original file to verify this document.")

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


class DocumentTasks:
    """Verify saved files without generation, customization, or provider calls."""

    def __init__(self, workspace):
        self.w = workspace

    def review(self, document_id):
        """Verify stored bytes and available previews locally, without claiming a visual audit."""
        document = self.w.docs.get(document_id)
        self.w.docs.verify(document_id)
        paths = document.get("image_paths", [])
        issues = []
        if not paths or any(not self.w.docs.asset(path).read_bytes() for path in paths):
            issues.append("Document page previews are missing.")
        review = {"document_id": document_id, "passed": not issues, "issues": issues,
                  "method": "file_integrity", "profile_revision": self.w.profile()["revision"]}
        self.w.store.put("document_review", document_id, review)
        return review

    def require_review(self, document_id):
        """Changed bytes or missing previews block an upload without another agent pass."""
        review = self.review(document_id)
        if not review["passed"]:
            raise ValueError("Document requires correction: " + "; ".join(review["issues"]))
        return review
