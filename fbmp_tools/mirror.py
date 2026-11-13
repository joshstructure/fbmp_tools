"""Mirror the directional loads of an FB-MultiPier model about the pile cap centerlines.

The foundation is kept exactly as modeled.  Selected load cases are rewritten so
each load acts at the mirror-image location with mirrored components:

    Mirror X  (x -> 2*cx - x):  Fx, My, Mz change sign
    Mirror Y  (y -> 2*cy - y):  Fy, Mx, Mz change sign
    Mirror XY (both)         :  Fx, Fy, Mx, My change sign

LOAD lines move to the nearest node of the same kind at the mirrored position.
LOADBP lines move to the bearing at the mirrored position (found from the
bearing coordinates, so the bearing index and the L/R row follow the geometry).
"""
from __future__ import annotations

import fnmatch
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .infile import COMPONENTS, InFile, LoadLine, load_family
from .outfile import Node, OutModel

DIRECTIONS = ("X", "Y", "XY")
FLIP = {
    "X": (True, False, False, False, True, True),
    "Y": (False, True, False, True, False, True),
    "XY": (True, True, False, True, True, False),
}
#: load families mirrored unless the user chooses otherwise
DEFAULT_FAMILIES = ("WS", "WL", "BR", "WA", "TU")
#: families that are directional by nature; the audit points them out when left unmirrored
DIRECTIONAL_FAMILIES = ("WS", "WL", "BR", "WA", "TU", "CE", "CR", "SH", "CT", "CV", "IC", "EQ", "FR")


@dataclass
class NodeMatch:
    node: int
    kind: str
    pile: int
    x: float
    y: float
    z: float
    mirror_x: float
    mirror_y: float
    new_node: int
    new_pile: int
    new_x: float
    new_y: float
    new_z: float
    offset: float
    flag: str = ""
    loads: int = 0


@dataclass
class LoadChange:
    line: LoadLine
    mirrored: bool
    new_target: int
    new_side: str
    new_values: Tuple[str, ...]
    pos: Optional[Tuple[float, float, float]]        # where the load acts before
    new_pos: Optional[Tuple[float, float, float]]    # where it acts after

    @property
    def before(self) -> Tuple[float, ...]:
        return self.line.floats

    @property
    def after(self) -> Tuple[float, ...]:
        return tuple(float(v) for v in self.new_values)

    @property
    def label(self) -> str:
        return f"{self.line.target}{self.line.side}"

    @property
    def new_label(self) -> str:
        return f"{self.new_target}{self.new_side}"


@dataclass
class AuditRow:
    family: str
    selected: bool
    status: Dict[str, str]                 # component -> status text
    level: Dict[str, str]                  # component -> 'ok' | 'check' | 'problem' | 'na'
    cases: List[int] = field(default_factory=list)


@dataclass
class Resultant:
    case: int
    type: str
    before: Tuple[float, ...]     # Fx Fy Fz Mx My Mz about the mirror center (kips, kip-ft)
    after: Tuple[float, ...]
    expected: Tuple[float, ...]


@dataclass
class MirrorResult:
    direction: str
    text: str
    changes: List[LoadChange]
    node_map: Dict[int, NodeMatch]
    center: Tuple[float, float]
    selected_cases: Set[int]
    audit: List[AuditRow]
    resultants: List[Resultant]
    warnings: List[str]
    title_suffix: str

    def report(self) -> str:
        return format_report(self)


def neg_text(value: str) -> str:
    """Negate a number written as text, keeping its formatting (zero stays as written)."""
    try:
        if float(value) == 0:
            return value
    except ValueError:
        pass
    if value.startswith("-"):
        return value[1:]
    if value.startswith("+"):
        return "-" + value[1:]
    return "-" + value


def cases_for(infile: InFile, patterns: Iterable[str]) -> Set[int]:
    """Load case numbers whose type label matches any pattern ('WS*', 'TU', 'BR3', ...)."""
    pats = [p.strip() for p in patterns if p and p.strip()]
    out: Set[int] = set()
    for case, label in infile.case_types().items():
        if any(fnmatch.fnmatchcase(label, p) or load_family(label) == p for p in pats):
            out.add(case)
    return out


def default_cases(infile: InFile) -> Set[int]:
    return cases_for(infile, DEFAULT_FAMILIES)


class _Geometry:
    def __init__(self, model: OutModel, center: Optional[Tuple[Optional[float], Optional[float]]] = None):
        self.model = model
        cx, cy = model.mirror_center()
        if center:
            cx = cx if center[0] is None else float(center[0])
            cy = cy if center[1] is None else float(center[1])
        self.cx, self.cy = cx, cy
        nodes = list(model.nodes.values())
        self.pile = [n for n in nodes if n.group == "pile"]
        self.cap = [n for n in nodes if n.group == "cap"] + [n for n in self.pile if n.pile_head]
        self.pier = [n for n in nodes if n.group == "pier"]
        self.brg_l = sorted((n for n in nodes if n.group == "bearingL"), key=lambda n: n.id)
        self.brg_r = sorted((n for n in nodes if n.group == "bearingR"), key=lambda n: n.id)
        self.bearing = self.brg_l + self.brg_r
        self.n_bearings = model.n_bearings or len(self.brg_l) or None

    def candidates(self, node: Node) -> List[Node]:
        if node.group == "pile":
            pool = self.pile
        elif node.group == "cap":
            pool = self.cap
        elif node.group == "pier":
            pool = self.pier
        elif node.group.startswith("bearing"):
            pool = self.bearing
        else:
            pool = [n for n in self.model.nodes.values() if n.group == node.group]
        if node.active:
            active = [n for n in pool if n.active]
            if active:
                return active
        return pool

    def mirror_point(self, x: float, y: float, direction: str) -> Tuple[float, float]:
        if "X" in direction:
            x = 2 * self.cx - x
        if "Y" in direction:
            y = 2 * self.cy - y
        return x, y

    def nearest(self, node: Node, direction: str, pool: Optional[Sequence[Node]] = None):
        tx, ty = self.mirror_point(node.x, node.y, direction)
        best, best_d = None, math.inf
        for cand in (self.candidates(node) if pool is None else pool):
            d = (cand.x - tx) ** 2 + (cand.y - ty) ** 2 + (cand.z - node.z) ** 2
            if d < best_d:
                best, best_d = cand, d
        return best, math.sqrt(best_d), tx, ty

    def bearing_node(self, index: int, side: str) -> Optional[Node]:
        row = self.brg_l if side == "L" else self.brg_r
        return row[index - 1] if 0 < index <= len(row) else None

    def mirror_bearing(self, node: Node, direction: str):
        """Bearing at the mirror point.  When both rows are equally near (rows at the same plan
        position), Mirror X keeps the row and Mirror Y / XY changes it."""
        tx, ty = self.mirror_point(node.x, node.y, direction)
        dist = [math.sqrt((c.x - tx) ** 2 + (c.y - ty) ** 2 + (c.z - node.z) ** 2) for c in self.bearing]
        best = min(dist)
        want = node.group if "Y" not in direction else ("bearingR" if node.group == "bearingL" else "bearingL")
        near = [c for c, d in zip(self.bearing, dist) if d <= best + 0.01]
        for cand in near:
            if cand.group == want:
                return cand, best
        return near[0], best

    def bearing_id(self, node: Node) -> Tuple[int, str]:
        if node.group == "bearingL":
            return self.brg_l.index(node) + 1, "L"
        return self.brg_r.index(node) + 1, "R"


def mirror_loads(
    infile: InFile,
    model: OutModel,
    direction: str,
    selected_cases: Optional[Iterable[int]] = None,
    center: Optional[Tuple[Optional[float], Optional[float]]] = None,
    tolerance: float = 24.0,
    title_suffix: Optional[str] = None,
) -> MirrorResult:
    """Return the mirrored .in text plus a before/after record of every load line."""
    if direction not in FLIP:
        raise ValueError(f"direction must be one of {DIRECTIONS}, not {direction!r}")
    selected = set(default_cases(infile) if selected_cases is None else selected_cases)
    flip = FLIP[direction]
    geo = _Geometry(model, center)
    warnings: List[str] = []
    out_lines = list(infile.lines)
    changes: List[LoadChange] = []
    node_map: Dict[int, NodeMatch] = {}
    missing: Set[int] = set()

    for ld in infile.loads:
        pos = _position(geo, ld)
        if ld.case not in selected:
            changes.append(LoadChange(ld, False, ld.target, ld.side, ld.values, pos, pos))
            continue
        new_values = tuple(neg_text(v) if flip[k] else v for k, v in enumerate(ld.values))
        new_target, new_side, new_pos = ld.target, ld.side, pos
        if ld.section == "LOAD":
            src = model.nodes.get(ld.target)
            if src is None:
                missing.add(ld.target)
            else:
                match = node_map.get(ld.target)
                if match is None:
                    best, dist, tx, ty = geo.nearest(src, direction)
                    flag = "CHECK" if dist > tolerance else ("approx" if dist > 1.0 else "")
                    match = NodeMatch(src.id, src.group, src.pile, src.x, src.y, src.z, tx, ty,
                                      best.id, best.pile, best.x, best.y, best.z, dist, flag)
                    node_map[ld.target] = match
                match.loads += 1
                new_target = match.new_node
                new_pos = (match.new_x, match.new_y, match.new_z)
        else:
            src = geo.bearing_node(ld.target, ld.side)
            if src is not None:
                best, dist = geo.mirror_bearing(src, direction)
                new_target, new_side = geo.bearing_id(best)
                new_pos = (best.x, best.y, best.z)
                if dist > 1.0:
                    msg = (f"Bearing {ld.target}{ld.side} has no mirror-image bearing for Mirror {direction} "
                           f"(nearest is {new_target}{new_side}, {dist:.1f} off).")
                    if msg not in warnings:
                        warnings.append(msg)
            elif geo.bearing:   # other bearings have coordinates but this one does not: do not guess
                msg = (f"Bearing {ld.target}{ld.side} is not in the .out file; its loads keep their bearing "
                       f"(components still change sign). Check them by hand.")
                if msg not in warnings:
                    warnings.append(msg)
            else:   # no bearing coordinates at all: use the index rule
                if "X" in direction:
                    if not geo.n_bearings:
                        raise ValueError("Bearing count unknown; cannot mirror LOADBP in X.")
                    new_target = geo.n_bearings + 1 - ld.target
                if "Y" in direction:
                    new_side = "R" if ld.side == "L" else "L"
        out_lines[ld.index] = ld.render(new_target, new_values, new_side)
        changes.append(LoadChange(ld, True, new_target, new_side, new_values, pos, new_pos))

    # title line
    families = _ordered_families(infile, selected)
    suffix = title_suffix if title_suffix is not None else f" - Mirror {direction} " + " ".join(families)
    for i in range(len(out_lines) - 1):
        if infile.sections[i] == "PROBLEM" and infile.sections[i + 1] == "PROBLEM#END":
            out_lines[i] = out_lines[i].rstrip() + suffix
            break

    if infile.unparsed:
        warnings.append(f"{len(infile.unparsed)} line(s) in the LOAD / LOADBP sections were not understood and are copied "
                        f"unchanged (first: line {infile.unparsed[0] + 1}). Check those loads by hand.")
    loose = [m for m in node_map.values() if 3.0 < m.offset <= tolerance]
    if loose:
        worst = max(loose, key=lambda m: m.offset)
        warnings.append(f"{len(loose)} loaded node(s) have no node at their mirror point and were moved to the nearest one, "
                        f"up to {worst.offset:.1f} away (node {worst.node} to node {worst.new_node}, {worst.kind}). "
                        "Expected for as-built piles; the mirrored resultant shifts slightly.")
    if missing:
        warnings.append("Nodes not found in the .out file (load left on the same node): "
                        + ", ".join(str(n) for n in sorted(missing)))
    for m in sorted(node_map.values(), key=lambda m: m.node):
        if m.flag == "CHECK":
            warnings.append(f"Node {m.node} ({m.kind}) matched to node {m.new_node}, {m.offset:.1f} from its mirror point "
                            f"(tolerance {tolerance:g}).")
    stacked = defaultdict(list)
    for m in node_map.values():
        stacked[m.new_node].append(m.node)
    for target, sources in sorted(stacked.items()):
        if len(sources) > 1:
            warnings.append(f"Nodes {', '.join(map(str, sorted(sources)))} all map onto node {target}; their loads stack.")
    sym = pier_symmetry(model, geo)
    for d in ("X", "Y"):
        if d in direction and sym[d] > 1.0:
            warnings.append(f"Pier is not symmetric about the Mirror {d} plane (largest pier-node offset {sym[d]:.2f}).")

    return MirrorResult(
        direction=direction,
        text=infile.text(out_lines),
        changes=changes,
        node_map=node_map,
        center=(geo.cx, geo.cy),
        selected_cases=selected,
        audit=build_audit(infile, changes, selected, direction),
        resultants=build_resultants(changes, geo, direction),
        warnings=warnings,
        title_suffix=suffix,
    )


def _position(geo: _Geometry, ld: LoadLine) -> Optional[Tuple[float, float, float]]:
    node = geo.model.nodes.get(ld.target) if ld.section == "LOAD" else geo.bearing_node(ld.target, ld.side)
    return (node.x, node.y, node.z) if node is not None else None


def _ordered_families(infile: InFile, cases: Set[int]) -> List[str]:
    seen: Dict[str, None] = {}
    types = infile.case_types()
    for case in sorted(cases):
        if case in types:
            seen.setdefault(load_family(types[case]))
    return list(seen)


def pier_symmetry(model: OutModel, geo: Optional[_Geometry] = None) -> Dict[str, float]:
    geo = geo or _Geometry(model)
    active = [n for n in geo.pier if n.active]
    out = {}
    for d in ("X", "Y"):
        worst = 0.0
        for n in active:
            _, dist, _, _ = geo.nearest(n, d, active)
            worst = max(worst, dist)
        out[d] = worst
    return out


# ---------------------------------------------------------------------------
# audit: what happened to each load family, component by component
# ---------------------------------------------------------------------------
#: loads that land within this distance of an equal original load count as "the same load" in the audit
SAME_LOAD_TOLERANCE = 3.0


def _signature(changes: Sequence[LoadChange], k: int, after: bool) -> Dict[Tuple, Tuple[float, Optional[Tuple]]]:
    """(case, section, target, side) -> (summed component k, position), zeros dropped."""
    sig: Dict[Tuple, float] = defaultdict(float)
    where: Dict[Tuple, Optional[Tuple]] = {}
    for ch in changes:
        if after:
            key = (ch.line.case, ch.line.section, ch.new_target, ch.new_side)
            sig[key] += ch.after[k]
            where[key] = ch.new_pos
        else:
            key = (ch.line.case, ch.line.section, ch.line.target, ch.line.side)
            sig[key] += ch.before[k]
            where[key] = ch.pos
    return {key: (round(v, 9), where[key]) for key, v in sig.items() if abs(v) > 1e-12}


def _same_loads(before: Dict, after: Dict, tolerance: float = SAME_LOAD_TOLERANCE) -> Optional[float]:
    """Largest distance between matched equal loads, or None when the two load sets differ."""
    if len(before) != len(after):
        return None
    pool: Dict[Tuple, List] = defaultdict(list)
    for key, (value, pos) in before.items():
        pool[(key[0], key[1], value)].append((key, pos))
    worst = 0.0
    for key, (value, pos) in after.items():
        cands = pool.get((key[0], key[1], value))
        if not cands:
            return None
        best_i, best_d = -1, math.inf
        for i, (bkey, bpos) in enumerate(cands):
            if pos is None or bpos is None:
                d = 0.0 if bkey == key else math.inf
            else:
                d = math.dist(pos, bpos)
            if d < best_d:
                best_i, best_d = i, d
        if best_d > tolerance:
            return None
        cands.pop(best_i)
        worst = max(worst, best_d)
    return worst


def build_audit(infile: InFile, changes: Sequence[LoadChange], selected: Set[int], direction: str) -> List[AuditRow]:
    by_family: Dict[str, List[LoadChange]] = defaultdict(list)
    for ch in changes:
        by_family[ch.line.family].append(ch)
    flip = FLIP[direction]
    rows: List[AuditRow] = []
    for family in infile.families():
        fam = by_family.get(family, [])
        cases = sorted({ch.line.case for ch in fam})
        n_sel = sum(1 for c in cases if c in selected)
        is_selected = n_sel > 0
        status: Dict[str, str] = {}
        level: Dict[str, str] = {}
        for k, comp in enumerate(COMPONENTS):
            before = _signature(fam, k, False)
            if not before:
                status[comp], level[comp] = "N/A (zero)", "na"
                continue
            after = _signature(fam, k, True)
            if not is_selected:
                if flip[k] and family in DIRECTIONAL_FAMILIES:
                    status[comp], level[comp] = "NOT mirrored (case not selected)", "problem"
                elif flip[k]:
                    status[comp], level[comp] = "kept (not selected)", "check"
                else:
                    status[comp], level[comp] = "kept", "ok"
                continue
            partial = "" if n_sel == len(cases) else f" ({n_sel} of {len(cases)} cases)"
            same = _same_loads(before, after)
            if same is not None:
                near = f" (within {same:.1f})" if same > 0.05 else ""
                if flip[k]:
                    status[comp] = f"symmetric: mirror image equals the original{near}{partial}"
                    level[comp] = "check"
                else:
                    status[comp], level[comp] = f"same after mirroring{near}{partial}", "ok"
            elif flip[k]:
                status[comp], level[comp] = f"mirrored, sign reversed{partial}", "ok"
            else:
                status[comp], level[comp] = f"moved to mirror location{partial}", "ok"
        rows.append(AuditRow(family, is_selected, status, level, cases))
    return rows


def build_resultants(changes: Sequence[LoadChange], geo: _Geometry, direction: str) -> List[Resultant]:
    flip = FLIP[direction]
    sums: Dict[int, Dict[str, object]] = {}
    for ch in changes:
        if not ch.mirrored or ch.pos is None or ch.new_pos is None:
            continue
        rec = sums.setdefault(ch.line.case, {"type": ch.line.type, "b": [0.0] * 6, "a": [0.0] * 6})
        for vec, pos, key in ((ch.before, ch.pos, "b"), (ch.after, ch.new_pos, "a")):
            rx, ry, rz = pos[0] - geo.cx, pos[1] - geo.cy, pos[2]
            fx, fy, fz, mx, my, mz = vec
            tot = rec[key]
            tot[0] += fx
            tot[1] += fy
            tot[2] += fz
            # reported in kip-ft: coordinates are inches, applied moments are entered in kip-ft
            tot[3] += (ry * fz - rz * fy) / 12.0 + mx
            tot[4] += (rz * fx - rx * fz) / 12.0 + my
            tot[5] += (rx * fy - ry * fx) / 12.0 + mz
    out = []
    for case in sorted(sums):
        rec = sums[case]
        before = tuple(rec["b"])
        expected = tuple(-v if flip[k] else v for k, v in enumerate(before))
        out.append(Resultant(case, rec["type"], before, tuple(rec["a"]), expected))
    return out


# ---------------------------------------------------------------------------
# text report
# ---------------------------------------------------------------------------
def format_audit(result: MirrorResult, components: Sequence[str] = ("Fx", "Fy", "Fz")) -> str:
    rows = [("Load type",) + tuple(components)]
    for row in result.audit:
        rows.append((row.family + ("" if row.selected else " (kept)"),) + tuple(row.status[c] for c in components))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip() for r in rows)


def format_report(result: MirrorResult) -> str:
    out = [f"FB-MultiPier load mirroring report  -  Mirror {result.direction}", ""]
    out.append(f"Mirror planes: X = {result.center[0]:.3f}, Y = {result.center[1]:.3f}")
    out.append("Sign changes applied: " + ", ".join(c for c, f in zip(COMPONENTS, FLIP[result.direction]) if f))
    n_load = sum(1 for c in result.changes if c.mirrored and c.line.section == "LOAD")
    n_bp = sum(1 for c in result.changes if c.mirrored and c.line.section == "LOADBP")
    out.append(f"LOAD lines rewritten: {n_load} ({len(result.node_map)} distinct nodes); LOADBP lines rewritten: {n_bp}")
    out += ["", "What happened to each load type:", format_audit(result), ""]
    if result.warnings:
        out.append("Warnings:")
        out += ["  - " + w for w in result.warnings]
        out.append("")
    kinds: Dict[str, List[NodeMatch]] = defaultdict(list)
    for m in result.node_map.values():
        kinds[m.kind].append(m)
    for kind, items in kinds.items():
        out.append(f"  {kind:<8} {len(items):4d} nodes, largest offset from the ideal mirror point = "
                   f"{max(i.offset for i in items):.2f}")
    out += ["", "Resultants of the mirrored cases about the mirror planes (kips, kip-ft; model in kips and inches):",
            "  case  type     " + "".join(f"{c + '(new)':>13}{c + '(exp)':>13}" for c in COMPONENTS)]
    for r in result.resultants:
        out.append(f"  {r.case:<5d} {r.type:<8s}" + "".join(f"{a:13.2f}{e:13.2f}" for a, e in zip(r.after, r.expected)))
    return "\n".join(out) + "\n"
