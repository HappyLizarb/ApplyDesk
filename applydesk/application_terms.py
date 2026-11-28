"""Identify employer terms from the visible account or application form."""

import re


ASSENT = re.compile(r"\b(agree(?:ment)?|accept|abide|comply|acknowledg\w*|consent|authorize|certify|declare|terms|agreement|privacy policy|recruitment notice|electronic signature)\b", re.I)
SUBJECT = re.compile(r"\b(terms|agreement|arbitration|class.action waiver|privacy|notice|polic(?:y|ies)|background|screening|reference checks?|"
                     r"code of conduct|conditions of (?:employment|application)|restricted.part(?:y|ies)|interpol|release of liability|electronic signature|accurate|accuracy|truthful|certify (?:these|my|the) answers)\b", re.I)
OPTIONAL = re.compile(r"\b(optional|marketing|newsletter|promotional|talent (?:community|pool)|text messages|job alerts|future (?:opportunities|openings))\b|"
                      r"\b(interview|audio|video).{0,30}\b(record\w*|transcri\w*)|"
                      r"\b(record\w*|transcri\w*).{0,30}\b(interview|audio|video)", re.I)


# A positive answer applies only to the full union of events and institutions in the profile field.
APPLICANT = r"(?:(?:have you (?:ever |previously )?been|were you(?: ever| previously)?) )?"
TERMINATION = r"(?:terminated|fired|dismissed|discharged)"
RESIGNATION = r"(?:asked|requested) to resign"
ALTERNATIVE = r"\s*,?\s*(?:and/or|or|/)\s*(?:been )?"
BOTH_EVENTS = rf"(?:{TERMINATION}{ALTERNATIVE}{RESIGNATION}|{RESIGNATION}{ALTERNATIVE}{TERMINATION})"
ARTICLE = r"(?:(?:any|an?|any prior|any previous) )?"
WORK = ARTICLE + r"(?:jobs?|employment|employers?|places? of employment|positions?)"
SCHOOL = ARTICLE + r"(?:(?:academic|educational) institutions?|schools?)"
BOTH_DOMAINS = rf"(?:{WORK}{ALTERNATIVE}{SCHOOL}|{SCHOOL}{ALTERNATIVE}{WORK})"
END = r"\s*[.*:]*\s*$"
DISCIPLINE = re.compile(APPLICANT + BOTH_EVENTS + r",? from " + BOTH_DOMAINS + END, re.I)
# A negative answer also rules out a subset, such as an employment termination for cause.
NARROW_DISCIPLINE = re.compile(APPLICANT + rf"(?:{BOTH_EVENTS}|{TERMINATION}|{RESIGNATION}),? from "
                              + rf"(?:{BOTH_DOMAINS}|{WORK}|{SCHOOL})"
                              + r"(?: for (?:cause|misconduct)| (?:in|within) the (?:past|last) \d+ years?)?" + END, re.I)
PERSONAL = re.compile(r"\b(?:citizen\w*|immigration status|security clearance|sponsorship|years of experience|relatives?|"
                      r"previously employed|years? old|(?:at least|over|under) \d+|convict\w*|felon\w*|"
                      r"terminat(?:ed|ion)|asked to resign|disciplinary action)\b", re.I)


def disciplinary_question(wording, value):
    """Release a saved answer for the applicant's matching employment or academic disclosure."""
    if value not in {"yes", "no"} or not isinstance(wording, str):
        return False
    patterns = (DISCIPLINE, NARROW_DISCIPLINE) if value == "no" else (DISCIPLINE,)
    return any(pattern.fullmatch(line.strip(" *:")) for line in re.split(r"[\n?!;]|(?<=\.)\s+", wording) for pattern in patterns)


def covers(wording):
    """Separate actual employer terms from optional choices and applicant statements."""
    if not isinstance(wording, str) or not ASSENT.search(wording) or not SUBJECT.search(wording):
        return False
    decline = r"\b(decline|disagree|opt out|(?:do not|don['’]t|cannot) (?:agree|accept|consent|authorize|acknowledge))\b"
    return not (OPTIONAL.search(wording) or PERSONAL.search(wording) or re.search(decline, wording, re.I))


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
