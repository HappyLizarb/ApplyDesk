"""Synthetic listings and explicit selection for isolated recommendation tests."""

from applydesk.recommendation_import import import_selected


def listing(identity="123", platform="linkedin", **changes):
    """Known source URLs and invented descriptions keep these tests entirely local."""
    url = f"https://www.linkedin.com/jobs/view/{identity}/" if platform == "linkedin" else f"https://app.joinhandshake.com/job-search/{identity}"
    return {"id": identity, "url": url, "title": "Software Engineer", "company": "Example Labs",
            "location": "San Francisco", "text": "Required: Python and testing experience. " * 6,
            "employer_url": f"https://careers.example.test/jobs/{identity}", "complete": True, "issues": [], **changes}


def response(status="ready", platform="linkedin", **changes):
    """The mock retains the controlled browser protocol rather than a page snapshot."""
    return {"source": platform, "status": status, "jobs": [], "issues": [], **changes}


def accept_captured(case, platform="linkedin"):
    """Only bookkeeping tests approve every synthetic capture; relevance has separate tests."""
    report = case.finished(platform)
    records = case.w.store.get("recommendation_candidates", platform, {}).get("records", [])
    with case.w.lock, case.w.store.lock:
        report = import_selected(case.w, records, report)
        case.w.store.put("recommendation", platform, report)
    return report
