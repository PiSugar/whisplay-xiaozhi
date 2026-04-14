"""
IoT Thing base class for XiaoZhi IoT protocol.

Matches the py-xiaozhi Thing/Property/Parameter/Method model so that
descriptors and commands are wire-compatible with the server.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List


class ValueType:
    BOOLEAN = "boolean"
    NUMBER = "number"
    STRING = "string"
    FLOAT = "float"


class Property:
    def __init__(self, name: str, description: str, getter: Callable):
        self.name = name
        self.description = description
        self.getter = getter
        self.type = ValueType.STRING
        self._type_determined = False

    def _determine_type(self, value: Any):
        if isinstance(value, bool):
            self.type = ValueType.BOOLEAN
        elif isinstance(value, int):
            self.type = ValueType.NUMBER
        elif isinstance(value, float):
            self.type = ValueType.FLOAT
        else:
            self.type = ValueType.STRING

    def get_descriptor(self) -> dict:
        return {"description": self.description, "type": self.type}

    async def get_value(self):
        value = await self.getter()
        if not self._type_determined:
            self._determine_type(value)
            self._type_determined = True
        return value


class Parameter:
    def __init__(self, name: str, description: str, type_: str, required: bool = True):
        self.name = name
        self.description = description
        self.type = type_
        self.required = required
        self.value = None

    def get_descriptor(self) -> dict:
        return {"description": self.description, "type": self.type}

    def set_value(self, value: Any):
        self.value = value

    def get_value(self) -> Any:
        return self.value


class Method:
    def __init__(self, name: str, description: str, parameters: List[Parameter], callback: Callable):
        self.name = name
        self.description = description
        self.parameters = {p.name: p for p in parameters}
        self.callback = callback

    def get_descriptor(self) -> dict:
        return {
            "description": self.description,
            "parameters": {
                name: p.get_descriptor() for name, p in self.parameters.items()
            },
        }

    async def invoke(self, params: Dict[str, Any]) -> Any:
        for name, value in params.items():
            if name in self.parameters:
                p = self.parameters[name]
                if p.type == ValueType.STRING and isinstance(value, (dict, list)):
                    p.set_value(json.dumps(value, ensure_ascii=False))
                else:
                    p.set_value(value)
        return await self.callback(self.parameters)


class Thing:
    """Base class for IoT things that can be controlled by the server."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.properties: Dict[str, Property] = {}
        self.methods: Dict[str, Method] = {}

    def add_property(self, name: str, description: str, getter: Callable):
        self.properties[name] = Property(name, description, getter)

    def add_method(self, name: str, description: str, parameters: List[Parameter], callback: Callable):
        self.methods[name] = Method(name, description, parameters, callback)

    def get_descriptor(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "properties": {
                name: p.get_descriptor() for name, p in self.properties.items()
            },
            "methods": {
                name: m.get_descriptor() for name, m in self.methods.items()
            },
        }

    async def get_state(self) -> dict:
        state = {}
        for name, prop in self.properties.items():
            state[name] = await prop.get_value()
        return {"name": self.name, "state": state}

    async def invoke(self, command: dict) -> Any:
        method_name = command.get("method")
        if method_name not in self.methods:
            raise ValueError(f"Unknown method: {method_name}")
        params = command.get("parameters", {})
        return await self.methods[method_name].invoke(params)
