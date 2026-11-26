"""Select trusted Workday guidance from the currently visible application step."""

from functools import lru_cache
from pathlib import Path
import re
from urllib.parse import urlsplit

SKILL = Path(__file__).parent / "skills" / "workday-apply" / "SKILL.md"
STAGES = {"my information": "My Information", "my experience": "My Experience",
          "application questions": "Application Questions", "voluntary disclosures": "Voluntary Disclosures",
          "self identify": "Self Identify", "self identification": "Self Identify", "review": "Review",
          "sign in": "Account", "create account": "Account", "start your application": "Start"}
FACT_KINDS = {
    "Start": set(), "Account": {"identity"},
    "My Information": {"identity", "websites", "application_source", "application_answer"},
    "My Experience": {"professional_experience", "education", "skill", "language", "websites", "application_answer"},
    # Employer questions vary; use confirmed facts once without also sending the raw profile sections.
    "Application Questions": None,
    "Voluntary Disclosures": {"disclosure"}, "Self Identify": {"disclosure", "identity"},
}


@lru_cache(maxsize=1)
def sections():
    """Load the packaged skill once and expose only its common and current-stage instructions."""
    parts = re.split(r"^## (.+)$", SKILL.read_text(encoding="utf-8"), flags=re.M)
    return {title: body.strip() for title, body in zip(parts[1::2], parts[2::2])}


def tenant_key(url):
    """Scope learned screen names to an HTTPS Workday employer host and career site."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or parsed.username or parsed.password or not host.endswith(".myworkdayjobs.com"):
        return None
    path = parsed.path.strip("/").split("/")
    if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", path[0]):
        path = path[1:]
    site = path[0] if path and re.fullmatch(r"[\w-]{1,100}", path[0]) else ""
    return host + "/" + site


def visible_stage(page):
    """Prefer an explicit current step; a progress list containing every step cannot select one."""
    current, headings, buttons = set(), set(), set()
    for item in page.get("elements", []):
        if not item.get("web_content", True):
            continue
        for raw in (item.get("label"), item.get("value")):
            if not isinstance(raw, str):
                continue
            label = " ".join(raw.casefold().replace("-", " ").split())
            marked = re.fullmatch(r"(?:current step(?: \d+ of \d+)?\s*[:,·]?\s+|step \d+ of \d+\s*[:,·]\s*)(.+)", label)
            stage = STAGES.get(marked[1] if marked else label)
            if stage and (marked or item.get("selected") is True):
                current.add(stage)
            elif stage and item.get("role") == "AXHeading":
                headings.add(stage)
            elif item.get("role") == "AXButton":
                buttons.add(label)
    evidence = current or headings
    if len(evidence) == 1:
        return next(iter(evidence))
    return "Start" if not evidence and "autofill with resume" in buttons else None


def adapt_context(context, store):
    """Route the same agent by visible stage and cache only known stage names for this tenant."""
    page = context["page"]
    tenant = tenant_key(page.get("url", ""))
    if tenant is None:
        return ""
    stage = visible_stage(page)
    with store.lock:
        known = store.get("workday_steps", tenant, [])
        if stage and stage not in known:
            known = [*known, stage]
            store.put("workday_steps", tenant, known)
    context["workday"] = {"current_step": stage, "observed_tenant_steps": known}
    if stage in FACT_KINDS:
        profile, kinds = context["profile"], FACT_KINDS[stage]
        context["profile"] = {"confirmed": profile.get("confirmed"), "facts": [
            fact for fact in profile.get("facts", []) if fact.get("confirmed") and (kinds is None or fact.get("kind") in kinds)]}
        context.pop("review_evidence", None)
    guide = sections()
    return "\n\n".join([guide["Workflow"], guide[stage]]) if stage else guide["Workflow"]
