"""Shared guard: a question that names one room must not be answered with a sum of several.

The parser sets `extra["single_room"]` for "the area of the room 'living room'" and for English
singular takeoffs ("tiles for the kitchen"). If a route then finds more than one matching room (two
living rooms on two levels), it abstains and lists them instead of silently adding them up.
"""
from __future__ import annotations


def single_room_problem(q, rooms: list, name=lambda r: None) -> str | None:
    if not q.extra.get("single_room") or len(rooms) <= 1:
        return None
    names = [str(name(r) or "?") for r in rooms][:8]
    return (f"the question names one room but {len(rooms)} rooms match ({', '.join(names)}); "
            f"name the level or the room number to pick one")
