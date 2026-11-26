"""Synthetic listings and browser responses for saved-source tests."""

def listing(identity="123", platform="linkedin", **changes):
    """Known source URLs and invented descriptions keep these tests entirely local."""
    url = f"https://www.linkedin.com/jobs/view/{identity}/" if platform == "linkedin" else f"https://app.joinhandshake.com/job-search/{identity}"
    return {"id": identity, "url": url, "title": "Software Engineer", "company": "Example Labs",
            "location": "San Francisco", "text": "Required: Python and testing experience. " * 6,
            "employer_url": f"https://careers.example.test/jobs/{identity}", "complete": True, "issues": [], **changes}


def response(status="ready", platform="linkedin", **changes):
    """The mock retains the controlled browser protocol rather than a page snapshot."""
    return {"source": platform, "status": status, "jobs": [], "issues": [], **changes}
