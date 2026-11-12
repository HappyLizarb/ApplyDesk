"""One active uploaded resume per user-selected industry, with a General fallback."""


class ResumeLibrary:
    def __init__(self, workspace):
        self.w = workspace

    def categories(self):
        """Keep profile spelling while collapsing case-insensitive duplicate industries."""
        names = {"general": "General"}
        for row in self.w.profile()["industries"]:
            name = row["name"].strip()
            if name:
                names.setdefault(name.casefold(), name)
        return list(names.values())

    def list(self):
        """Only active rows appear; replaced originals remain available to job history."""
        return [{**self.w.docs.get(row["document_id"]), "category": row["category"]}
                for row in self.w.store.get("resume_library", "main", [])]

    def add(self, document_id):
        """New uploads wait for an explicit tag, so General is never replaced by accident."""
        with self.w.lock:
            rows = self.w.store.get("resume_library", "main", [])
            rows.append({"document_id": document_id, "category": ""})
            self.w.store.put("resume_library", "main", rows)

    def tag(self, data):
        """Assign or replace one category atomically without deleting immutable files."""
        category = data.get("category")
        if not isinstance(category, str) or category not in self.categories():
            raise ValueError("Choose General or an industry from your profile.")
        with self.w.lock:
            rows = self.w.store.get("resume_library", "main", [])
            current = next((row for row in rows if row["document_id"] == data.get("document_id")), None)
            if not current or self.w.docs.get(current["document_id"])["kind"] != "resume":
                raise ValueError("Choose an active uploaded resume.")
            if current["category"] == category:
                return current
            updated = {"document_id": current["document_id"], "category": category}
            rows = [row for row in rows if row is not current and row["category"].casefold() != category.casefold()]
            self.w.store.put("resume_library", "main", [*rows, updated])
            for job in self.w.store.all("job"):
                if job["status"] not in {"new", "submitted"} and not job.get("removed_at"):
                    self.w.store.job(job["id"], status="documents_changed",
                                     exceptions=["Resume selection changed. Prepare again to check the attachment."])
            return updated

    def select(self, category):
        """An exact industry tag wins; otherwise only General may be used."""
        allowed = {name.casefold() for name in self.categories()}
        key = category.strip().casefold() if isinstance(category, str) else ""
        rows = {row["category"].casefold(): row for row in self.list() if row["category"]}
        record = rows.get(key) if key in allowed else None
        record = record or rows.get("general")
        if not record:
            raise ValueError("Upload and tag a resume for this industry or General in Documents.")
        self.w.docs.verify(record["id"])
        return record
