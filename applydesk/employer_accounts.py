"""One durable applicant account per known employer, shared across its roles."""

import secrets
import uuid
from urllib.parse import urlsplit

from .account_scope import employer_key, https_origin
from .storage import now


class EmployerAccounts:
    """Serialize account reservations; passwords stay solely in the system Keychain."""

    def __init__(self, workspace):
        self.w = workspace
        self.store = workspace.store

    def _job(self, job):
        """Always use stored identity rather than a stale caller-supplied company name."""
        current = self.store.get("job", job["id"])
        if not current:
            raise ValueError("Application not found.")
        key = employer_key(current)
        if current.get("employer_account_id") not in {None, key}:
            raise ValueError("This application's employer changed. Review its account manually.")
        return current, key

    def _related(self, key):
        """Include removed applications so deleting a row never forgets a previous signup."""
        jobs = []
        for job in self.store.all("job"):
            try:
                if employer_key(job) == key:
                    jobs.append(job)
            except ValueError:
                continue
        return jobs

    def _legacy(self, job, key):
        """Adopt consistent old bindings without claiming that an earlier signup succeeded."""
        rows = [row for row in self._related(key)
                if row.get("account") or row.get("account_origin") or row.get("has_password")]
        if not rows:
            return None
        if any(not row.get("account") or not row.get("account_origin") for row in rows):
            raise ValueError("An existing employer account has no verified origin. Sign in manually.")
        identities = {(row["account"].strip(), https_origin(row["account_origin"])) for row in rows}
        if len(identities) != 1:
            raise ValueError("Conflicting accounts exist for this employer. Resolve them manually before continuing.")
        account, origin = identities.pop()
        record = self._new(job, key, account, origin, "existing_or_uncertain")
        record["legacy_ids"] = [row["id"] for row in rows]
        record["owner_job_id"] = rows[0]["id"]
        return record

    def _new(self, job, key, account, origin, status):
        """Random secret identifiers remain unique across separate local workspaces."""
        return {"id": key, "company": job["company"], "account": account, "origin": origin,
                "secret_key": "employer-account:" + uuid.uuid4().hex, "status": status,
                "owner_job_id": job["id"], "reuse_confirmed": False, "claimed_origins": [origin], "created_at": now()}

    def _password(self, record):
        """Migrate matching legacy secrets only after the destination has been validated."""
        password = self.w.secrets.get(record["secret_key"])
        if not record.get("legacy_ids"):
            return password
        existing = [self.w.secrets.get("account:" + identity) for identity in record["legacy_ids"]]
        values = {value for value in [password, *existing] if value}
        if len(values) > 1:
            raise ValueError("Conflicting saved passwords exist for this employer. Sign in manually.")
        password = next(iter(values), None)
        if password:
            self.w.secrets.set(record["secret_key"], password)
        record.pop("legacy_ids")
        record["has_password"] = bool(password)
        self.store.put("employer_account", record["id"], record)
        return password

    def credentials(self, job, origin, realm, action):
        """Reserve before any external write, and never retry an existing or uncertain signup."""
        with self.store.lock:
            job, key = self._job(job)
            record = self.store.get("employer_account", key) or self._legacy(job, key)
            if record and action == "create_account":
                raise ValueError("An account or signup attempt already exists for this employer. Use Sign in or account recovery; no second account will be created.")
            if record:
                self._check_scope(record, job, origin, realm)
                password = self._password(record)
            elif action == "create_account":
                record, password = self._reserve(job, key, origin, realm)
            else:
                raise ValueError("Save this employer's existing account in the application row, or sign in manually.")
            if not password:
                raise ValueError("Enter this employer account's password in the application row, or sign in manually.")
            record.update(realm=realm, has_password=True)
            self.store.put("employer_account", key, record)
            self._attach(job, record)
            return record["account"], password

    def _check_scope(self, record, job, origin, realm):
        """A path is an extra boundary, not proof of a shared recruiting site's tenant."""
        if record["origin"] != origin or record.get("realm") not in {None, realm}:
            raise ValueError("The employer's sign-in website or account scope changed. Sign in manually.")
        if job["id"] != record["owner_job_id"] and not record.get("reuse_confirmed"):
            raise ValueError("An existing employer account was found. Save that account once to confirm reuse across roles, or sign in manually.")
        conflicts = [item for item in self.store.all("employer_account")
                     if item["id"] != record["id"] and item.get("realm") == realm]
        if conflicts:
            raise ValueError("This sign-in page is shared by another employer identity. Sign in manually.")

    def _reserve(self, job, key, origin, realm):
        """A persisted reservation survives crashes, failed fills, and uncertain HTTP responses."""
        if any(item.get("realm") == realm or origin in {*item.get("claimed_origins", []), item.get("origin")}
               for item in self.store.all("employer_account")):
            raise ValueError("An employer account already uses this sign-in website. Verify the existing account manually.")
        if any(row.get("account_origin") and https_origin(row["account_origin"]) == origin
               for row in self.store.all("job") if row.get("account") or row.get("has_password")):
            raise ValueError("A previous employer account uses this website. Reuse it or sign in manually.")
        if self.w.secrets.get("account:" + job["id"]):
            raise ValueError("This saved password has no verified origin. Save the account again or sign in manually.")
        account = self.w.profile()["identity"].get("email", "").strip()
        if not account:
            raise ValueError("Add your account email before creating an employer account.")
        record = self._new(job, key, account, origin, "creation_attempted")
        record.update(realm=realm, attempted_at=now())
        self.store.put("employer_account", key, record)
        password = secrets.token_urlsafe(24)
        self.w.secrets.set(record["secret_key"], password)
        return record, password

    def save(self, data):
        """Explicitly confirm one existing account for all roles at this employer."""
        parsed = urlsplit(str(data.get("account_origin", "")))
        origin = https_origin(str(data.get("account_origin", "")))
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Enter the exact HTTPS sign-in origin, without a path, credentials, or query.")
        account = str(data.get("account", "")).strip()
        if not account or len(account) > 500:
            raise ValueError("Enter the employer account email or username.")
        with self.store.lock:
            job, key = self._job(data)
            record = self.store.get("employer_account", key) or self._legacy(job, key)
            if record and record["account"] != account:
                raise ValueError("This employer already has a different saved account. Reuse it or resolve the account manually.")
            if record and record["origin"] != origin and not data.get("password"):
                raise ValueError("Enter the password again when changing the sign-in origin.")
            record = record or self._new(job, key, account, origin, "saved")
            return self._save_password(job, record, origin, data.get("password"))

    def _save_password(self, job, record, origin, password):
        """Keep the registry even if Keychain fails; saving is not a successful external login."""
        record["claimed_origins"] = sorted({*record.get("claimed_origins", []), record["origin"], origin})
        if record["origin"] != origin:
            record.pop("realm", None)
            record.update(secret_key="employer-account:" + uuid.uuid4().hex, has_password=False)
            record.pop("legacy_ids", None)
        record.update(origin=origin, reuse_confirmed=True, status="saved")
        self.store.put("employer_account", record["id"], record)
        if password:
            self.w.secrets.set(record["secret_key"], str(password))
            record.pop("legacy_ids", None)
            record["has_password"] = True
        else:
            record["has_password"] = bool(self._password(record))
        self.store.put("employer_account", record["id"], record)
        for related in self._related(record["id"]):
            self._attach(related, record)
        return self.store.get("job", job["id"])

    def _attach(self, job, record):
        """Application rows reference the shared account without copying its secret."""
        return self.store.job(job["id"], employer_account_id=record["id"], account=record["account"],
                              account_origin=record["origin"], has_password=record.get("has_password", False))

    def display(self, job):
        """Show the same saved account on newly discovered roles without reading Keychain."""
        try:
            record = self.store.get("employer_account", employer_key(job))
        except ValueError:
            return job
        if not record:
            return job
        return {**job, "employer_account_id": record["id"], "account": record["account"],
                "account_origin": record["origin"], "has_password": record.get("has_password", False)}
