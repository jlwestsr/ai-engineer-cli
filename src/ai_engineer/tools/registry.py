"""Associate tool schemas with handlers and validate arguments before dispatch."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any


class ToolRegistry:
    """Trusted application registration; models may select tools but cannot add them."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[dict[str, Any], Callable[[dict[str, Any]], Any]]] = {}

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        function: Callable[[dict[str, Any]], Any],
    ) -> None:
        if not name.strip() or name in self._tools:
            raise ValueError(f"Tool name must be non-empty and unique: {name!r}")
        self._tools[name] = (
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
            function,
        )

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    @classmethod
    def _validate_value(cls, value: Any, schema: dict[str, Any], path: str) -> None:
        expected_type = schema.get("type")
        type_checks: dict[str, Callable[[Any], bool]] = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "boolean": lambda item: isinstance(item, bool),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: (
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(item)
            ),
            "null": lambda item: item is None,
        }
        if expected_type is not None:
            check_type = type_checks.get(expected_type)
            if check_type is None:
                raise ValueError(f"Unsupported schema type {expected_type!r} at {path}")
            if not check_type(value):
                raise ValueError(f"{path} must be {expected_type}")

        if isinstance(value, dict):
            properties = schema.get("properties", {})
            missing = set(schema.get("required", ())) - value.keys()
            if missing:
                raise ValueError(
                    f"{path} is missing required field(s): {', '.join(sorted(missing))}"
                )
            additional = schema.get("additionalProperties", True)
            for key, item in value.items():
                property_schema = properties.get(key)
                if property_schema is not None:
                    cls._validate_value(item, property_schema, f"{path}.{key}")
                elif additional is False:
                    raise ValueError(f"{path} contains unexpected field: {key}")
                elif isinstance(additional, dict):
                    cls._validate_value(item, additional, f"{path}.{key}")

        if isinstance(value, list) and isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                cls._validate_value(item, schema["items"], f"{path}[{index}]")

    def definitions(self) -> list[dict[str, Any]]:
        return [entry[0] for entry in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self._tools:
            return {"error": f"Unknown tool: {name}"}
        try:
            schema = self._tools[name][0]["function"]["parameters"]
            self._validate_value(arguments, schema, "arguments")
            return self._tools[name][1](arguments)
        except Exception as error:  # noqa: BLE001 — tool failures must not abort the turn
            return {"error": str(error)}
