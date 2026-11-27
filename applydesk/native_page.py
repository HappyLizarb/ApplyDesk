"""Collect public page evidence by scrolling the visible Chrome viewport."""

import time
import re
from urllib.parse import urlsplit

from .native_browser import BrowserError
from .discovery import canonical_url
from .workday_skill import tenant_key

HARD_FLAGS = ("text_capped", "controls_truncated", "elements_truncated", "render_unsettled")


def return_to_listing(session, job, page):
    """Reopen only this verified posting from the same employer's Workday candidate or search page."""
    source = job.get("public_source") or {}
    target, current = source.get("url") or "", page.get("url") or ""
    tenant = tenant_key(target)
    if (not source.get("complete") or source.get("company") != job.get("company") or not tenant or tenant.endswith("/")
            or tenant_key(current) != tenant or urlsplit(current).netloc != urlsplit(target).netloc
            or "/job/" not in urlsplit(target).path or page.get("native_modal") or page.get("reading_notice")):
        raise ValueError("Return to the saved listing from its employer's candidate or search page.")
    # Workday keeps its careers-site name before the optional userHome or jobs page.
    path = [part for part in urlsplit(current).path.split("/") if part]
    if path and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", path[0]):
        path = path[1:]
    if len(path) != 1 and not (len(path) == 2 and path[-1].casefold() in {"userhome", "candidatehome", "jobs", "search"}):
        raise ValueError("Return to the saved listing from its employer's candidate or search page.")
    return session.call("open", {"url": target})


def capture_page(session, page, limit=32):
    """Read every displayed section; a bounded incomplete capture is never a complete listing."""
    if not page.get("native"):
        return page
    page = _move_view(session, page)
    original, lines, links, previous = page, [], {}, None
    unreadable, readable = set(), set()
    complete, incomplete, repeated = False, False, 0
    diagnostics = {"viewports": 0, "stop_reason": "viewport_limit", "hard_flags": [], "unreadable_controls": []}
    for _ in range(limit):
        signature = _visible_signature(page)
        repeated = repeated + 1 if signature == previous else 0
        previous = signature
        incomplete |= _record_readability(page, unreadable, readable)
        _record_diagnostics(page, diagnostics, unreadable)
        _append_visible(lines, page.get("text", "").splitlines())
        for link in page.get("links", []):
            links[(link.get("text"), link.get("href", link.get("url")))] = link
        if repeated >= 2:
            complete, diagnostics["stop_reason"] = True, "bottom_reached"
            break
        if len("\n".join(lines)) > 60000:
            diagnostics["stop_reason"] = "text_limit"
            break
        page = _move_view(session, page, scroll=True)
        if canonical_url(page.get("url", "")) != canonical_url(original.get("url", "")):
            raise ValueError("The page navigated while its visible description was being read.")
    try:
        current = _move_view(session, page)
    except BrowserError as error:
        # Returning to the top is for presentation. Keep a completed read if that key was never sent.
        if getattr(error, "code", None) != "stale_observation" or not complete:
            raise
        current = session.call("native_observe")
        identity = ("pid", "window_id", "generation", "active_tab_id")
        if (current.get("native_modal") or any(page.get(key) is None or current.get(key) != page[key] for key in identity)
                or canonical_url(current.get("url", "")) != canonical_url(original.get("url", ""))):
            raise ValueError("The posting changed after its description was read.") from None
    return {**current, "text": "\n".join(lines)[:60000], "links": list(links.values()),
            "text_truncated": not complete or incomplete or bool(unreadable), "visible_capture": True,
            "screenshot_needed": bool(unreadable), "unreadable_control_ids": sorted(unreadable),
            "capture_diagnostics": diagnostics}


def _record_readability(page, unreadable, readable):
    """A boundary-clipped control can be recovered later; actual caps and missing evidence stay incomplete."""
    for control in page.get("elements", []):
        identity = control.get("id")
        if not identity or not control.get("web_content", True):
            continue
        if control.get("text_incomplete"):
            if identity not in readable:
                unreadable.add(identity)
        elif not control.get("clipped") and (control.get("value") or control.get("label")):
            unreadable.discard(identity)
            readable.add(identity)
    return bool(_hard_flags(page))


def _hard_flags(page):
    """Name irrecoverable capture flags without confusing recoverable clipped controls with missing evidence."""
    flags = [key for key in HARD_FLAGS if page.get(key)]
    identified = any(item.get("id") and item.get("web_content", True) and item.get("text_incomplete")
                     for item in page.get("elements", []))
    if page.get("text_truncated") and not identified:
        flags.append("text_truncated_without_control")
    return flags


def _record_diagnostics(page, diagnostics, unreadable):
    """Retain only capture state and unresolved native control geometry, never page text or field values."""
    diagnostics["viewports"] += 1
    diagnostics["hard_flags"] = sorted(set(diagnostics["hard_flags"]) | set(_hard_flags(page)))
    controls = {item["id"]: item for item in diagnostics["unreadable_controls"]}
    for item in page.get("elements", []):
        identity = item.get("id")
        if identity not in unreadable or not item.get("web_content", True):
            continue
        controls[identity] = {"id": identity, "role": item.get("role"),
            "bounds": {key: item["bounds"][key] for key in ("x", "y", "width", "height") if key in item.get("bounds", {})},
            "clipped": bool(item.get("clipped")), "viewport": diagnostics["viewports"]}
    diagnostics["unreadable_controls"] = [controls[identity] for identity in sorted(unreadable) if identity in controls]


def incomplete_capture_message(page, message):
    """Explain the capture failure plainly; native diagnostic details stay in the structured capture."""
    diagnostics = page.get("capture_diagnostics")
    if not diagnostics:
        return message
    if diagnostics.get("stop_reason") in {"viewport_limit", "text_limit"}:
        reason = "The page could not be read completely within this scan."
    elif "render_unsettled" in diagnostics.get("hard_flags", []):
        reason = "The page was still changing while being read."
    else:
        reason = "Some visible text could not be read."
    return message + " " + reason


def _visible_signature(page):
    """Visible links distinguish otherwise identical sections without reading hidden page structure."""
    links = tuple((row.get("text"), row.get("href", row.get("url"))) for row in page.get("links", []))
    return page.get("text", ""), links


def _move_view(session, page, *, scroll=False):
    """Reset or overlap visible viewports, then wait for actual text observations to settle."""
    binding = {key: page.get(key) for key in ("pid", "window_id", "generation", "active_tab_id")}
    url = canonical_url(page.get("url", ""))
    page = _viewport_action(session, page, scroll=scroll)
    previous, quiet = _visible_signature(page), 0
    # A second bounded observation pass recovers transient reads without repeating input or waiving evidence.
    for _ in range(12):
        time.sleep(0.1)
        fresh = session.call("native_observe")
        if any(fresh.get(key) != value for key, value in binding.items()):
            raise ValueError("The page or browser tab changed while its visible description was settling.")
        if not fresh.get("url") and url:
            quiet = 0
            continue
        if canonical_url(fresh.get("url", "")) != url:
            raise ValueError("The posting URL changed while its visible description was settling.")
        signature = _visible_signature(fresh)
        quiet = quiet + 1 if signature == previous else 0
        page, previous = fresh, signature
        if quiet >= 2:
            return page
    return {**page, "render_unsettled": True}


def _viewport_action(session, page, *, scroll=False):
    """Retry only a known unsent viewport action after rereading the same visible page."""
    original = canonical_url(page.get("url", ""))
    for attempt in range(3):
        action = {"action": "key", "key": "home"}
        if scroll:
            height = page.get("viewport", {}).get("height", 0)
            if not isinstance(height, (int, float)) or height < 80:
                raise ValueError("The visible browser viewport could not be measured. Show the page and try again.")
            action = {"action": "scroll", "delta_y": min(600, int(height * 0.7)), "delta_x": 0}
        try:
            result = session.call("native_action", {"snapshot_id": page["snapshot_id"], **action})
        except BrowserError as error:
            if getattr(error, "code", None) != "stale_observation" or attempt == 2:
                raise
            time.sleep(0.1)
            page = session.call("native_observe")
            if canonical_url(page.get("url", "")) != original:
                raise ValueError("The page navigated while its visible description was being read.")
            continue
        if result.get("url") and canonical_url(result["url"]) != original:
            raise ValueError("The page navigated while its visible description was being read.")
        return result


def _append_visible(lines, incoming):
    """Remove only the overlap between successive viewports, retaining observed wording."""
    incoming = [line for line in incoming if line.strip()]
    maximum = min(len(lines), len(incoming))
    overlap = next((size for size in range(maximum, 0, -1) if lines[-size:] == incoming[:size]), 0)
    lines.extend(incoming[overlap:])


def public_page(session, url, *, listing_only=False):
    """Open the requested destination and capture its rendered description without DOM access."""
    if listing_only:
        parsed = urlsplit(url)
        # These public forms share an explicit job identity with their listing but omit its qualifications.
        if (parsed.hostname in {"jobs.lever.co", "jobs.ashbyhq.com"}
                and re.fullmatch(r"/[^/]+/[\da-fA-F]{8}(?:-[\da-fA-F]{4}){3}-[\da-fA-F]{12}/(?:apply|application)/?", parsed.path)):
            url = canonical_url(url)
    options = {"url": url}
    if listing_only:
        options["listing_only"] = True
    page = session.call("open", options)
    try:
        captured = capture_page(session, page)
    except BrowserError as error:
        if not listing_only or str(error) != "The action changed application tabs. Observe the intended job again.":
            raise
        # A tab switch during the public listing read discards the partial capture and restarts on the original tab.
        restored = session.call("native_observe")
        identity = ("pid", "window_id", "generation", "active_tab_id", "url")
        if restored.get("native_modal") or any(page.get(k) is None or restored.get(k) != page[k] for k in identity):
            raise
        captured = capture_page(session, restored)
    scan = captured.get("capture_diagnostics", {})
    if (listing_only and scan.get("hard_flags") == ["render_unsettled"] and scan.get("stop_reason") == "bottom_reached"
            and not captured.get("unreadable_control_ids") and captured.get("text_truncated")):
        # A public listing can finish changing after the first scan. Reread the same tab from scratch once.
        fresh = session.call("native_observe")
        identity = ("pid", "window_id", "generation", "active_tab_id", "url")
        if (not fresh.get("native_modal") and
                all(captured.get(key) is not None and fresh.get(key) == captured[key] for key in identity)):
            captured = capture_page(session, fresh)
    return {**captured, "has_open_draft": True} if page.get("has_open_draft") else captured
