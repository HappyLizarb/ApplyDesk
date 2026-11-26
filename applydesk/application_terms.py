"""Identify employer terms from the visible account or application form."""

import re


ASSENT = re.compile(r"\b(agree(?:ment)?|accept|acknowledg\w*|consent|authorize|certify|declare|terms|agreement|privacy policy|recruitment notice|electronic signature)\b", re.I)
SUBJECT = re.compile(r"\b(terms|agreement|arbitration|class.action waiver|privacy|notice|polic(?:y|ies)|background|screening|reference checks?|"
                     r"restricted.part(?:y|ies)|interpol|release of liability|electronic signature|accurate|accuracy|truthful|certify (?:these|my|the) answers)\b", re.I)
OPTIONAL = re.compile(r"\b(optional|marketing|newsletter|promotional|talent (?:community|pool)|text messages|job alerts|future (?:opportunities|openings))\b|"
                      r"\b(interview|audio|video).{0,30}\b(record\w*|transcri\w*)|"
                      r"\b(record\w*|transcri\w*).{0,30}\b(interview|audio|video)", re.I)


def covers(wording):
    """Separate actual employer terms from optional choices and applicant statements."""
    if not isinstance(wording, str) or not ASSENT.search(wording) or not SUBJECT.search(wording):
        return False
    personal = r"\b(?:citizen\w*|immigration status|security clearance|sponsorship|years of experience|relatives?)\b"
    decline = r"\b(decline|disagree|opt out|(?:do not|don['’]t|cannot) (?:agree|accept|consent|authorize|acknowledge))\b"
    return not (OPTIONAL.search(wording) or re.search(personal, wording, re.I) or re.search(decline, wording, re.I))


def unresolved_declarations(text, pattern):
    """Only separate applicant declarations need evidence beyond accepting employer terms."""
    return [line for line in text.splitlines() if pattern.search(line) and not covers(line)]


def visible_terms(page, control):
    """Recognize a terms checkbox even when its accessible name is only a required asterisk."""
    label = str(control.get("label") or "").strip(" *:.").casefold()
    if covers(label):
        return True
    area = control.get("bounds") or {}
    if label not in {"", "required", "yes", "agree", "i agree", "accept", "i accept", "i consent"} or not area:
        return False
    x, y = area["x"], area["y"]
    wording = []
    for item in page.get("elements", []):
        box = item.get("bounds") or {}
        if item is control or item.get("id") == control.get("id") or not box or not item.get("web_content", True):
            continue
        if not (x - 40 <= box["x"] <= x + 400 and box["y"] <= y + area.get("height", 0) + 12
                and box["y"] + box.get("height", 0) >= y - 76):
            continue
        if item.get("role") in {"AXCheckBox", "AXRadioButton"}:
            return False
        if item.get("role") in {"AXStaticText", "AXLink", "AXHeading"}:
            wording.append(str(item.get("value") or item.get("label") or ""))
    notice = " ".join(wording)
    return bool((label == "i consent" or re.search(r"\b(i |by (?:creating|applying|submitting)|must (?:agree|accept))", notice, re.I))
                and covers(" ".join((notice, label))))
