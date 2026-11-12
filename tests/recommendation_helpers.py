"""Explicit selection for isolated duplicate/import tests, without a provider call."""

from applydesk.recommendation_import import import_selected


def accept_captured(case, platform="linkedin"):
    """Only bookkeeping tests approve every synthetic capture; relevance has separate tests."""
    report = case.finished(platform)
    records = case.w.store.get("recommendation_candidates", platform, {}).get("records", [])
    with case.w.lock, case.w.store.lock:
        report = import_selected(case.w, records, report)
        case.w.store.put("recommendation", platform, report)
    return report
