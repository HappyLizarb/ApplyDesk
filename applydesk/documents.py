"""Store uploaded originals, render previews, and verify saved documents."""

from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import uuid

from PIL import Image

from .document_import import MAX_PAGES, read_docx, read_pdf, render_docx, render_pdf, selected_resume, validate_upload
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

    def _paths(self, record, pdf, images, source):
        # Only relative paths leave the service, so HTTP callers need no disk access.
        record.update(pdf_path=record["source_path"] if pdf == source else str(pdf.relative_to(self.root)),
                      image_paths=[str(path.relative_to(self.root)) for path in images])
        for path in (self.root / record["id"]).rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)

    def import_file(self, filename: str, data: bytes, kind: str) -> dict:
        # Original filenames are display metadata; UUIDs select every disk location.
        if kind not in {"profile", "transcript"}:
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
            return self._save(record)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def register_resume(self, path):
        """Register a native file selection and keep only its previews and private location in the workspace."""
        source = Path(path)
        data, binding = selected_resume(source)
        if Path(binding["canonical"]).is_relative_to(self.root):
            raise ValueError("Choose the original resume outside ApplyDesk storage.")
        validate_upload(source.name, data)
        identity = uuid.uuid4().hex
        directory = self.root / identity
        directory.mkdir(mode=0o700)
        record = {"id": identity, "kind": "resume", "filename": source.name,
                  "created_at": datetime.now(timezone.utc).isoformat(), "source_path": identity + "/selected-original",
                  "source_sha256": hashlib.sha256(data).hexdigest(), "source_size": len(data)}
        try:
            (directory / ".location").write_text(json.dumps(binding), encoding="utf-8")
            if source.suffix.lower() == ".docx":
                # LibreOffice needs a named input. Only this private rendering copy is temporary.
                with tempfile.TemporaryDirectory(dir=directory, prefix=".render-") as temp:
                    render_input = Path(temp) / "resume.docx"
                    render_input.write_bytes(data)
                    self._prepare_import(record, render_input)
            else:
                self._prepare_import(record, source, data)
            self.source_file(record, for_upload=True)
            self._prepare_resume(record)
            return self._save(record)
        except Exception as error:
            shutil.rmtree(directory, ignore_errors=True)
            if isinstance(error, ValueError) and str(error).startswith("The selected resume"):
                raise
            raise ValueError("Cannot read or preview the selected resume. Choose a readable PDF or DOCX.") from None

    def _prepare_resume(self, record):
        """Keep uploaded bytes intact and expose complete text solely for independent review."""
        blocks = record.get("blocks") or [{"id": f"line:{index}", "text": line}
                                         for index, line in enumerate(record.get("text", "").splitlines()) if line.strip()]
        record.update(document_type="resume", title=record["filename"],
                      blocks=[{**block, "editable": False} for block in blocks],
                      sha256=(record["source_sha256"] if record["pdf_path"] == record["source_path"] else
                              hashlib.sha256(self.asset(record["pdf_path"]).read_bytes()).hexdigest()))

    def _prepare_import(self, record, source, data=None):
        """Extract readable text and render previews without modifying the uploaded source."""
        directory = self.root / record["id"]
        if source.suffix.lower() == ".docx":
            record.update(read_docx(source), needs_manual_entry=False, docx_path=record["source_path"])
            pdf, images = render_docx(source, directory / "preview")
        else:
            record.update(read_pdf(BytesIO(data) if data is not None else source))
            pdf = source
            images = render_pdf(data if data is not None else source, directory / "source-preview")
        record["confirmed"] = True
        record["needs_fidelity_confirmation"] = False
        record["warnings"] = (["No extractable text was found. OCR is not included; enter profile facts manually."]
                              if record["needs_manual_entry"] else [])
        self._paths(record, pdf, images, source)

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
                if hashlib.sha256(self.asset_bytes(record[key])[1]).hexdigest() != record[digest]:
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

    def preview(self, identity):
        """Read the saved version's page images or rebuild missing pages from its own PDF."""
        with self.lock:
            record = self.get(identity)
            paths = record.get("image_paths") or []
            if 0 < len(paths) <= MAX_PAGES and all(self._readable_page(path) for path in paths):
                return record
            directory = (self.root / identity).resolve()
            if directory.parent != self.root or not directory.is_dir():
                return {**record, "image_paths": []}
            rendered = None
            try:
                _, pdf = self.asset_bytes(record["pdf_path"])
                expected = record.get("sha256")
                if expected and hashlib.sha256(pdf).hexdigest() != expected:
                    return {**record, "image_paths": []}
                rendered = Path(tempfile.mkdtemp(prefix="pages-", dir=directory))
                pages = [str(path.relative_to(self.root)) for path in render_pdf(pdf, rendered)]
                self._save({**self.manifest[identity], "image_paths": pages})
                return self.get(identity)
            except Exception:
                if rendered:
                    shutil.rmtree(rendered, ignore_errors=True)
                return {**record, "image_paths": []}

    def _readable_page(self, path):
        """Verify the saved PNG, including its chunks, before offering it in a job preview."""
        try:
            with Image.open(self.asset(path), formats=["PNG"]) as page:
                page.verify()
            return True
        except (OSError, TypeError, ValueError, SyntaxError, Image.DecompressionBombError):
            return False

    def remove_upload(self, identity):
        """Delete any selected document's original, previews and manifest entry."""
        with self.lock:
            self.get(identity)
            return remove_unused(self, identity)

    def list_documents(self):
        # All versions are retained so applications can name the exact uploaded file.
        with self.lock:
            return deepcopy([record for record in self.manifest.values() if record["kind"] in {"document", "resume"}])

    def source_file(self, record, *, for_upload=False):
        """Resolve the chosen original for attachments and allow older workspace copies for reading."""
        if record.get("source_path") != record["id"] + "/selected-original":
            if for_upload and record["kind"] == "resume":
                raise ValueError("Choose the original resume again in Documents so ApplyDesk can attach it from its saved location.")
            return self.asset(record["source_path"])
        source, _ = self._registered_original(record)
        return source

    def _registered_original(self, record):
        """Read the registered bytes through the selected file identity without exposing its location."""
        try:
            binding = json.loads((self.root / record["id"] / ".location").read_text(encoding="utf-8"))
            source = Path(binding["path"])
            if source.name != record["filename"]:
                raise KeyError
        except (OSError, UnicodeError, TypeError, ValueError, KeyError):
            raise ValueError("The selected resume is unavailable. Choose the original file again in Documents.") from None
        data, _ = selected_resume(source, binding)
        digest = hashlib.sha256(data).hexdigest()
        if digest != record["source_sha256"] or len(data) != record.get("source_size"):
            raise ValueError("The selected resume changed. Choose its current version again in Documents.")
        return source, data

    def asset_bytes(self, path):
        """Serve exactly the checked local selection or a private document's stored media."""
        relative = Path(path)
        if len(relative.parts) < 2 or relative.is_absolute():
            raise ValueError("Document asset not found.")
        record = self.get(relative.parts[0])
        allowed = (record.get("source_path"), record.get("pdf_path"), record.get("docx_path"), *record.get("image_paths", []))
        if path not in allowed:
            raise ValueError("Document asset not found.")
        if relative.name == "selected-original":
            source, data = self._registered_original(record)
            return source.name, data
        asset = self.asset(path)
        return asset.name, asset.read_bytes()

    def asset(self, path):
        """Expose document media, including the exact PDF or DOCX explicitly selected for this record."""
        relative = Path(path)
        if len(relative.parts) == 2 and relative.name == "selected-original":
            record = self.get(relative.parts[0])
            if record.get("source_path") == path:
                return self.source_file(record)
        candidate = (self.root / relative).resolve()
        if (relative.is_absolute() or not candidate.is_relative_to(self.root) or candidate.suffix.lower() not in {".pdf", ".docx", ".png"}
                or any(part.startswith(".") for part in relative.parts) or not candidate.is_file()):
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
