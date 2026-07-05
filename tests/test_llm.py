from __future__ import annotations

import unittest
from typing import Any

from dynamic_workflows_agent.llm import DeepSeekClient


class JsonRepairDeepSeekClient(DeepSeekClient):
    def __init__(self) -> None:
        super().__init__(api_key="test", max_retries=1, json_repair_retries=1)
        self.bodies: list[dict[str, Any]] = []

    def _post_chat(self, *, body: dict[str, Any], schema_name: str) -> dict[str, Any]:
        self.bodies.append(body)
        if len(self.bodies) == 1:
            return {"choices": [{"message": {"content": '{"id":"F-T14","claim":"truncated",'}}]}
        return {"choices": [{"message": {"content": '{"id":"F-T14","claim":"repaired"}'}}]}


class LLMTests(unittest.TestCase):
    def test_deepseek_retries_when_json_is_truncated(self) -> None:
        client = JsonRepairDeepSeekClient()

        result = client._chat_json_sync(
            system="Return JSON only.",
            user="Produce a finding.",
            schema_name="finding",
            max_tokens=100,
        )

        self.assertEqual(result["claim"], "repaired")
        self.assertEqual(len(client.bodies), 2)
        self.assertGreater(client.bodies[1]["max_tokens"], 100)
        repair_prompt = client.bodies[1]["messages"][-1]["content"]
        self.assertIn("not a complete valid JSON object", repair_prompt)
        self.assertIn("Return exactly one complete JSON object", repair_prompt)


if __name__ == "__main__":
    unittest.main()
