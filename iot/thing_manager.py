"""
IoT Thing manager — registry for device things.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Tuple

from iot.thing import Thing

log = logging.getLogger("iot")


class ThingManager:
    def __init__(self):
        self._things: dict[str, Thing] = {}
        self._last_states: dict[str, dict] = {}

    def register(self, thing: Thing):
        self._things[thing.name] = thing
        log.info("registered IoT thing: %s", thing.name)

    def get_descriptors(self) -> list[dict]:
        return [t.get_descriptor() for t in self._things.values()]

    async def get_states(self, delta: bool = False) -> Tuple[bool, list]:
        """Get states of all things. If delta=True, only return changed ones."""
        if not delta:
            self._last_states.clear()

        changed = False
        states = []
        for thing in self._things.values():
            state = await thing.get_state()
            if delta:
                if thing.name in self._last_states and self._last_states[thing.name] == state:
                    continue
                changed = True
            self._last_states[thing.name] = state
            states.append(state)

        return changed, states

    async def invoke(self, command: Dict) -> Optional[Any]:
        name = command.get("name")
        thing = self._things.get(name)
        if not thing:
            log.error("unknown IoT thing: %s", name)
            raise ValueError(f"Unknown thing: {name}")
        return await thing.invoke(command)
