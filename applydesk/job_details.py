"""Copy explicitly labelled job sections and pay statements without generating missing content."""

import re

SECTION_NAMES = ("responsibilities", "about", "required_qualifications", "preferred_qualifications", "why_join_us")
HEADINGS = {
    "responsibilities": r"(?:key |primary |essential |main )?(?:responsibilities|duties|job duties)(?: and responsibilities)?|job duties consist of but are not limited to|what you(?:'|’)ll do|what you will do|your (?:role|responsibilities|impact)",
    "about": r"company (?:overview|description)|(?:job|position|role|team) (?:summary|overview|description)|about (?:the |our )?(?:team|company|role|position|opportunity|us)|who we are",
    "required_qualifications": r"(?:required|minimum|basic|essential)(?: qualifications| requirements| skills| experience)?|(?:additional )?requirements(?: (?:and|&) qualifications)?|qualifications(?: (?:&|and) (?:experience|requirements))?|qualifications \((?:required|minimum)\)|must[- ]haves?|what you(?:(?:'|’)ll| will)? bring|what we(?:'|’)re looking for",
    "preferred_qualifications": r"preferred(?: qualifications| requirements| skills| experience)?(?: \((?:nice[- ]to[- ]haves?|not required|optional)\))?|qualifications \(preferred\)|nice[- ]to[- ]haves?|bonus points|desired qualifications",
    "why_join_us": r"why join us|why work (?:here|with us)|what we offer|(?:our |company )?culture|benefits(?: and perks| package| you will enjoy(?: at .+)?)?",
}
PATTERNS = {key: re.compile(r"^(?:" + value + r")$", re.I) for key, value in HEADINGS.items()}
PAY_HEADING = re.compile(r"^(?:(?:annual|hourly|base|total|estimated|starting)\s+)*(?:compensation|salary|pay|wage)(?:\s+(?:range|package|description|information|transparency))*$", re.I)
STOP_HEADING = re.compile(
    r"^(?:about the job|job information|diversity(?:\s*(?:&|and)\s*inclusion)?|.*accommodations?|"
    r"(?:application|interview|hiring) process|how to apply|employment policy|privacy(?: notice| policy)?|"
    r"equal opportunity(?: employer| employment)?|benefits found in job post|requirements added by the job poster|"
    r"company|title|location|job location|reports to|classification|level|position type|education level|experience|travel|employer job page)$", re.I)
POLICY = re.compile(
    r"\b(?:equal opportunity|affirmative action|reasonable accommodations?|applicant privacy|privacy policy|"
    r"by submitting an application|applications are (?:reviewed|considered)|candidates may apply|"
    r"please (?:fill out|apply|submit)|to apply|apply via|fair chance|we do not discriminate)\b", re.I)
INLINE = re.compile(r"(?:^|\s+)((?:\*\*|__)?(?:required(?: qualifications)?|minimum qualifications|preferred(?: qualifications)?|responsibilities|benefits)(?:\*\*|__)?(?:[:：]|\s+[–—-]\s+)(?:\*\*|__)?)\s*", re.I)
PREFERRED_CUE = re.compile(r"\b(?:(?:is|are) preferred|nice[- ]to[- ]have|(?:is|are) (?:a plus|optional))\b", re.I)
REQUIRED_CUE = re.compile(r"\b(?:required|must|mandatory|necessary)\b", re.I)
PAY_WORDS = re.compile(r"\b(?:compensation|salary|salaries|pay|hourly rate|wages?|earnings|remuneration|signing bonus|sign-on bonus)\b", re.I)
MONEY = re.compile(r"(?:[$€£¥]\s*\d[\d,]*(?:\.\d+)?\s*[kKmM]?|\b(?:USD|EUR|GBP|CAD|AUD|NZD|CHF|SGD|HKD|JPY)\s*\d[\d,]*(?:\.\d+)?\s*[kKmM]?|\b\d[\d,]*(?:\.\d+)?\s*(?:USD|EUR|GBP|CAD|AUD|NZD|CHF|SGD|HKD|JPY)\b)")
PAY_RANGE = re.compile(r"\b(?:\d[\d,]{3,}|\d+(?:\.\d+)?[kK])\s*(?:-|–|—|to)\s*(?:\d[\d,]{3,}|\d+(?:\.\d+)?[kK])\b")
PAY_RATE = re.compile(r"\b\d[\d,]*(?:\.\d+)?[kK]?\s*(?:(?:per\s+|/)\s*(?:hr|hour|week|mo|month|yr|year|annum)|hourly|weekly|monthly|annually|yearly)\b", re.I)
PERIOD = re.compile(r"(?:/\s*(?:hr|hour|yr|year|mo|month)\b|\bper (?:hour|year|month|week)\b|\b(?:hourly|annually|annual|yearly|monthly|weekly)\b)", re.I)
NON_PAY = re.compile(r"\b(?:project budget|budgets?|revenue|sales target|project cost|401\s*\(?k\)?|retirement match|reimbursement)\b", re.I)
BUSINESS_AMOUNT = re.compile(r"\b(?:budgets?|revenue|sales target|project cost)\b", re.I)
YEAR_RANGE = re.compile(r"^(?:19|20)\d{2}\s*(?:-|–|—|to)\s*(?:19|20)\d{2}$")
LIST_PREFIX = re.compile(
    r"^[^\S\n]*(?:(?:[-*–—]|\d+[.)])[^\S\n]+|[•·∙●▪◦‣⁃■□○◆◇►▸\uf0b7\uf0a7][^\S\n]*)+", re.M)


def _label(value):
    """Normalize heading punctuation only; copied body text is never normalized."""
    label = value.strip().strip("#*_ \t:：")
    label = re.sub(r"^(?:\d+[.)]|[•●▪–—-])\s+", "", label)
    return re.sub(r"\s+", " ", label.strip("*_ \t")).strip()


def _classify(value):
    """Recognize explicit labels without guessing that title-cased body text is a heading."""
    label = _label(value)
    if STOP_HEADING.fullmatch(label):
        return "stop"
    if PAY_HEADING.fullmatch(label):
        return "pay"
    for section, pattern in PATTERNS.items():
        if pattern.fullmatch(label):
            return section
    if re.fullmatch(r"(?i:about) [A-Z][\w& .’'-]{0,55}", label):
        return "about"
    if re.fullmatch(r"[A-Z][\w& ’'-]{0,45} (?i:Culture)", label):
        return "why_join_us"
    return None


def _heading(line):
    """Split an explicit inline heading without altering the following wording."""
    raw = line.strip()
    parts = re.split(r"[:：]|\s+[–—-](?:\s+|$)", raw, maxsplit=1)
    category = _classify(parts[0])
    if not category:
        return ("stop", "") if re.match(r"^#{1,6}\s+\S", raw) else None
    body = parts[1].strip() if len(parts) == 2 else ""
    if re.match(r"^(?:#+\s*)?(?:\d+[.)]\s+)?(\*\*|__)", raw) and body.startswith(("**", "__")):
        body = body[2:].lstrip()
    return category, body


def _events(line):
    """Nested inline Required/Preferred labels are boundaries only within a labelled line."""
    heading = _heading(line)
    if not heading:
        return [(None, line)]
    category, body = heading
    events, start = [], 0
    for match in INLINE.finditer(body):
        events.append((category, body[start:match.start()].strip()))
        category, start = _heading(match.group(1))[0], match.end()
    events.append((category, body[start:].strip()))
    return events


def _sections(text):
    """Copy source-order blocks; policy and unrelated headings terminate the current section."""
    result = {key: [] for key in SECTION_NAMES}
    active, lines = None, []
    for line in text.split("\n"):
        for heading, content in _events(line):
            if heading or POLICY.search(content):
                if active and "\n".join(lines).strip():
                    result[active].append("\n".join(lines).strip())
                active, lines = heading if heading in result else None, []
                if POLICY.search(content):
                    active = None
            if active:
                if active == "required_qualifications" and PREFERRED_CUE.search(content):
                    if not REQUIRED_CUE.search(content):
                        result["preferred_qualifications"].append(content.strip())
                else:
                    lines.append(content)
    if active and "\n".join(lines).strip():
        result[active].append("\n".join(lines).strip())
    return {key: "\n\n".join(blocks) for key, blocks in result.items()}


def _pay_statement(block, labelled):
    """A money amount must describe pay, rather than experience, benefits percentages, or budgets."""
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])|\n", block):
        amount = MONEY.search(sentence) or PAY_RANGE.search(sentence) or PAY_RATE.search(sentence)
        if BUSINESS_AMOUNT.search(sentence) or (amount and YEAR_RANGE.fullmatch(amount.group())):
            continue
        pay = PAY_WORDS.search(sentence)
        if amount and (pay or (labelled and not NON_PAY.search(sentence)) or (MONEY.search(sentence) and PERIOD.search(sentence))):
            if not NON_PAY.search(sentence) or pay:
                return True
    return labelled and bool(re.search(r"\b(?:competitive|negotiable|commensurate|unpaid)\b", block, re.I))


def _pay_blocks(text):
    """Explicit headings split compact descriptions even when paragraphs have no blank lines."""
    lines = []
    for line in text.split("\n"):
        if not line.strip() or _heading(line):
            if lines:
                yield "\n".join(lines).strip()
                lines = []
        if line.strip():
            lines.append(line)
    if lines:
        yield "\n".join(lines).strip()


def _compensation(text):
    """Keep full pay paragraphs so base/total explanations and location-specific statements survive."""
    result, labelled, pending = [], False, ""
    for block in _pay_blocks(text):
        lines = block.split("\n")
        heading = _heading(lines[0])
        if heading:
            labelled = heading[0] == "pay"
            pending = block if labelled and not heading[1] and "\n" not in block else ""
        if _pay_statement(block, labelled):
            if not labelled:
                start = next(index for index, line in enumerate(lines) if _pay_statement(line, False))
                block = "\n".join(lines[start:])
            value = pending + "\n\n" + block if pending and pending != block else block
            if value not in result:
                result.append(value)
            labelled, pending = False, ""
        elif not heading:
            labelled, pending = False, ""
    return "\n\n".join(result)


def parse_job_details(text):
    """Remove list markers before either parser; retain wording and paragraph boundaries."""
    source = text.replace("\r\n", "\n").replace("\r", "\n") if isinstance(text, str) else ""
    source = LIST_PREFIX.sub("", source)
    return {"detail_sections": _sections(source), "compensation": _compensation(source)}
