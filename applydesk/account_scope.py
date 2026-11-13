"""Conservative employer and sign-in boundaries for saved applicant accounts."""

import hashlib
import re
from urllib.parse import urlsplit


def employer_key(job):
    """Match spelling exactly except for case and whitespace; never guess company aliases."""
    company = " ".join(str(job.get("company") or "").split()).casefold()
    if company in {"", "unknown", "unknown company", "not assessed", "company", "employer", "n/a"}:
        raise ValueError("Identify the employer before saving or creating an applicant account.")
    return hashlib.sha256(company.encode()).hexdigest()


def https_origin(url):
    """Credential scope is the exact HTTPS host and port, never a hostname suffix."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Credentials require an HTTPS page and form destination without embedded credentials.")
    host = parsed.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    return "https://" + host + (f":{parsed.port}" if parsed.port and parsed.port != 443 else "")


def sign_in_scope(job, snapshot, button):
    """Require visible employer evidence and retain the exact authentication path boundary."""
    employer_key(job)
    company = " ".join(job["company"].split()).casefold()
    visible = " ".join(str(snapshot.get("text") or "").split()).casefold()
    if not re.search(r"(?<!\w)" + re.escape(company) + r"(?!\w)", visible):
        raise ValueError("The sign-in page does not identify this employer. Sign in manually.")
    action = urlsplit(button["form_action"])
    if action.query or action.fragment or "%" in action.path or ".." in action.path.split("/"):
        raise ValueError("The employer's sign-in scope is ambiguous. Sign in manually.")
    parent = action.path.rstrip("/").rsplit("/", 1)[0] + "/"
    return https_origin(button["form_action"]) + parent
