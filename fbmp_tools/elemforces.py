"""Element forces on a plane picked by a node and an axis.

The .out file prints, for every load combination, the end forces of every frame
element (piles, columns, pier cap, bent cap, extra members) in element local axes:
FAX, F22, F33 (kips) and M22, M33, TORQUE (kip-ft).

A plane is defined by a node number and a global axis: it is the plane normal to
that axis through the node (axis X: all points with the node's X).  Every frame
element lying in the plane is collected, grouped into members (a pile, a column,
the cap beam ...) and plotted as a max / min envelope over all load combinations.

Sign: the printed values are forces on the element ends, so the two ends of an
element have opposite signs.  The plots use internal forces, continuous along a
member: the J-end value as printed and the I-end value reversed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .outfile import OutModel, Source, _lines

COMPONENTS = ("FAX", "F22", "F33", "M22", "M33", "TORQUE")
UNITS = ("kips", "kips", "kips", "kip-ft", "kip-ft", "kip-ft")
AXES = ("X", "Y", "Z")


@dataclass
class ElementForces:
    combos: List[int]
    elem_ids: List[int]
    index: Dict[int, int]
    data: np.ndarray            # (n_combos, n_elem, 2, 6) printed end forces; NaN where not printed
    nodes: np.ndarray           # (n_elem, 2) node numbers of the printed ends

    def internal(self) -> np.ndarray:
        """(n_combos, n_elem, 2, 6) internal forces: I end reversed, J end as printed."""
        out = self.data.copy()
        out[:, :, 0, :] *= -1.0
        return out

    def envelope(self):
        """max, min (n_elem, 2, 6) and the load combinations that give them."""
        val = self.internal()
        filled_max = np.where(np.isnan(val), -np.inf, val)
        filled_min = np.where(np.isnan(val), np.inf, val)
        imax = filled_max.argmax(axis=0)
        imin = filled_min.argmin(axis=0)
        combos = np.array(self.combos)
        vmax = np.take_along_axis(filled_max, imax[None], axis=0)[0]
        vmin = np.take_along_axis(filled_min, imin[None], axis=0)[0]
        vmax[~np.isfinite(vmax)] = np.nan
        vmin[~np.isfinite(vmin)] = np.nan
        return vmax, vmin, combos[imax], combos[imin]


def read_element_forces(source: Source, model: OutModel) -> ElementForces:
    """Read the end forces of every frame element for every load combination."""
    elem_ids = [e.id for e in model.frame_elements]
    index = {eid: i for i, eid in enumerate(elem_ids)}
    blocks: List[np.ndarray] = []
    combos: List[int] = []
    nodes = np.zeros((len(elem_ids), 2), dtype=np.int64)
    current: Optional[np.ndarray] = None
    in_sub = False       # inside "ANALYTICAL FORCE RESULTS FOR SUBSTRUCTURE"
    in_pile = False      # after "- Analytical Force Results for each Pile" (other tables reuse "Pile Number")
    table = False        # inside a force table
    row = -1
    for raw in _lines(source):
        if "RESULTS FOR LOAD COMBINATION" in raw:
            parts = raw.replace("*", " ").split()
            combos.append(int(parts[-1]))
            current = np.full((len(elem_ids), 2, 6), np.nan)
            blocks.append(current)
            in_sub = in_pile = table = False
            continue
        if current is None:
            continue
        head = raw[:2]
        if head in (" -", " *", " _"):
            if head == " *":
                if "ANALYTICAL FORCE RESULTS FOR SUBSTRUCTURE" in raw:
                    in_sub, in_pile, table = True, False, False
                elif any(ch.isalpha() for ch in raw):
                    in_sub = in_pile = table = False
                else:
                    table = False
            elif head == " _":
                table = False
            elif raw.startswith(" - "):
                if "- Analytical Force Results for each Pile" in raw:
                    in_pile = True
                table = in_sub or (in_pile and "- Pile Number" in raw)
            continue
        if not table:
            continue
        parts = raw.split()
        lead = 0
        while lead < len(parts) and lead < 4 and parts[lead].isdigit():
            lead += 1
        try:
            if lead == 4 and len(parts) >= 10:
                row = index.get(int(parts[0]), -1)
                if row >= 0:
                    current[row, 0] = [float(v) for v in parts[4:10]]
                    nodes[row, 0] = int(parts[2])
            elif lead in (2, 3) and len(parts) >= lead + 6 and row >= 0:
                current[row, 1] = [float(v) for v in parts[lead:lead + 6]]
                nodes[row, 1] = int(parts[1])
                row = -1
        except ValueError:
            continue
    if not blocks:
        raise ValueError("No load combination results found in the .out file.")
    return ElementForces(combos, elem_ids, index, np.stack(blocks), nodes)


# ---------------------------------------------------------------------------
# plane selection and members
# ---------------------------------------------------------------------------
@dataclass
class Member:
    label: str
    kind: str
    direction: int                    # 0, 1, 2: global axis the member runs along
    elems: List[int] = field(default_factory=list)       # element ids, ordered along the member
    stations: List[Tuple[float, float]] = field(default_factory=list)   # coordinate of the I and J end


@dataclass
class Plane:
    node: int
    axis: int                         # 0, 1, 2
    value: float                      # coordinate of the plane, model units
    nodes: List[int]
    frame_elems: List[int]            # frame elements lying in the plane
    cap_elems: List[int]              # pile cap elements the plane passes through (or lying in it)
    members: List[Member]

    @property
    def name(self) -> str:
        return f"{AXES[self.axis]} = {self.value / 12.0:.3f} ft (node {self.node})"


def _coord(node, axis: int) -> float:
    return (node.x, node.y, node.z)[axis]


def select_plane(model: OutModel, node: int, axis: str, tol: float = 1.0) -> Plane:
    """Everything on the plane normal to ``axis`` through ``node`` (within tol, model units)."""
    if node not in model.nodes:
        raise KeyError(node)
    ax = AXES.index(axis.upper())
    value = _coord(model.nodes[node], ax)
    on = {n.id for n in model.nodes.values() if abs(_coord(n, ax) - value) <= tol}
    frame = [e for e in model.frame_elements if e.i in on and e.j in on]
    cap = []
    for el in model.cap_elements:
        c = [_coord(model.nodes[i], ax) - value for i in el.nodes if i in model.nodes]
        if c and min(c) <= tol and max(c) >= -tol:
            cap.append(el.id)

    # members: same kind, same direction, connected
    groups: Dict[Tuple[str, int], list] = {}
    for e in frame:
        a, b = model.nodes[e.i], model.nodes[e.j]
        d = [abs(b.x - a.x), abs(b.y - a.y), abs(b.z - a.z)]
        groups.setdefault((e.kind, int(np.argmax(d))), []).append(e)
    members: List[Member] = []
    for (kind, direction), elems in groups.items():
        parent = {e.id: e.id for e in elems}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        by_node: Dict[int, int] = {}
        for e in elems:
            for n in (e.i, e.j):
                if n in by_node:
                    parent[find(e.id)] = find(by_node[n])
                else:
                    by_node[n] = e.id
        chains: Dict[int, list] = {}
        for e in elems:
            chains.setdefault(find(e.id), []).append(e)
        for chain in chains.values():
            chain.sort(key=lambda e: min(_coord(model.nodes[e.i], direction), _coord(model.nodes[e.j], direction)))
            first = model.nodes[chain[0].i]
            if kind == "pile":
                pile = max(model.nodes[chain[0].i].pile, model.nodes[chain[0].j].pile)
                label = f"Pile {pile}"
            else:
                others = [a for a in range(3) if a not in (direction, ax)]
                where = ", ".join(f"{AXES[a]} = {_coord(first, a) / 12.0:.2f} ft" for a in others)
                start = min(_coord(model.nodes[chain[0].i], direction), _coord(model.nodes[chain[0].j], direction))
                end = max(_coord(model.nodes[chain[-1].i], direction), _coord(model.nodes[chain[-1].j], direction))
                label = (f"{kind.capitalize()}, {AXES[direction]} {start / 12.0:.2f} to {end / 12.0:.2f} ft ({where}), "
                         f"elements {chain[0].id}-{chain[-1].id}")
            m = Member(label, kind, direction)
            for e in chain:
                m.elems.append(e.id)
                m.stations.append((_coord(model.nodes[e.i], direction), _coord(model.nodes[e.j], direction)))
            members.append(m)
    members.sort(key=lambda m: (m.kind, m.direction, m.label))
    return Plane(node, ax, value, sorted(on), [e.id for e in frame], cap, members)


def member_envelope(forces: ElementForces, member: Member, component: str, model: Optional[OutModel] = None):
    """Stations (model units), max, min and governing combinations along one member.

    The .out prints an element's two ends in its own order (piles: top end first), which is not
    always the I-J order of the connectivity table, so each value is placed at the node printed with it.
    """
    k = COMPONENTS.index(component)
    vmax, vmin, cmax, cmin = forces.envelope()
    xs, hi, lo, chi, clo, ids = [], [], [], [], [], []
    for eid, (si, sj) in zip(member.elems, member.stations):
        row = forces.index.get(eid)
        if row is None:
            continue
        for end, s in ((0, si), (1, sj)):
            printed = int(forces.nodes[row, end])
            if model is not None and printed in model.nodes:
                s = _coord(model.nodes[printed], member.direction)
            xs.append(s); hi.append(vmax[row, end, k]); lo.append(vmin[row, end, k])
            chi.append(int(cmax[row, end, k])); clo.append(int(cmin[row, end, k])); ids.append(eid)
        xs.append(None); hi.append(None); lo.append(None); chi.append(None); clo.append(None); ids.append(None)
    return xs, hi, lo, chi, clo, ids


def summary_rows(forces: ElementForces, plane: Plane):
    """Per member and component: (member label, component, max, element, combo, min, element, combo)."""
    vmax, vmin, cmax, cmin = forces.envelope()
    rows = []
    for m in plane.members:
        idx = [forces.index[e] for e in m.elems if e in forces.index]
        if not idx:
            continue
        for k, comp in enumerate(COMPONENTS):
            hi = vmax[idx, :, k]
            lo = vmin[idx, :, k]
            if np.all(np.isnan(hi)):
                continue
            a = np.unravel_index(np.nanargmax(hi), hi.shape)
            b = np.unravel_index(np.nanargmin(lo), lo.shape)
            rows.append((m.label, comp, float(hi[a]), m.elems[a[0]], int(cmax[idx[a[0]], a[1], k]),
                         float(lo[b]), m.elems[b[0]], int(cmin[idx[b[0]], b[1], k])))
    return rows


_PALETTE = ("#2a78d6", "#e34948", "#2e9b57", "#b0672b", "#7a5ea8", "#c2408f", "#3a9aa8", "#8a7a6a",
            "#d69a2a", "#4d6fa8", "#a83a3a", "#5f8f3a")


def envelope_figures(forces: ElementForces, plane: Plane, component: str, title: str = "",
                     width: Optional[int] = 1000, model: Optional[OutModel] = None) -> list:
    """One plotly figure per group of like members: max / min of ``component`` along each member."""
    import plotly.graph_objects as go

    k = COMPONENTS.index(component)
    groups: Dict[Tuple[str, int], List[Member]] = {}
    for m in plane.members:
        groups.setdefault((m.kind, m.direction), []).append(m)
    figs = []
    for (kind, direction), members in groups.items():
        fig = go.Figure()
        vertical = direction == 2
        any_data = False
        for n, m in enumerate(members):
            xs, hi, lo, chi, clo, ids = member_envelope(forces, m, component, model)
            if not xs:
                continue
            any_data = True
            st = [None if s is None else s / 12.0 for s in xs]
            color = _PALETTE[n % len(_PALETTE)]
            for vals, combos, dash, what in ((hi, chi, "solid", "max"), (lo, clo, "dot", "min")):
                text = [None if v is None else f"{m.label}<br>element {e}<br>{what} {component} = {v:,.2f} {UNITS[k]}"
                        f"<br>load combination {c}" for v, e, c in zip(vals, ids, combos)]
                x, y = (vals, st) if vertical else (st, vals)
                fig.add_trace(go.Scatter(
                    x=x, y=y, mode="lines+markers", name=m.label, legendgroup=m.label, showlegend=what == "max",
                    line=dict(color=color, dash=dash, width=2), marker=dict(size=4),
                    text=text, hovertemplate="%{text}<extra></extra>", connectgaps=False))
        if not any_data:
            continue
        force_axis = dict(title=f"{component} ({UNITS[k]}), solid = max, dotted = min", zeroline=True,
                          zerolinecolor="#888")
        along = dict(title=f"{AXES[direction]} (ft)" + (", down" if vertical else ""))
        if vertical:
            along["autorange"] = "reversed"
        fig.update_layout(
            title=f"{kind.capitalize()} members along {AXES[direction]}: {component}, max / min over all load combinations"
                  + (f" ({title})" if title else ""),
            xaxis=force_axis if vertical else along, yaxis=along if vertical else force_axis,
            height=620 if vertical else 400, width=width, autosize=width is None,
            margin=dict(l=60, r=20, t=50, b=50), legend=dict(font=dict(size=10)))
        figs.append(fig)
    return figs
