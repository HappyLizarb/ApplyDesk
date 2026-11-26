"""Map confirmed education into the observed Ashby education widget without inventing dates."""

import re
from datetime import datetime, timezone


def education_answer(field, profile, today=None):
    """Keep every saved degree distinct; current-study status never becomes an awarded degree."""
    if field.get("widget") != "ashby_education" or not profile.get("confirmed"):
        return None
    records = profile.get("education", [])
    if not records or any(not record.get("institution", "").strip() for record in records):
        return None
    today = today or datetime.now(timezone.utc).date()
    available = set(field.get("education_fields", []))
    values = [_education_value(record, available, today.strftime("%Y-%m")) for record in records]
    observed = field.get("value", [])
    for index, value in enumerate(values):
        school = observed[index].get("schoolName", "") if index < len(observed) else ""
        if " ".join(school.casefold().split()) == " ".join(value["schoolName"].casefold().split()):
            value["schoolName"] = school
    return {"field_id": field["field_id"], "value": values,
            "fact_ids": [f"education.{index}" for index in range(len(records))]}


def _education_value(record, available, current_month):
    """Omit unknown month precision; Ashby's current-student checkbox removes its end date."""
    result = {"schoolName": record["institution"].strip()}
    for source, target in (("degree", "degree"), ("field", "major")):
        if target in available and record.get(source, "").strip():
            result[target] = record[source].strip()
    start, end = record.get("start", ""), record.get("end", "")
    month = r"\d{4}-(?:0[1-9]|1[0-2])"
    if "startDate" in available and re.fullmatch(month, start):
        result["startDate"] = start
    current = bool(re.fullmatch(month, end) and end > current_month
                   and re.fullmatch(month, start) and start <= current_month)
    if "isCurrent" in available and (current or re.fullmatch(month, end) and end < current_month):
        result["isCurrent"] = current
    if "endDate" in available and re.fullmatch(month, end) and not current:
        result["endDate"] = end
    return result
