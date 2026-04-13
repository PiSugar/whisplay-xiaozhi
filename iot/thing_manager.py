"""
IoT Thing manager — registry for device things.
"""

import logging
from iot.thing import Thing

log = logging.getLogger("iot")


class ThingManager:
    def __init__(self):
        self._things: dict[str, Thing] = {}

    def register(self, thing: Thing):
        self._things[thing.name] = thing
        log.info("registered IoT thing: %s", thing.name)

    def get_descriptors(self) -> list[dict]:
        return [t.get_descriptor() for t in self._things.values()]

    def execute(self, name: str, method: str, params: dict) -> dict:
        thing = self._things.get(name)
        if not thing:
            return {"error": f"Unknown thing: {name}"}
        return thing.execute(method, params)
