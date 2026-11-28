"""Native I/O contracts use synthetic accessibility pages and temporary original documents only."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chat_helpers import selected_fixture
from applydesk.documents import DocumentService
from applydesk.account_scope import employer_key
from applydesk.native_application_io import NativeInputRequired, _account_controls, authenticate, upload
from applydesk.native_application_browser import normalize_observation
from applydesk.native_navigation import clear_upload_picker, track_upload_picker
from applydesk.native_evidence import observation, public_page
from applydesk.native_browser import NativeBrowser
from applydesk.storage import Store


def control(identity, role="AXButton", label="", **extra):
    """Every test target is a visible, enabled native AX control with explicit geometry."""
    return {"id": identity, "role": role, "label": label, "enabled": True, "secure": False,
            "bounds": {"x": 20, "y": 20, "width": 80, "height": 20}, **extra}


def dialog(identity, parent=None):
    """Native ancestry is separate from webpage text imitating an operating-system dialog."""
    return {"id": identity, "role": "AXSheet", "native": True, "parent_dialog_id": parent,
            "bounds": {"x": 0, "y": 0, "width": 500, "height": 400}}


def upload_pages():
    """The picker, Go-to-Folder entry and completed application are distinct observed states."""
    base = {"snapshot_id": "start", "text": "Example Co Upload resume", "dialogs": [],
            "elements": [control("upload", label="Upload resume")]}
    picker = {"snapshot_id": "picker", "text": "Open Cancel", "dialogs": [dialog("picker")], "elements": [
        control("files", "AXOutline", "Files", parent_dialog_id="picker"),
        control("open", label="Open", parent_dialog_id="picker"), control("cancel", label="Cancel", parent_dialog_id="picker")]}
    folder = {"snapshot_id": "folder", "text": "Go to Folder", "dialogs": [dialog("picker"), dialog("folder", "picker")],
              "elements": [*picker["elements"], control("path", "AXTextField", "", parent_dialog_id="folder", focused=True, picker_path_field=True),
                           control("close", parent_dialog_id="folder"), control("suggestions", "AXTable", parent_dialog_id="folder")]}
    typed = {**folder, "snapshot_id": "typed", "action_result": {"typed": True, "private_value_matches": True}}
    selected = {**picker, "snapshot_id": "selected", "text": "resume.pdf Open Cancel"}
    finished = {**base, "snapshot_id": "uploaded", "text": "Example Co Resume: resume.pdf"}
    return [base, picker, folder, typed, selected, finished]


def native_session(pages):
    """Use actual native response decoding and normalization instead of inventing upload proof fields."""
    responses, transport, session = iter(pages), NativeBrowser(), Mock()
    def call(method, parameters):
        page = copy.deepcopy(next(responses))
        page["controls"] = page.pop("elements")
        if parameters.get("private"):
            transport.redactions.add(parameters["text"])
        method = {"native_observe": "observe", "native_action": "action"}[method]
        result = transport._result({"result": page}, method, parameters, transport.interrupt_generation)
        return normalize_observation(result)
    session.call.side_effect = call
    return session


class NativeIOTests(unittest.TestCase):
    """No native process or personal browser is opened by these tests."""

    def test_file_picker_contents_stay_out_of_model_and_review_history(self):
        """The backend can identify Open while the agent sees only a chooser ID."""
        _, picker, *_ = upload_pages()
        picker.update(native_modal=True, text="unrelated-folder.pdf", screenshot={"data": "private-image"})
        picker["elements"][0]["value"] = "unrelated-folder.pdf"
        visible, history = public_page(picker), observation(picker)
        self.assertEqual(visible["dialogs"], [{"id": "picker", "kind": "file_picker"}])
        self.assertNotIn("screenshot", visible)
        self.assertNotIn("unrelated-folder.pdf", repr((visible, history)))

    def test_next_navigation_can_cancel_its_interrupted_upload(self):
        """Cancel only the same observed path sheet and Open picker after resuming browser work."""
        _, picker, folder, _, selected, done = [
            dict(p, native_modal=bool(p["dialogs"]), active_tab_id="owned") for p in upload_pages()]
        owner = track_upload_picker(picker, {"key": "cmd+shift+g"}, None)
        owner = track_upload_picker(folder, {"action": "type", "element_id": "path", "private": True}, owner)
        manager = Mock(tabs={"job": "owned"}, upload_picker=owner)
        manager._action.side_effect = [selected, done]
        self.assertEqual(clear_upload_picker(manager, folder, 7), done)
        events = [call.args[0] for call in manager._action.call_args_list]
        self.assertEqual(events, [{"action": "key", "key": "escape"}, {"action": "click", "element_id": "cancel"}])
        self.assertIsNone(manager.upload_picker)

    def test_unknown_or_replaced_chrome_dialog_receives_no_recovery_input(self):
        """An unrelated OS sheet, a different tab or a helper restart must require user intervention."""
        _, picker, folder, *_ = upload_pages()
        for owner in (None, ("other-tab", "picker", "folder"), ("owned", "picker", "previous-sheet")):
            manager = Mock(tabs={"job": "owned"}, upload_picker=owner)
            view = dict(folder, native_modal=True, active_tab_id="owned")
            with self.assertRaisesRegex(RuntimeError, "dialog is open"):
                clear_upload_picker(manager, view, 7)
            manager._action.assert_not_called()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "resume.pdf"
        self.source.write_bytes(b"original resume fixture")
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.store = Store(Path(self.temp.name) / "store")
        self.job = {"id": "job", "url": "https://careers.example.test/job/1", "company": "Example Co", "resume_id": "resume"}
        self.store.put("job", "job", self.job)
        self.profile = {"confirmed": True, "revision": 4, "identity": {"email": "example@applicant.test"}, "transcript_id": "transcript"}
        self.secrets = {}
        vault = Mock(get=lambda key: self.secrets.get(key), set=lambda key, value: self.secrets.__setitem__(key, value))
        docs = DocumentService(Path(self.temp.name) / "documents")
        selected_fixture(docs, "resume", self.source)
        self.workspace = SimpleNamespace(store=self.store, secrets=vault, profile=lambda: self.profile, docs=docs)
        self.upload_decision = {"element_id": "upload", "document_id": "resume", "kind": "resume"}

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def account_page(self):
        return {"snapshot_id": "account", "url": "https://careers.example.test/accounts/create", "text": "Example Co Create account",
                "elements": [control("email", "AXTextField", "Email", value=""),
                             control("password", "AXTextField", "Password", secure=True, value=None), control("submit", label="Create account")]}

    def account_session(self, page):
        session, state = Mock(), copy.deepcopy(page)
        def call(method, params):
            if method == "native_action":
                element = next(item for item in state["elements"] if item["id"] == params["element_id"])
                if params["action"] == "type" and not element.get("secure"):
                    element["value"] = params["text"]
                state["snapshot_id"] += "+"
            elif state["snapshot_id"].endswith("+++"):
                state.update(text="Candidate Home", elements=[])
            return copy.deepcopy(state)
        session.call.side_effect = call
        return session

    def test_original_resume_uses_native_picker_and_reports_only_supported_evidence(self):
        pages = upload_pages()
        self.assertNotIn("private_value_matches", pages[3])
        session = native_session(pages)
        page, evidence = upload(session, self.workspace, self.job, self.profile, pages[0], self.upload_decision)
        operations = [item.args[1] for item in session.call.call_args_list if item.args[0] == "native_action"]
        self.assertEqual([item["action"] for item in operations], ["click", "key", "type", "key", "click"])
        self.assertEqual(operations[1]["key"], "cmd+shift+g")
        self.assertEqual((operations[2]["text"], operations[-1]["approved_file"], operations[-1]["picker_id"]), (str(self.source), str(self.source), "picker"))
        self.assertTrue(operations[2]["private"])
        self.assertEqual(operations[3]["element_id"], "path")
        self.assertEqual(evidence["sha256"], self.digest)
        self.assertTrue(evidence["source_verified"])
        self.assertFalse(evidence["browser_bytes_verified"])
        self.assertFalse(evidence["upload_confirmed"])
        self.assertEqual(evidence["visible_filename"], "resume.pdf")
        self.assertEqual(evidence["element_id"], "upload")
        self.assertNotIn(self.temp.name, json.dumps([page, evidence]))

    def test_existing_native_picker_after_web_notice_uses_the_original_file_action(self):
        """Resume an already-open sheet without a second file-dialog launch or a manual file selection."""
        pages = upload_pages()[1:]
        decision = dict(self.upload_decision, element_id="picker")
        session = native_session(pages)
        _, evidence = upload(session, self.workspace, self.job, self.profile, pages[0], decision)
        actions = [call.args[1] for call in session.call.call_args_list if call.args[0] == "native_action"]
        self.assertEqual(actions[0]["key"], "cmd+shift+g")
        self.assertEqual(evidence["sha256"], self.digest)
        self.assertEqual(evidence["visible_filename"], "resume.pdf")
        with self.assertRaisesRegex(ValueError, "observed native upload"):
            upload(native_session(pages), self.workspace, self.job, self.profile, pages[0], self.upload_decision)

    def test_fake_web_picker_never_receives_a_path(self):
        pages = upload_pages()
        pages[1]["dialogs"][0]["native"] = False
        session = native_session(pages[:2])
        with patch("applydesk.native_application_io.time.monotonic", side_effect=[0, 6]):
            with self.assertRaisesRegex(ValueError, "file picker"):
                upload(session, self.workspace, self.job, self.profile, pages[0], self.upload_decision)
        self.assertFalse(any(item.args[1].get("action") == "type" for item in session.call.call_args_list))

    def test_unverified_folder_never_receives_a_path(self):
        """A path can be typed only into one new child of the original native picker."""
        for mode in ("wrong_parent", "old", "two_dialogs", "two_fields"):
            pages = upload_pages()
            if mode == "wrong_parent":
                pages[2]["dialogs"][1]["parent_dialog_id"] = "other"
            elif mode == "old":
                pages[1]["dialogs"].append(dialog("folder", "picker"))
            elif mode == "two_dialogs":
                pages[2]["dialogs"].append(dialog("second", "picker"))
                pages[2]["elements"].append(control("second-path", "AXTextField", parent_dialog_id="second"))
            else:
                pages[2]["elements"].append(control("second-path", "AXTextField", parent_dialog_id="folder"))
            session = native_session(pages[:3])
            with self.subTest(mode=mode), patch("applydesk.native_application_io.time.monotonic", side_effect=[0, 0, 6]):
                with self.assertRaisesRegex(NativeInputRequired, "file picker.*List view"):
                    upload(session, self.workspace, self.job, self.profile, pages[0], self.upload_decision)
            self.assertFalse(any(item.args[1].get("action") == "type" for item in session.call.call_args_list))

    def test_inline_folder_input_needs_go_and_cannot_be_plain_picker_search(self):
        """An inline Go layout remains supported; a search field without that evidence is rejected."""
        for has_go, path_field in ((True, True), (False, True), (True, False)):
            pages = upload_pages()
            for index in (2, 3):
                pages[index]["dialogs"] = [dialog("picker")]
                pages[index]["elements"] = [*pages[1]["elements"],
                    control("path", "AXTextField", parent_dialog_id="picker", focused=True, picker_path_field=path_field)]
                if has_go:
                    pages[index]["elements"].append(control("go", label="Go", parent_dialog_id="picker"))
            session = native_session(pages)
            with self.subTest(has_go=has_go, path_field=path_field):
                if has_go and path_field:
                    self.assertEqual(upload(session, self.workspace, self.job, self.profile, pages[0], self.upload_decision)[1]["visible_filename"], "resume.pdf")
                else:
                    with patch("applydesk.native_application_io.time.monotonic", side_effect=[0, 0, 6]), self.assertRaisesRegex(ValueError, "file picker"):
                        upload(session, self.workspace, self.job, self.profile, pages[0], self.upload_decision)
                    self.assertFalse(any(item.args[1].get("action") == "type" for item in session.call.call_args_list))

    def test_changed_source_and_wrong_document_stop_before_picker_click(self):
        pages = upload_pages()
        session = Mock()
        session.call.return_value = pages[0]
        self.source.write_bytes(b"changed bytes")
        with self.assertRaisesRegex(ValueError, "resume changed"):
            upload(session, self.workspace, self.job, self.profile, pages[0], self.upload_decision)
        self.assertEqual(session.call.call_count, 1)
        with self.assertRaises(NativeInputRequired):
            upload(session, self.workspace, self.job, self.profile, pages[0], {**self.upload_decision, "document_id": "other"})
        with self.assertRaises(NativeInputRequired):
            upload(session, self.workspace, self.job, self.profile, pages[0],
                   {**self.upload_decision, "kind": "transcript", "document_id": self.profile["transcript_id"]})
        self.assertTrue(all(call.args[0] == "native_observe" for call in session.call.call_args_list))

    def test_unverified_native_path_never_reaches_return_or_open(self):
        """Neither failed private readback nor a lost path-field focus permits Return or Open."""
        for failure in ("readback", "missing", "focus"):
            pages = upload_pages()
            if failure == "readback":
                pages[3]["action_result"]["private_value_matches"] = False
            elif failure == "missing":
                pages[3]["action_result"].pop("private_value_matches")
            else:
                next(item for item in pages[3]["elements"] if item["id"] == "path")["focused"] = False
            session = native_session(pages)
            with self.subTest(failure=failure), self.assertRaisesRegex(ValueError, "document path was not confirmed"):
                upload(session, self.workspace, self.job, self.profile, pages[0], self.upload_decision)
            operations = [item.args[1] for item in session.call.call_args_list if item.args[0] == "native_action"]
            self.assertEqual([item["action"] for item in operations], ["click", "key", "type"])

    def test_account_creation_reserves_keychain_secret_and_uses_secure_native_typing(self):
        page = self.account_page()
        session = self.account_session(page)
        decision = {"mode": "create_account", "username_id": "email", "password_ids": ["password"], "submit_id": "submit"}
        result, evidence = authenticate(session, self.workspace, self.job, self.profile, page, decision)
        account = self.store.get("employer_account", employer_key(self.job))
        self.assertEqual(account["status"], "creation_attempted")
        self.assertEqual(account["realm"], page["url"])
        secret = self.secrets[account["secret_key"]]
        password_call = session.call.call_args_list[2].args[1]
        self.assertEqual(password_call["text"], secret)
        self.assertTrue(password_call["private"])
        self.assertTrue(session.call.call_args_list[-2].args[1]["final_permit"])
        self.assertEqual(result["text"], "Candidate Home")
        self.assertNotIn(secret, json.dumps([result, evidence]))
        self.assertTrue(evidence["attempted"])
        self.assertFalse(evidence["confirmed"])

    def test_scope_and_nonsecure_fields_are_rejected_before_credentials_are_read(self):
        page = self.account_page()
        decision = {"mode": "sign_in", "username_id": "email", "password_ids": ["password"], "submit_id": "submit"}
        self.store.put("employer_account", employer_key(self.job), {"origin": "https://other.example.test", "realm": "https://other.example.test/login"})
        with self.assertRaises(NativeInputRequired):
            authenticate(self.account_session(page), self.workspace, self.job, self.profile, page, decision)
        page["elements"][1]["secure"] = False
        with self.assertRaisesRegex(ValueError, "ordinary account"):
            authenticate(self.account_session(page), self.workspace, self.job, self.profile, page, decision)
        self.assertEqual(self.secrets, {})

    def test_implicit_account_terms_continue_but_password_change_needs_input(self):
        decision = {"mode": "create_account", "username_id": "email", "password_ids": ["password"], "submit_id": "submit"}
        page = self.account_page()
        page["text"] += "\nI agree to the terms of service."
        self.assertEqual(_account_controls(page, decision)[2]["id"], "submit")
        page = self.account_page()
        page["elements"][-1]["label"] = "Change password"
        with self.assertRaisesRegex(NativeInputRequired, "Password changes"):
            authenticate(self.account_session(page), self.workspace, self.job, self.profile, page, decision)
        self.assertEqual(self.secrets, {})
if __name__ == "__main__":
    unittest.main()
