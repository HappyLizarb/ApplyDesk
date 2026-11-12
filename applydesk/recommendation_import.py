"""Store selected source captures using the same identity and removal rules everywhere."""

from .discovery import canonical_url
from .recommendation_source import known_identity_matches, source_identity


def duplicate(workspace, source):
    """Stable source IDs beat titles; only untouched incomplete rows can be repaired."""
    jobs = workspace.store.all("job")
    protected = ("analysis", "resume_id", "cover_id", "account", "account_origin", "has_password", "live_url", "attachments", "last_snapshot", "history")
    for job in jobs:
        prior = job.get("recommendation_source", {})
        untouched = (job["status"] in {"new", "needs_review"} and job["id"] not in workspace.sessions
                     and not job.get("removed_at") and not any(job.get(key) for key in protected)
                     and workspace.tasks.get("prepare:" + job["id"], {}).get("status") != "running")
        if (prior.get("platform"), prior.get("job_id")) == (source["platform"], source["job_id"]):
            return job, untouched and prior.get("complete") is False and known_identity_matches(prior, source)
        try:
            source_identity(source["platform"], job["url"], source["job_id"])
            return job, "recommendation_source" not in job and job["status"] == "new" and untouched
        except ValueError:
            pass
    employer = canonical_url(source["employer_url"]) if source["employer_url"] else None
    for job in jobs:
        prior = job.get("recommendation_source", {})
        if employer and any(canonical_url(url) == employer for url in (job["url"], prior.get("employer_url")) if url):
            return job, False
    return None, False


def import_selected(workspace, records, report):
    """The caller holds workspace/store locks after verifying the current search request."""
    report = {**report, "job_ids": list(report.get("job_ids", [])), "added_job_ids": list(report.get("added_job_ids", []))}
    for source in records:
        if source["complete"] is not True:
            continue
        existing, reusable = duplicate(workspace, source)
        if existing and not reusable:
            count = "excluded_count" if existing.get("removed_at") else "existing_count"
            report[count] = report.get(count, 0) + 1
            if not existing.get("removed_at"):
                report["job_ids"].append(existing["id"])
            continue
        job = existing or workspace.store.add_job(source["source_url"])
        workspace.store.job(job["id"], url=source["source_url"], company=source["company"], title=source["title"], location=source["location"],
                            details=source["description"], recommendation_source=source, qualification_status="unverified", status="new",
                            exceptions=source["issues"] + ([] if source["employer_url"] else ["Employer link not available; application needs manual review."]),
                            progress="Matching recommendation · qualifications not checked")
        report["job_ids"].append(job["id"])
        if existing:
            report["updated_count"] = report.get("updated_count", 0) + 1
        else:
            report["added_job_ids"].append(job["id"])
    report["job_ids"] = list(dict.fromkeys(report["job_ids"]))
    report["added_count"] = len(report["added_job_ids"])
    return report
