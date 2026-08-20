from __future__ import annotations

import sys
from typing import TextIO


class Report:
    """Structured terminal output for demo commands."""

    def __init__(self, *, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout
        self.next_lines: list[str] = []
        self.demo_ready = True

    def section(self, title: str) -> None:
        self.stream.write(f"\n== {title} ==\n")

    def kv(self, key: str, value: str) -> None:
        self.stream.write(f"  {key}: {value}\n")

    def line(self, text: str = "") -> None:
        self.stream.write(f"{text}\n")

    def block(self, text: str) -> None:
        for raw in text.rstrip().splitlines():
            self.stream.write(f"  {raw}\n")

    def warn(self, message: str) -> None:
        self.demo_ready = False
        if message not in self.next_lines:
            self.next_lines.append(message)

    def finish(self, *, compact: bool = False) -> int:
        if self.next_lines:
            self.stream.write("\nNEXT:\n")
            for item in self.next_lines:
                self.stream.write(f"  - {item}\n")
            return 1
        if not compact:
            self.stream.write("\nDemo-ready: yes\n")
        return 0
