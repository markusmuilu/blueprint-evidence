"""Reference answers for the unseen IFC drawings, computed straight from the model with ifcopenshell
(no bpqa code). Prints one JSON object per question; tools/unseen_build_questions.py merges them.

    python tools/unseen_refs_ifc.py ../data/unseen
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ue
import ifcopenshell.util.unit as uu
import numpy as np


def storey_of(el):
    c = ue.get_container(el)
    while c is not None and not c.is_a("IfcBuildingStorey"):
        c = ue.get_aggregate(c)
    return c


def footprint_area(sp):
    """Floor area of a space from its mesh: the triangles on its lowest plane (m2). Fine for extruded prisms."""
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    sh = ifcopenshell.geom.create_shape(s, sp)
    v = np.array(sh.geometry.verts).reshape(-1, 3) 
    f = np.array(sh.geometry.faces).reshape(-1, 3)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    z0 = v[:, 2].min()
    flat = (np.abs(a[:, 2] - z0) < 1e-6) & (np.abs(b[:, 2] - z0) < 1e-6) & (np.abs(c[:, 2] - z0) < 1e-6)
    area = np.abs(np.cross(b - a, c - a)[:, 2]) / 2
    return float(area[flat].sum())


def qto_value(el, names):
    for qs in ue.get_psets(el, qtos_only=True).values():
        for k in names:
            if k in qs:
                return qs[k]
    return None
