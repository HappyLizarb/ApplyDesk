"""Research conversations can finish multi-page work and remain cancellable."""

import json
from unittest.mock import patch

from chat_helpers import ChatFixture
from test_orchestrator import response


class ResearchRoutingTests(ChatFixture):
    """Tool sequences use synthetic responses without provider or browser calls."""

    def test_search_can_continue_past_eight_rounds(self):
        engine = self.w.orchestrator
        outputs = [(str(index), response(calls=[{"type": "function_call", "name": "read_page",
                                                "call_id": str(index), "arguments": json.dumps({"url": f"https://example.test/{index}"})}])) for index in range(12)]
        outputs.append(("done", response("Saved the selected jobs.")))
        with patch.object(engine, "run_agent", side_effect=outputs) as agent:
            with patch.object(engine.tools, "call", return_value={"jobs": []}) as tool:
                result = engine._master([{"role": "user", "content": "Research matching roles."}])
        self.assertEqual(result, "Saved the selected jobs.")
        self.assertEqual(tool.call_count, 12)
        self.assertEqual(agent.call_count, 13)

    def test_repeated_page_reuses_capture_without_removing_tool_access(self):
        """Repeated reads reuse the browser capture and let the agent choose its next step."""
        engine = self.w.orchestrator
        steps = []
        def model(*_args, **kwargs):
            self.assertTrue(kwargs.get("tools"))
            steps.append(True)
            if len(steps) == 5:
                return "done", response("The page has no further results.")
            return "step", response(calls=[{"type": "function_call", "name": "read_page",
                                            "call_id": "read", "arguments": '{"url":"https://example.test/jobs"}'}])
        with patch.object(engine, "run_agent", side_effect=model) as agent:
            with patch.object(engine.tools, "call", return_value={"capture_id": "original", "text": "No results"}) as tool:
                result = engine._master([{"role": "user", "content": "Find roles."}])
        self.assertEqual(result, "The page has no further results.")
        tool.assert_called_once()
        self.assertEqual(agent.call_count, 5)

    def test_progress_resumes_when_a_tool_result_changes(self):
        """Repeated polling can continue when it brings new information."""
        from applydesk.orchestrator_tools import ToolProgress
        values = iter([{"status": "waiting"}] * 3 + [{"status": "complete", "jobs": ["one"]}])
        progress = ToolProgress(lambda _: next(values))
        for _ in range(4):
            result = progress.execute({"name": "workspace_overview", "arguments": "{}"})
        self.assertNotIn("progress_note", result)
        self.assertEqual(result["jobs"], ["one"])

    def test_explicit_refresh_is_available_when_a_page_has_changed(self):
        """The agent can request fresh content instead of being restricted to cached reads."""
        from applydesk.orchestrator_tools import ToolProgress
        values = iter([{"text": "old"}, {"text": "new"}])
        progress = ToolProgress(lambda _: next(values))
        first = progress.execute({"name": "read_page", "arguments": '{"url":"https://example.test/jobs"}'})
        second = progress.execute({"name": "refresh_page", "arguments": '{"url":"https://example.test/jobs"}'})
        self.assertEqual((first["text"], second["text"]), ("old", "new"))
        self.assertEqual(progress.execute({"name": "read_page", "arguments": '{"url":"https://example.test/jobs"}'})["text"], "new")

    def test_stop_interrupts_a_research_sequence(self):
        engine = self.w.orchestrator
        def model(*_args, **_kwargs):
            engine.cancelled.set()
            return "stopped", response(calls=[{"type": "function_call", "name": "read_page",
                                                "call_id": "one", "arguments": '{"url":"https://example.test"}'}])
        with patch.object(engine, "run_agent", side_effect=model):
            with patch.object(engine.tools, "call") as tool:
                with self.assertRaises(InterruptedError):
                    engine._master([{"role": "user", "content": "Research roles."}])
        tool.assert_not_called()
