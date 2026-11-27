"""An ordinary browser window simulated with stable native tab identities and no OS input."""

from types import SimpleNamespace
from applydesk.native_browser import BrowserError


class NativeWindowFixture:
    """Each helper retains the same fake Chrome window while independently observing stable tab IDs."""

    def __init__(self, redactions=None):
        self.process = SimpleNamespace(poll=lambda: None)
        self.ended, self.paused, self.interrupt_generation = False, False, 0
        self.options, self.calls = {"native": True, "headless": False}, []
        self.tabs, self.active, self.focused, self.url = ["tab-1"], "tab-1", False, "about:blank"
        self.counter, self.revision, self.native_generation = 1, 0, 0

    def call(self, method, params=None, expected_generation=None):
        """A stale Python generation can never reach this simulated native event loop."""
        params = params or {}
        if expected_generation != self.interrupt_generation:
            raise BrowserError("interrupted")
        if method == "action" and self.paused:
            raise BrowserError("stopped")
        self.calls.append((method, params))
        if method in {"use_existing", "attach"}:
            self.paused = False
            self.native_generation += 1
        if method == "action":
            self._action(params)
        page = self.page()
        if params.get("screenshot"):
            page["screenshot"] = {"mime_type": "image/png", "data": self.active, "width": 800, "height": 600}
        return page

    def open_public_url(self, url, generation):
        """Simulate Chrome receiving a URL in its existing profile and displaying a new tab."""
        if self.paused or self.interrupt_generation != generation:
            raise BrowserError("interrupted")
        self.calls.append(("open_public_url", {"url": url}))
        self.counter += 1
        self.active = f"tab-{self.counter}"
        self.tabs.append(self.active)
        self.url, self.focused = url, False
        self.revision += 1

    def _action(self, params):
        """Tab selection and address entry are observable key/click operations, never hidden navigation."""
        key = params.get("key")
        if key == "cmd+t":
            self.counter += 1
            self.active = f"tab-{self.counter}"
            self.tabs.append(self.active)
        if params.get("element_id") == "new-application-link":
            self.counter += 1
            self.active = f"tab-{self.counter}"
            self.tabs.append(self.active)
        if params.get("action") == "click" and params.get("element_id") in self.tabs:
            self.active = params["element_id"]
        if key == "cmd+l":
            self.focused = True
        if params.get("action") == "type":
            self.url = params["text"]
        if key == "enter":
            self.focused = False
        if key == "cmd+w":
            self.tabs.remove(self.active)
            self.active = self.tabs[-1] if self.tabs else None
        self.revision += 1

    def page(self):
        """Fixture geometry matches native absolute screen bounds before Python normalization."""
        return {"pid": 42, "window_id": 88, "snapshot_id": str(self.revision),
            "generation": self.native_generation, "title": "Fixture", "url": self.url, "text": "Visible fixture listing",
            "document_url": self.url if not self.focused else None,
            "bounds": {"x": 600, "y": 20, "width": 800, "height": 700},
            "web_bounds": {"x": 600, "y": 120, "width": 800, "height": 600},
            "tabs": [{"id": value, "selected": value == self.active} for value in self.tabs],
            "active_tab_id": self.active, "controls": [{"id": "address", "role": "AXTextField",
                "label": "Address and search bar", "web_content": False,
                "focused": self.focused, "secure": False, "value": self.url, "bounds": {"x": 620, "y": 60}}]}

    def interrupt(self):
        """Input authority changes without deleting or closing any native tab."""
        self.interrupt_generation += 1
        self.paused = True
        self.calls.append(("stop", {}))

    def close(self):
        """Only the helper ends; its ordinary Chrome tabs are retained."""
        self.ended = True
        self.paused = True
        self.calls.append(("close", {}))
