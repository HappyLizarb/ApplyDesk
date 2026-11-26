"""Map saved degrees to Workday's expected-year form without claiming graduation."""

import re


def _normalized(value):
    """Ignore typography without treating a broad substring as a matching discipline."""
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def study_candidates(value):
    """Use the named program and explicit components, never a different academic specialty."""
    cleaned = re.sub(r"\s*\(honou?rs\)\s*$", "", value, flags=re.I).strip()
    candidates = [value.strip(), cleaned]
    candidates.extend(part.strip() for part in re.split(r"\s*(?:&|\band\b)\s*", cleaned, flags=re.I))
    for candidate in list(candidates):
        if re.search(r"\sengineering science$", candidate, re.I):
            candidates.append(candidate[:-8].strip())
        if re.search(r"\ssciences$", candidate, re.I):
            candidates.append(candidate[:-1])
    queries = list(dict.fromkeys(candidate for candidate in candidates if candidate))
    # An omitted component could match a second, different catalog choice.
    return queries if len(queries) <= 6 else list(dict.fromkeys((value.strip(), cleaned)))


def _degree(value):
    """Map recognized degree names to a Workday category without inferring completion."""
    normalized = _normalized(value)
    if re.fullmatch(r"bachelor of (?:arts|engineering|science)|bachelors|bachelor s degree|bs|ba|bsc", normalized):
        return "Bachelors"
    return value.strip()


def _education_value(record, observed):
    """Retain only a live canonical choice supported by the saved program's named components."""
    year = lambda value: value[:4] if re.fullmatch(r"\d{4}(?:-(?:0[1-9]|1[0-2]))?", value) else ""
    source = record.get("field", "").strip()
    selected = observed.get("fieldOfStudy", "")
    choices = {_normalized(candidate) for candidate in study_candidates(source)}
    field = selected if selected and _normalized(selected) in choices else source
    gpa, scale = record.get("gpa", "").strip(), record.get("gpa_scale", "").strip()
    return {"schoolName": record.get("institution", "").strip(), "degree": _degree(record.get("degree", "")),
            "fieldOfStudy": field, "gradeAverage": f"{gpa}/{scale}" if gpa and scale else gpa,
            "firstYearAttended": year(record.get("start", "")), "lastYearAttended": year(record.get("end", ""))}


def workday_education_answer(field, profile):
    """Every profile degree remains distinct; absent required dates remain unresolved."""
    if field.get("widget") != "workday_education" or not profile.get("confirmed"):
        return None
    records, observed = profile.get("education", []), field.get("value", [])
    if not records or field.get("end_date_kind") != "actual_or_expected":
        return None
    values = [_education_value(record, observed[index] if index < len(observed) else {})
              for index, record in enumerate(records)]
    required = ["schoolName", "degree", "fieldOfStudy", "firstYearAttended", "lastYearAttended"]
    if any(not value[key] for value in values for key in required):
        return None
    return {"field_id": field["field_id"], "value": values,
            "fact_ids": [f"education.{index}" for index in range(len(records))]}
