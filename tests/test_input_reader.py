from __future__ import annotations

import tempfile
import unittest

from dynamic_workflows_agent.input_reader import ReadlineInputReader, create_input_reader


class InputReaderTests(unittest.TestCase):
    def test_create_input_reader_can_force_plain_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reader = create_input_reader(
                history_path=f"{tmp}/history",
                commands=["/help"],
                prefer_prompt_toolkit=False,
            )

            self.assertIn(reader.backend_name, {"readline", "input"})

    def test_readline_reader_records_backend_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reader = ReadlineInputReader(history_path=f"{tmp}/history", commands=["/help"])

            self.assertIn(reader.backend_name, {"readline", "input"})


if __name__ == "__main__":
    unittest.main()
