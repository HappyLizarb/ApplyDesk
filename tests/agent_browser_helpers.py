"""Synthetic browser contracts shared by deterministic and generic pipeline tests."""

import copy
import hashlib
import json
from pathlib import Path

from applydesk.applications import Applications
from applydesk.browser_bridge import BrowserError


def legacy_application(*args, **options):
    """Preserve deterministic-driver regression coverage while production selects agent browsing."""
    if options.pop("agent_driven", False) is not True:
        raise AssertionError("Production application runs must select agent browsing.")
    return Applications(*args, **options, agent_driven=False)


class GenericBrowserFixture:
    """Expose unfamiliar observed controls and independently read uploaded fixture bytes."""

    def __init__(self, job, confirmed=True):
        self.methods, self.confirmed, self.closed = [], confirmed, False
        field = {"tag": "input", "visible": True, "disabled": False, "required": True, "form_id": "application"}
        self.page = {"url": job["url"], "title": "Example Co Analyst", "text": job["public_source"]["description"],
            "elements": [{**field, "id": "identity", "type": "text", "label": "Applicant identity", "value": ""},
                         {**field, "id": "attachment", "type": "file", "label": "Your CV", "files": [], "accept": ".pdf"},
                         {**field, "id": "finish", "tag": "button", "type": "submit", "label": "Send", "required": False,
                          "context": "Example Co Analyst application", "value": None}]}

    def call(self, method, params=None):
        """The fake browser records actual controller methods, not the model's claimed outcome."""
        self.methods.append(method)
        if method in {"open", "agent_observe"}:
            return self.observe()
        if method == "agent_action":
            return self.act(params)
        if method == "agent_submit":
            if params["snapshot_id"] != self.observe()["snapshot_id"]:
                raise BrowserError("The final page changed.")
            return {"confirmed": self.confirmed, "attempt_id": params["attempt_id"],
                    "receipt": "Your application was submitted" if self.confirmed else ""}
        raise AssertionError("Generic pipeline used an unexpected browser method: " + method)

    def observe(self):
        """Unchanged observations have an identical identity for fresh-review verification."""
        page = copy.deepcopy(self.page)
        page["snapshot_id"] = hashlib.sha256(json.dumps(page, sort_keys=True).encode()).hexdigest()
        return page

    def act(self, params):
        """Only selected existing controls can change, and uploaded bytes are independently hashed."""
        element = next((item for item in self.page["elements"] if item["id"] == params["element_id"]), None)
        if not element:
            raise BrowserError("The observed control changed. Inspect the page again.")
        if params["action"] == "fill":
            element["value"] = params["value"]
            return self.observe()
        if params["action"] != "upload":
            raise AssertionError("Unexpected generic fixture action")
        upload = params["upload"]
        path = Path(upload["path"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        element["files"] = [{"name": path.name, "size": path.stat().st_size, "sha256": digest}]
        self.page["text"] += "\nUploaded: " + path.name
        verified = {"sha256": digest, "files": copy.deepcopy(element["files"]),
                    "upload_confirmed": False, "selected_file_verified": True}
        return {**self.observe(), "result": {"upload": verified}}

    def close(self):
        """Workspace cleanup marks only this isolated fixture as closed."""
        self.closed = True
        self.methods.append("close")
