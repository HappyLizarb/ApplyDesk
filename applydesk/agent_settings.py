"""One agent uses the model exposed by the explicitly selected connection."""

from .agent_contract import object_fields

ROLES = {"master": "ApplyDesk Agent"}


class AgentSettings:
    """Keep the former master selection; obsolete role settings are never used."""

    def __init__(self, workspace, accounts):
        self.w, self.accounts = workspace, accounts

    def read(self):
        """Defaults use the first account-advertised model and its default reasoning behavior."""
        account = self.accounts.state()
        model = account["models"][0]["id"] if account["models"] else ""
        saved = self.w.store.get("agent_settings", account["active"], {})
        return {"roles": {role: saved.get("roles", {}).get(role, {"model": model, "thinking": "default"}) for role in ROLES},
                "automatic": saved.get("automatic", True)}

    def save(self, data):
        """Reject unsupported effort values and unknown roles before any inference begins."""
        object_fields(data, ["roles", "automatic"])
        object_fields(data["roles"], ROLES)
        if type(data["automatic"]) is not bool:
            raise ValueError("Automatic processing must be enabled or disabled.")
        for selection in data["roles"].values():
            self.validate(selection)
        active = self.accounts.state()["active"]
        if not active:
            raise ValueError("Connect an account before saving agent settings.")
        self.w.store.put("agent_settings", active, data)
        return self.read()

    def validate(self, selection):
        """Account catalogs are choices, not proof that a particular request will succeed."""
        object_fields(selection, ["model", "thinking"])
        model = next((row for row in self.accounts.state()["models"] if row["id"] == selection["model"]), None)
        if not model or selection["thinking"] not in ["default", *model["thinking"]]:
            raise ValueError("Choose an available model and supported thinking strength in the model menu.")
        return selection

    def save_role(self, data):
        """Repair one role without replacing or validating unrelated saved model choices."""
        object_fields(data, ["role", "selection"])
        role = data["role"]
        if not isinstance(role, str) or role not in ROLES:
            raise ValueError("Choose the ApplyDesk agent.")
        with self.accounts.lock:
            selection = self.validate(data["selection"])
            active = self.accounts.state()["active"]
            if not active:
                raise ValueError("Connect an account before saving agent settings.")
            settings = self.read()
            settings["roles"][role] = selection.copy()
            self.w.store.put("agent_settings", active, settings)
            return self.read()
