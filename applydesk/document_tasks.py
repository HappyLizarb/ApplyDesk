"""Local integrity checks for unchanged application attachments."""


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
