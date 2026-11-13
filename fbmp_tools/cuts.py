"""Section cuts through the pile cap by statics.

The pile cap is taken as a free body.  For every load combination the forces on
it are collected from the .out file:

  * pile head forces (first row of each pile's force table),
  * column forces through the connector elements at their pile cap nodes
    (or the column base forces when the model has no connectors),
  * pile cap self weight (element area x self-weight thickness x unit weight x DC factor),
  * loads applied directly to pile cap nodes (.in LOAD lines x combination factors).

A cut is a full-width straight line in plan.  The moment and shear on the cut are
the resultants of everything on one side of it.  The other side gives the same
answer when the free body is in equilibrium; the difference is reported so the
reading of the .out file can be checked on every model.

Element forces are printed in local axes 1-2-3.  The axes used here were
calibrated on FB-MultiPier 6 output for vertical piles/columns and horizontal
connectors:  1 = from node I to node J;  vertical member: 2 = global X;
other members: 3 = 1 x Z (horizontal), 2 = 3 x 1.  Battered piles follow the
second rule, which has not been checked against a battered model: look at the
equilibrium residual before using results from one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .infile import InFile
from .outfile import ComboResult, OutModel

_Z = np.array([0.0, 0.0, 1.0])
_X = np.array([1.0, 0.0, 0.0])


def local_axes(p_i: Sequence[float], p_j: Sequence[float]) -> np.ndarray:
    """Rows e1, e2, e3 of the element local axes in global coordinates."""
    e1 = np.asarray(p_j, float) - np.asarray(p_i, float)
    e1 /= np.linalg.norm(e1)
    horizontal = np.hypot(e1[0], e1[1])
    if horizontal < 1e-6:
        e2 = _X.copy()
        e3 = np.cross(e1, e2)
    else:
        e3 = np.cross(e1, _Z)
        e3 /= np.linalg.norm(e3)
        e2 = np.cross(e3, e1)
    return np.vstack([e1, e2, e3])


@dataclass
class CapForces:
    """Forces acting on the pile cap, per load combination."""
    combos: List[int]
    xyz: np.ndarray                 # (n, 3) application points, model length units
    force: np.ndarray               # (n_combos, n, 3) global Fx Fy Fz on the cap
    moment: np.ndarray              # (n_combos, n, 3) global Mx My Mz on the cap, kip-ft
    kind: List[str]                 # 'pile', 'column', 'self weight', 'cap load'
    label: List[str]
    length_per_ft: float = 12.0
    notes: List[str] = field(default_factory=list)

    def residual(self) -> np.ndarray:
        """(n_combos, 6) sum of forces and of moments about the cap centroid; zero in equilibrium."""
        origin = self.xyz.mean(axis=0)
        r = (self.xyz - origin) / self.length_per_ft
        f = self.force.sum(axis=1)
        m = (np.cross(r[None, :, :], self.force) + self.moment).sum(axis=1)
        return np.hstack([f, m])

    def moments_about_origin(self) -> Tuple[np.ndarray, np.ndarray]:
        """(origin, per-item moment of force about that origin plus applied moment), cached."""
        if getattr(self, "_m0", None) is None:
            origin = np.array([self.xyz[:, 0].mean(), self.xyz[:, 1].mean(), 0.0])
            r = self.xyz.copy()
            r[:, 2] = 0.0                      # all cap forces act at the cap plane
            r = (r - origin) / self.length_per_ft
            self._origin = origin
            self._m0 = np.cross(r[None, :, :], self.force) + self.moment
        return self._origin, self._m0

    def vertical_load(self) -> np.ndarray:
        """(n_combos,) total downward load on the cap (columns + self weight + cap loads)."""
        mask = np.array([k != "pile" for k in self.kind])
        return self.force[:, mask, 2].sum(axis=1)


def _frame_lookup(model: OutModel):
    by_id = {e.id: e for e in model.frame_elements}
    by_node: Dict[int, list] = {}
    for e in model.frame_elements:
        by_node.setdefault(e.i, []).append(e)
        by_node.setdefault(e.j, []).append(e)
    return by_id, by_node


def _precise_thickness(infile: Optional[InFile]) -> List[float]:
    """Self-weight thicknesses as entered in the .in CAP section (the .out rounds them to 0.01 ft)."""
    if infile is None:
        return []
    found = set()
    for line, sec in zip(infile.lines, infile.sections):
        if sec == "CAP":
            m = re.search(r"T=\s*[-+0-9.eE]+\s*,\s*([-+0-9.eE]+)", line)
            if m:
                try:
                    found.add(float(m.group(1)))
                except ValueError:
                    pass
    return sorted(found)


def _xyz(model: OutModel, node: int) -> np.ndarray:
    n = model.nodes[node]
    return np.array([n.x, n.y, n.z])


def build_cap_forces(model: OutModel, infile: Optional[InFile], results: Dict[int, ComboResult],
                     length_per_ft: float = 12.0) -> CapForces:
    combos = sorted(c for c, r in results.items() if r.pile_head)
    has_columns = any(r.connector or r.column_base for r in results.values())
    incomplete = [c for c in combos if has_columns and not (results[c].connector or results[c].column_base)]
    combos = [c for c in combos if c not in incomplete]
    if not combos:
        raise ValueError("No pile force results found in the .out file (was the analysis run to completion?).")
    by_id, by_node = _frame_lookup(model)
    notes: List[str] = []
    if incomplete:
        notes.append("Left out load combination(s) " + ", ".join(map(str, incomplete))
                     + ": the .out file has no column forces for them (incomplete run).")

    points: List[np.ndarray] = []
    kind: List[str] = []
    label: List[str] = []
    axes: List[Optional[np.ndarray]] = []      # local axes for element-force items
    source: List[Tuple[str, int]] = []         # ('pile', pile no) / ('connector', elem) / ('column', col no)

    first = results[combos[0]]
    battered = 0
    for pile, (node, _) in sorted(first.pile_head.items()):
        elem = next((e for e in by_node.get(node, []) if e.kind == "pile"), None)
        if elem is None:
            continue
        ax = local_axes(_xyz(model, elem.i), _xyz(model, elem.j))
        if np.hypot(ax[0][0], ax[0][1]) >= 1e-6:
            battered += 1
        points.append(_xyz(model, node)); kind.append("pile"); label.append(f"Pile {pile}")
        axes.append(ax); source.append(("pile", pile))
    if battered:
        notes.append(f"{battered} battered pile(s): local-axis convention not verified for batter; check the equilibrium residual.")

    if first.connector:
        for elem_id, (node, _) in sorted(first.connector.items()):
            elem = by_id.get(elem_id)
            if elem is None or node not in model.nodes:
                continue
            points.append(_xyz(model, node)); kind.append("column"); label.append(f"Connector {elem_id} (node {node})")
            axes.append(local_axes(_xyz(model, elem.i), _xyz(model, elem.j))); source.append(("connector", elem_id))
    else:
        for col, (node, _) in sorted(first.column_base.items()):
            elem = next((e for e in by_node.get(node, []) if e.kind == "pier"), None)
            if elem is None:
                continue
            points.append(_xyz(model, node)); kind.append("column"); label.append(f"Column {col}")
            axes.append(local_axes(_xyz(model, elem.i), _xyz(model, elem.j))); source.append(("column", col))
        if first.column_base:
            notes.append("No connector elements in the results: each column is applied at its base node.")
    n_elem_items = len(points)

    # pile cap self weight: four sub-quads per 9-node element
    sw_start = len(points)
    weights: List[float] = []
    gamma = (model.cap_unit_weight_pcf or 0.0) / 1000.0            # kcf
    precise = _precise_thickness(infile)
    for el in model.cap_elements:
        thick_sw = el.thick_sw
        match = [t for t in precise if abs(t - thick_sw) <= 0.00501]
        if len(match) == 1:
            thick_sw = match[0]
        nd = [model.nodes[i] for i in el.nodes]
        for a, b, c, d in ((0, 1, 4, 3), (1, 2, 5, 4), (3, 4, 7, 6), (4, 5, 8, 7)):
            quad = [nd[a], nd[b], nd[c], nd[d]]
            xs = np.array([q.x for q in quad]); ys = np.array([q.y for q in quad])
            area = 0.5 * abs(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1)))
            if area <= 0:
                continue
            points.append(np.array([xs.mean(), ys.mean(), nd[4].z])); kind.append("self weight")
            label.append(f"Cap element {el.id}")
            weights.append(area / length_per_ft ** 2 * thick_sw * gamma)
    sw_end = len(points)

    # loads applied directly to pile cap nodes / pile heads
    cap_loads: List[Tuple[int, str, np.ndarray]] = []
    if infile is not None:
        for ld in infile.loads:
            if ld.section != "LOAD":
                continue
            node = model.nodes.get(ld.target)
            if node is None or not (node.group == "cap" or node.pile_head):
                continue
            vec = np.array(ld.floats)
            if not vec.any():
                continue
            cap_loads.append((len(points), ld.type, vec))
            points.append(np.array([node.x, node.y, node.z])); kind.append("cap load")
            label.append(f"{ld.type} at node {ld.target}")
    else:
        notes.append("No .in file: loads applied directly to pile cap nodes are not included.")

    n = len(points)
    force = np.zeros((len(combos), n, 3))
    moment = np.zeros((len(combos), n, 3))
    for ci, combo in enumerate(combos):
        res = results[combo]
        factors = model.combos.get(combo, {})
        for k in range(n_elem_items):
            what, key = source[k]
            table = {"pile": res.pile_head, "connector": res.connector, "column": res.column_base}[what]
            entry = table.get(key)
            if entry is None:
                continue
            local = np.array(entry[1])
            # printed values act on the element end; the cap receives the opposite
            force[ci, k] = -(local[0] * axes[k][0] + local[1] * axes[k][1] + local[2] * axes[k][2])
            moment[ci, k] = -(local[5] * axes[k][0] + local[3] * axes[k][1] + local[4] * axes[k][2])
        dc = factors.get("DC", 1.0)
        force[ci, sw_start:sw_end, 2] = np.array(weights) * dc
        for idx, type_label, vec in cap_loads:
            f = factors.get(type_label, 0.0)
            force[ci, idx] = vec[:3] * f
            moment[ci, idx] = vec[3:] * f
    if "DC" not in model.case_names:
        notes.append("No DC column in the load combination matrix: pile cap self weight taken with factor 1.0.")
    return CapForces(combos, np.array(points), force, moment, kind, label, length_per_ft, notes)


@dataclass
class CutResult:
    """Resultants on a cut, per load combination.

    Loads that sit exactly on the cut line (a pile or column node on the line) make
    the section just on one side differ from the section just on the other side.
    Both are kept: suffix _a is the +normal side, _b the other side.
    """
    combos: List[int]
    moment_a: np.ndarray      # kip-ft, + = tension in the bottom of the cap
    moment_b: np.ndarray
    shear_a: np.ndarray       # kips, vertical shear
    shear_b: np.ndarray
    torsion: np.ndarray       # kip-ft about the cut normal (side a)
    axial: np.ndarray         # kips along the cut normal (side a)
    in_plane_shear: np.ndarray
    unbalance: np.ndarray     # kip-ft, moment that fails to close between the two sides
    on_line: int = 0          # number of loads sitting on the cut line

    @property
    def moment_max(self) -> np.ndarray:
        return np.maximum(self.moment_a, self.moment_b)

    @property
    def moment_min(self) -> np.ndarray:
        return np.minimum(self.moment_a, self.moment_b)

    @property
    def shear_abs(self) -> np.ndarray:
        return np.maximum(np.abs(self.shear_a), np.abs(self.shear_b))

    def summary(self) -> Dict[str, Tuple[float, int]]:
        def pick(arr, fn):
            i = int(fn(arr))
            return float(arr[i]), self.combos[i]
        return {
            "Max. moment (+), bottom steel": pick(self.moment_max, np.argmax),
            "Min. moment (-), top steel": pick(self.moment_min, np.argmin),
            "Max. shear (abs)": pick(self.shear_abs, np.argmax),
        }

    def closure(self) -> float:
        """Largest moment unbalance between the two sides as a fraction of the largest moment."""
        scale = max(1e-9, float(np.abs(self.moment_a).max()), float(np.abs(self.moment_b).max()))
        return float(np.abs(self.unbalance).max()) / scale


def _frame(p0: Sequence[float], p1: Sequence[float]):
    p0 = np.array([p0[0], p0[1], 0.0]); p1 = np.array([p1[0], p1[1], 0.0])
    t = p1 - p0
    length = np.linalg.norm(t)
    if length == 0:
        raise ValueError("The two cut points are the same.")
    t /= length
    n = np.cross(t, _Z)            # horizontal normal; side a is the +n side
    return p0, t, n, length


def cut(capf: CapForces, p0: Sequence[float], p1: Sequence[float], tol: float = 0.05) -> CutResult:
    """Resultants on the full-width cut through p0 and p1 (plan coordinates)."""
    p0v, t, n, _ = _frame(p0, p1)
    origin, m0 = capf.moments_about_origin()
    s = (capf.xyz[:, :2] - p0v[:2]) @ n[:2]                  # signed distance from the cut
    shift = np.array([p0v[0] - origin[0], p0v[1] - origin[1], 0.0]) / capf.length_per_ft
    axis = np.cross(_Z, n)                                   # sagging-positive moment axis for side a

    def side(mask):
        fsum = capf.force[:, mask, :].sum(axis=1)
        msum = m0[:, mask, :].sum(axis=1) - np.cross(shift[None, :], fsum)
        return fsum, msum

    fa, ma = side(s > tol)
    fb, mb = side(s < -tol)
    on = np.abs(s) <= tol
    fo, mo = side(on)
    raw_a = ma @ axis
    raw_b = -(mb @ axis)
    couple = mo @ axis                       # moments applied on the line itself (column, pile head)
    # Small errors in the read forces grow with the size of the free body, so the section values are
    # taken from the lighter side; the other face follows from the loads on the line.
    weight = np.abs(capf.force[:, :, 2]).sum(axis=0)
    if weight[s > tol].sum() <= weight[s < -tol].sum():
        moment_a, moment_b = raw_a, raw_a + couple
        shear_a, shear_b = fa[:, 2], fa[:, 2] + fo[:, 2]
    else:
        moment_a, moment_b = raw_b - couple, raw_b
        shear_a, shear_b = -fb[:, 2] - fo[:, 2], -fb[:, 2]
    return CutResult(
        combos=capf.combos, moment_a=moment_a, moment_b=moment_b,
        shear_a=shear_a, shear_b=shear_b,
        torsion=ma @ n, axial=fa @ n, in_plane_shear=fa @ axis,
        unbalance=raw_a + couple - raw_b, on_line=int(on.sum()),
    )


def sweep(capf: CapForces, axis: str, positions: Sequence[float]) -> Dict[str, np.ndarray]:
    """Envelope over all combinations for cuts at x = position (axis 'x') or y = position (axis 'y')."""
    lo = capf.xyz.min(axis=0); hi = capf.xyz.max(axis=0)
    keys = ("Max moment", "Min moment", "Max shear")
    env = {k: np.zeros(len(positions)) for k in keys}
    for i, pos in enumerate(positions):
        if axis.lower() == "x":
            res = cut(capf, (pos, lo[1]), (pos, hi[1]))
        else:
            res = cut(capf, (lo[0], pos), (hi[0], pos))
        env["Max moment"][i] = res.moment_max.max()
        env["Min moment"][i] = res.moment_min.min()
        env["Max shear"][i] = res.shear_abs.max()
    return env


def nodes_on_line(model: OutModel, p0: Sequence[float], p1: Sequence[float], tol: float = 1.0):
    """Pile cap nodes (and pile heads) within tol of the line, sorted along it, with their station."""
    p0v, t, n, _ = _frame(p0, p1)
    found = []
    for node in model.nodes.values():
        if not (node.group == "cap" or node.pile_head):
            continue
        d = np.array([node.x - p0v[0], node.y - p0v[1]])
        if abs(d @ n[:2]) <= tol:
            found.append((float(d @ t[:2]), node))
    found.sort(key=lambda item: item[0])
    return found


def elements_on_line(model: OutModel, p0: Sequence[float], p1: Sequence[float]):
    """Pile cap elements the line passes through (or touches)."""
    p0v, _, n, _ = _frame(p0, p1)
    hit = []
    for el in model.cap_elements:
        s = [(model.nodes[i].x - p0v[0]) * n[0] + (model.nodes[i].y - p0v[1]) * n[1] for i in (el.nodes[0], el.nodes[2], el.nodes[8], el.nodes[6])]
        if min(s) <= 1e-6 and max(s) >= -1e-6:
            hit.append(el)
    return hit
