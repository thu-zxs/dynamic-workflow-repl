from __future__ import annotations

import tempfile
import unittest
from typing import Any
from pathlib import Path
from unittest.mock import patch

from dynamic_workflows_agent.agents import WorkerAgent
from dynamic_workflows_agent.models import Finding
from dynamic_workflows_agent.tools import ToolRegistry, _parse_bing_rss_results, _parse_duckduckgo_results
from dynamic_workflows_agent.validator import validate_workflow_plan


class ToolRegistryTests(unittest.TestCase):
    def test_file_tools_are_workspace_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
            registry = ToolRegistry(workspace_root=root)

            listed = registry.run("list_files", {"root": "."})
            self.assertTrue(listed.ok)
            self.assertEqual(listed.data["files"][0]["path"], "notes.txt")

            read = registry.run("read_file", {"path": "notes.txt"})
            self.assertTrue(read.ok)
            self.assertIn("alpha", read.data["content"])

            escaped = registry.run("read_file", {"path": "../outside.txt"})
            self.assertFalse(escaped.ok)
            self.assertIn("escapes workspace", escaped.error)

    def test_search_files_returns_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
            registry = ToolRegistry(workspace_root=root)

            result = registry.run("search_files", {"pattern": "hello", "root": "."})

            self.assertTrue(result.ok)
            self.assertEqual(result.data["matches"][0]["path"], "app.py")
            self.assertEqual(result.data["matches"][0]["line"], 1)

    def test_duckduckgo_parser_handles_html_result_shape(self) -> None:
        page = """
        <div class="result results_links results_links_deep web-result">
          <a class="result__a" rel="nofollow"
             href="/l/?kh=-1&amp;uddg=https%3A%2F%2Fexample.com%2Farticle%3Fx%3D1">
             Example <b>Title</b>
          </a>
          <div class="result__snippet">A <b>sample</b> snippet.</div>
        </div>
        """

        results = _parse_duckduckgo_results(page, max_results=3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Example Title")
        self.assertEqual(results[0]["url"], "https://example.com/article?x=1")
        self.assertEqual(results[0]["snippet"], "A sample snippet.")

    def test_bing_rss_parser_returns_results(self) -> None:
        feed = """<?xml version="1.0" encoding="utf-8" ?>
        <rss version="2.0"><channel>
          <item>
            <title>Example &amp; Result</title>
            <link>https://example.com/result</link>
            <description>A useful result.</description>
            <pubDate>Fri, 03 Jul 2026 13:01:00 GMT</pubDate>
          </item>
        </channel></rss>
        """

        results = _parse_bing_rss_results(feed, max_results=3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Example & Result")
        self.assertEqual(results[0]["url"], "https://example.com/result")
        self.assertEqual(results[0]["snippet"], "A useful result.")
        self.assertEqual(results[0]["published"], "Fri, 03 Jul 2026 13:01:00 GMT")

    def test_web_search_falls_back_to_bing_rss(self) -> None:
        class FakeRssResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b"""<?xml version="1.0" encoding="utf-8" ?>
                <rss version="2.0"><channel>
                  <item><title>Fallback Result</title><link>https://example.com</link>
                  <description>From RSS.</description></item>
                </channel></rss>
                """

        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(workspace_root=tmp)
            with patch(
                "dynamic_workflows_agent.tools.urllib.request.urlopen",
                side_effect=[TimeoutError("ddg timeout"), TimeoutError("ddg timeout"), FakeRssResponse()],
            ):
                result = registry.run("web_search", {"query": "openai", "max_results": 3})

        self.assertTrue(result.ok)
        self.assertEqual(result.data["source"], "https://www.bing.com/search")
        self.assertEqual(result.data["results"][0]["title"], "Fallback Result")

    def test_web_search_does_not_treat_unparseable_page_as_success(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b"<html><body>unexpected DuckDuckGo page</body></html>"

        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(workspace_root=tmp)
            with patch("dynamic_workflows_agent.tools.urllib.request.urlopen", return_value=FakeResponse()):
                result = registry.run("web_search", {"query": "openai", "max_results": 3})

        self.assertFalse(result.ok)
        self.assertIn("DuckDuckGo returned", result.error)

    def test_finding_accepts_tool_results(self) -> None:
        finding = Finding.from_dict(
            {
                "id": "F-T1",
                "subtask_id": "T1",
                "agent_role": "reader",
                "claim": "Read the file.",
                "evidence": ["Tool result available."],
                "confidence": 0.8,
                "tool_results": [
                    {
                        "name": "read_file",
                        "arguments": {"path": "README.md"},
                        "ok": True,
                        "summary": "read README.md",
                        "data": {"path": "README.md", "content": "hello"},
                    }
                ],
            }
        )

        self.assertEqual(finding.tool_results[0]["name"], "read_file")


class BadToolResultLLM:
    async def chat_json_with_tools(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        tools: list[dict[str, Any]],
        run_tool,
        max_tokens: int = 4096,
        max_tool_rounds: int = 4,
    ):
        return (
            {
                "id": "F-T1",
                "subtask_id": "T1",
                "agent_role": "reader",
                "claim": "Completed without trusting model-supplied tool results.",
                "evidence": ["The malformed tool_results field was ignored."],
                "confidence": 0.8,
                "tool_results": ["not an object"],
            },
            [],
        )

    async def chat_json(self, **kwargs):
        raise AssertionError("chat_json should not be called when tools are enabled")


class WorkerToolResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_ignores_malformed_model_tool_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = validate_workflow_plan(
                {
                    "goal": "Read files",
                    "success_criteria": ["return finding"],
                    "subtasks": [
                        {
                            "id": "T1",
                            "title": "Read",
                            "agent_role": "reader",
                            "prompt": "Read.",
                            "depends_on": [],
                        },
                        {
                            "id": "T2",
                            "title": "Check",
                            "agent_role": "checker",
                            "prompt": "Check.",
                            "depends_on": [],
                        },
                        {
                            "id": "T3",
                            "title": "Refute",
                            "agent_role": "refuter",
                            "prompt": "Refute.",
                            "depends_on": [],
                        },
                    ],
                    "parallel_groups": [{"id": "G1", "subtask_ids": ["T1", "T2", "T3"], "max_concurrency": 3}],
                    "verification_steps": [
                        {
                            "id": "V1",
                            "target_subtask_ids": ["T1", "T2", "T3"],
                            "mode": "refute",
                            "prompt": "Verify.",
                        }
                    ],
                }
            )
            worker = WorkerAgent(BadToolResultLLM(), tool_registry=ToolRegistry(workspace_root=tmp))

            finding = await worker.run(plan=plan, subtask=plan.subtasks[0], dependency_findings=[])

            self.assertEqual(finding.tool_results, [])


if __name__ == "__main__":
    unittest.main()
