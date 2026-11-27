"""Synthetic historical records from the former recommendation importer."""

import hashlib


def saved_listing(*, company="Example Labs", title="Software Engineer", text=None,
                  employer_url="https://careers.example.test/jobs/123", complete=True):
    """Build an old stored row independently of the current native browser."""
    text = "Required: Python and testing experience. " * 6 if text is None else text
    description = f"Company: {company}\nTitle: {title}\nLocation: Boston\n\n{text.strip()}"
    return {"platform": "linkedin", "source_url": "https://www.linkedin.com/jobs/view/123/",
            "job_id": "123", "company": company, "title": title, "location": "Boston",
            "description": description, "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
            "captured_at": "2024-01-02T10:00:00+00:00", "employer_url": employer_url,
            "complete": complete, "issues": [] if complete else ["Incomplete listing."]}


def public_source(url="https://careers.example.test/opening?id=101"):
    """Use a complete synthetic posting whose URL need not resemble a particular ATS."""
    description = ("Example Co Analyst. Entry hardware role in Boston. Full-time new-grad position. "
                   "Python required. Responsibilities include testing electronic hardware and documenting laboratory results.")
    return {"url": url, "company": "Example Co", "title": "Analyst", "description": description,
            "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
            "captured_at": "2026-11-12T01:00:00+00:00", "complete": True}
