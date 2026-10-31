"""Typed, allow-listed tool registry exposed to the local LLM."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
import json


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    capability_id: str = ""
    category: str = "General"

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Only registered handlers can ever execute; the model cannot run Python."""
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Duplicate tool: {spec.name}")
        self._tools[spec.name] = spec

    def definitions(self, names=None) -> list[dict[str, Any]]:
        """Return schemas only for the requested allow-listed tools.

        Passing ``None`` keeps the legacy all-tools behavior; an iterable of names
        enables request-scoped schemas and reduces prompt tokens for small models.
        """
        if names is None:
            selected = self._tools.values()
        else:
            wanted = set(names)
            selected = (tool for name, tool in self._tools.items() if name in wanted)
        return [tool.schema() for tool in selected]

    def names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> list[dict[str, str]]:
        return [{"name": t.name, "description": t.description,
                 "capability_id": t.capability_id, "category": t.category}
                for t in self._tools.values()]

    @staticmethod
    def _validate(spec: ToolSpec, arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
        schema = spec.parameters or {}
        allowed = set((schema.get("properties") or {}).keys())
        unknown = set(arguments) - allowed
        if unknown:
            raise ValueError("Unexpected argument(s): " + ", ".join(sorted(unknown)))
        missing = [key for key in schema.get("required", []) if key not in arguments]
        if missing:
            raise ValueError("Missing required argument(s): " + ", ".join(missing))
        for key, value in arguments.items():
            rule = (schema.get("properties") or {}).get(key, {})
            expected = rule.get("type")
            if expected == "string" and not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"{key} must be an integer")
            if expected == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError(f"{key} must be a number")
            if expected == "boolean" and not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")
            enum = rule.get("enum")
            if enum and value not in enum:
                raise ValueError(f"{key} must be one of: {', '.join(map(str, enum))}")
        return arguments

    def execute(self, name: str, arguments: Any) -> Any:
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"Unknown tool: {name}")
        validated = self._validate(spec, arguments)
        return spec.handler(**validated)
