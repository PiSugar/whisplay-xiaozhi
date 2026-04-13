"""
IoT Thing base class for XiaoZhi IoT protocol.
"""


class Thing:
    """Base class for IoT things that can be controlled by the server."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description

    def get_descriptor(self) -> dict:
        """Return the thing descriptor for server registration."""
        return {
            "name": self.name,
            "description": self.description,
            "properties": {},
            "methods": {},
        }

    def execute(self, method: str, params: dict) -> dict:
        """Execute a method on this thing."""
        raise NotImplementedError
