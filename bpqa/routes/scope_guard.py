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


def ambiguous_room(route: str, why: str):
    """The abstention for a named room that matches several rooms. The drawing was read and the question is
    ambiguous (not missing data), so the answer is final: no model is asked to guess which room was meant."""
    from ..answer import abstain
    a = abstain(route, why)
    a.final = True
    return a
