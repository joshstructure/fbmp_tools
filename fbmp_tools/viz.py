"""Plotly 3D views of an FB-MultiPier model, load arrows, section cuts, envelopes.

Coordinates are inches, forces kips, moments kip-ft.  FB-MultiPier's global Z
points DOWN, so the z axis is reversed to show piles hanging below the cap.
Only plain plotly.graph_objects is used (works in Colab).
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import plotly.graph_objects as go

BEFORE_COLOR = "#2a78d6"   # blue
AFTER_COLOR = "#e34948"    # red
CUT_COLOR = "#eb6834"

_PILE_COLOR = "#8a7a6a"
_PIER_COLOR = "#2f3b4a"
_CAP_COLOR = "#9aa0a6"
_GROUP_STYLE = {
    # group -> (trace name, color, size)
    "cap": ("Cap nodes", "#6b7178", 2.5),
    "pier": ("Pier nodes", "#2f3b4a", 4),
    "bearingL": ("Bearings", "#2e9b57", 6),
    "bearingR": ("Bearings", "#2e9b57", 6),
    "pile": ("Pile nodes", "#a08a74", 2.5),
}
_GROUP_WORDS = {
    "pile": "pile node", "cap": "pile cap node", "pier": "pier node",
    "bearingL": "bearing (left)", "bearingR": "bearing (right)",
}
_COMPONENTS = ("Fx", "Fy", "Fz", "Mx", "My", "Mz")


def _segments(pairs: Iterable[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]):
    """Flatten point pairs into None-separated x, y, z lists."""
    xs: List[Optional[float]] = []
    ys: List[Optional[float]] = []
    zs: List[Optional[float]] = []
    for a, b in pairs:
        xs += [a[0], b[0], None]
        ys += [a[1], b[1], None]
        zs += [a[2], b[2], None]
    return xs, ys, zs


def _node_text(n) -> str:
    parts = [f"Node {n.id}", _GROUP_WORDS.get(n.group, n.group)]
    if n.pile:
        parts.append(f"pile {n.pile}")
    parts.append(f"x, y, z = {n.x:.2f}, {n.y:.2f}, {n.z:.2f}")
    if not n.active:
        parts.append("inactive")
    return "<br>".join(parts)


def _mid_trace(name: str, mids, texts, color: str) -> go.Scatter3d:
    return go.Scatter3d(
        x=[m[0] for m in mids], y=[m[1] for m in mids], z=[m[2] for m in mids],
        mode="markers", name=name, legendgroup=name,
        marker=dict(size=3, color=color, opacity=0.35, symbol="square"),
        text=texts, hovertemplate="%{text}<extra></extra>",
    )


def model_figure(model, show_pile_nodes: bool = True, max_pile_depth: Optional[float] = None,
                 title: Optional[str] = None, height: int = 760, scale: float = 1.0 / 12.0,
                 unit: str = "ft", show_elements: bool = True, width: Optional[int] = None) -> go.Figure:
    """3D view of the whole model: every node and every element, with hover labels.

    Coordinates are multiplied by ``scale`` for display (default: inches to feet).
    ``max_pile_depth`` (model units below the pile cap) hides the lower part of long piles.
    """
    nodes = model.nodes
    cap_z = model.cap_z
    z_limit = None if max_pile_depth is None else cap_z + max_pile_depth
    k = scale
    fig_width = width

    def xyz(n):
        return (n.x * k, n.y * k, n.z * k)

    def hidden(n) -> bool:
        return z_limit is not None and n.group == "pile" and n.z > z_limit

    def node_text(n) -> str:
        parts = [f"Node {n.id}", _GROUP_WORDS.get(n.group, n.group)]
        if n.pile:
            parts.append(f"pile {n.pile}" + (" (head)" if n.pile_head else ""))
        parts.append(f"x, y, z = {n.x * k:.3f}, {n.y * k:.3f}, {n.z * k:.3f} {unit}")
        if not n.active:
            parts.append("inactive")
        return "<br>".join(parts)

    # frame elements, one line trace and one hover trace per kind
    kinds: Dict[str, Dict[str, list]] = {}
    for el in model.frame_elements:
        a, b = nodes.get(el.i), nodes.get(el.j)
        if a is None or b is None or hidden(a) or hidden(b):
            continue
        rec = kinds.setdefault(el.kind, {"pairs": [], "mid": [], "txt": []})
        pa, pb = xyz(a), xyz(b)
        rec["pairs"].append((pa, pb))
        rec["mid"].append(tuple((p + q) / 2 for p, q in zip(pa, pb)))
        length = math.dist(pa, pb)
        rec["txt"].append(f"Element {el.id}<br>{el.kind} element<br>nodes {el.i} - {el.j}"
                          f"<br>section {el.section}<br>length {length:.3f} {unit}")

    # pile cap shell elements: every edge of the 3 x 3 node grid, drawn once
    cap_edges: Dict[Tuple[int, int], None] = {}
    cap_mid, cap_txt = [], []
    grid_edges = ((0, 1), (1, 2), (3, 4), (4, 5), (6, 7), (7, 8), (0, 3), (3, 6), (1, 4), (4, 7), (2, 5), (5, 8))
    outline = ((0, 2), (2, 8), (8, 6), (6, 0))
    for ce in model.cap_elements:
        ids = ce.nodes
        if any(i not in nodes for i in ids):
            continue
        for p, q in (grid_edges if len(ids) == 9 else outline):
            e = (ids[p], ids[q]) if ids[p] < ids[q] else (ids[q], ids[p])
            cap_edges.setdefault(e)
        pts = [xyz(nodes[i]) for i in ids]
        cap_mid.append(tuple(sum(c) / len(pts) for c in zip(*pts)))
        cap_txt.append(f"Element {ce.id}<br>pile cap shell element<br>nodes {', '.join(str(i) for i in ids)}"
                       f"<br>thickness {ce.thick:.3f} ft (self weight {ce.thick_sw:.3f} ft)")
    cap_pairs = [(xyz(nodes[a]), xyz(nodes[b])) for a, b in cap_edges]

    fig = go.Figure()

    def line(name, pairs, color, width):
        if not pairs:
            return
        x, y, z = _segments(pairs)
        fig.add_trace(go.Scatter3d(x=x, y=y, z=z, mode="lines", name=name, legendgroup=name,
                                   line=dict(color=color, width=width), hoverinfo="skip"))

    frame_style = {"pile": ("Piles", _PILE_COLOR, 4), "pier": ("Pier", _PIER_COLOR, 6),
                   "bent cap": ("Bent cap", _PIER_COLOR, 7), "extra member": ("Extra members", "#b0672b", 5)}
    for kind, rec in kinds.items():
        name, color, width = frame_style.get(kind, (kind.capitalize(), "#7a5ea8", 4))
        line(name, rec["pairs"], color, width)
    line("Pile cap elements", cap_pairs, _CAP_COLOR, 1.5)

    # nodes: every node of the model, grouped for the legend
    styles = {"Pile cap nodes": ("#6b7178", 2.5), "Pile heads": ("#6b4f3a", 5), "Pier nodes": ("#2f3b4a", 4),
              "Bearings": ("#2e9b57", 6), "Pile nodes": ("#a08a74", 2.5), "Bent cap nodes": ("#2f3b4a", 4)}
    names = {"cap": "Pile cap nodes", "pier": "Pier nodes", "bearingL": "Bearings", "bearingR": "Bearings",
             "Bent Cap": "Bent cap nodes"}
    buckets: Dict[str, List] = {}
    shown = 0
    for n in nodes.values():
        if hidden(n):
            continue
        if n.group == "pile":
            if n.pile_head:
                name = "Pile heads"
            elif show_pile_nodes:
                name = "Pile nodes"
            else:
                continue
        else:
            name = names.get(n.group, f"{n.group} nodes")
        buckets.setdefault(name, []).append(n)
        shown += 1
    for name, lst in buckets.items():
        color, size = styles.get(name, ("#7a5ea8", 4))
        pts = [xyz(n) for n in lst]
        fig.add_trace(go.Scatter3d(
            x=[p[0] for p in pts], y=[p[1] for p in pts], z=[p[2] for p in pts],
            mode="markers", name=name, legendgroup=name, marker=dict(size=size, color=color),
            text=[node_text(n) for n in lst], hovertemplate="%{text}<extra></extra>"))

    # element hover points (at mid length / at the element center)
    n_elem = len(cap_mid)
    if show_elements:
        for kind, rec in kinds.items():
            if kind == "pile" and not show_pile_nodes:
                continue
            name, color, _ = frame_style.get(kind, (kind.capitalize(), "#7a5ea8", 4))
            fig.add_trace(_mid_trace(name + " (element labels)", rec["mid"], rec["txt"], color))
        if cap_mid:
            fig.add_trace(_mid_trace("Pile cap (element labels)", cap_mid, cap_txt, "#4d6fa8"))
    n_elem += sum(len(rec["pairs"]) for rec in kinds.values())

    pts_all = [xyz(n) for lst in buckets.values() for n in lst] or [(0.0, 0.0, 0.0)]
    lo = [min(p[i] for p in pts_all) for i in range(3)]
    hi = [max(p[i] for p in pts_all) for i in range(3)]
    big = max(max(h - l for h, l in zip(hi, lo)), 1e-6)
    pad = 0.08 * big                     # room for load arrows and labels
    spans = [h - l + 2 * pad for h, l in zip(hi, lo)]
    big = max(spans)
    x0, x1, y0, y1 = model.cap_extents()
    heading = title or getattr(model, "title", "") or None
    fig.update_layout(
        title=heading, height=height, width=fig_width, autosize=fig_width is None, margin=dict(l=0, r=0, t=40 if heading else 10, b=0),
        uirevision="model",
        scene=dict(
            # true proportions, scaled so the longest side of the model fills the view
            # (aspectmode "data" leaves the camera inside a tall, slender model)
            aspectmode="manual", aspectratio=dict(x=1.7 * spans[0] / big, y=1.7 * spans[1] / big, z=1.7 * spans[2] / big),
            xaxis=dict(title=f"X ({unit})", range=[lo[0] - pad, hi[0] + pad]),
            yaxis=dict(title=f"Y ({unit})", range=[lo[1] - pad, hi[1] + pad]),
            zaxis=dict(title=f"Z ({unit}, down)", range=[hi[2] + pad, lo[2] - pad]),
            camera=dict(eye=dict(x=-1.1, y=-1.5, z=0.7)),
        ),
        legend=dict(itemsizing="constant"),
        meta={"span": float(max(x1 - x0, y1 - y0, 1.0)) * k, "cap_z": float(cap_z) * k, "scale": k, "unit": unit,
              "nodes_shown": shown, "nodes_total": len(nodes), "elements_shown": n_elem,
              "elements_total": len(model.frame_elements) + len(model.cap_elements)},
    )
    return fig


def _scale(fig: go.Figure) -> float:
    meta = fig.layout.meta
    return float(meta.get("scale", 1.0)) if isinstance(meta, dict) else 1.0


def _span(fig: go.Figure) -> float:
    meta = fig.layout.meta
    if isinstance(meta, dict) and meta.get("span"):
        return float(meta["span"])
    xs, ys = [], []
    for t in fig.data:
        if t.x is not None and t.y is not None:
            xs += [v for v in t.x if v is not None]
            ys += [v for v in t.y if v is not None]
    if not xs:
        return 100.0
    return max(max(xs) - min(xs), max(ys) - min(ys), 1.0)


def _drop(fig: go.Figure, tag: str) -> None:
    fig.data = tuple(t for t in fig.data if t.meta != tag)


def _perp(d):
    """Two unit vectors perpendicular to the unit vector d."""
    ref = (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = (d[1] * ref[2] - d[2] * ref[1], d[2] * ref[0] - d[0] * ref[2], d[0] * ref[1] - d[1] * ref[0])
    n = math.sqrt(sum(c * c for c in u))
    u = tuple(c / n for c in u)
    w = (d[1] * u[2] - d[2] * u[1], d[2] * u[0] - d[0] * u[2], d[0] * u[1] - d[1] * u[0])
    return u, w


class _Glyphs:
    """Collects the geometry of one colour of arrows: shafts, heads, hover points, labels."""

    RING = 10

    def __init__(self):
        self.sx, self.sy, self.sz = [], [], []
        self.mx, self.my, self.mz, self.mi, self.mj, self.mk, self.mt = [], [], [], [], [], [], []
        self.hx, self.hy, self.hz, self.ht = [], [], [], []
        self.tx, self.ty, self.tz, self.tt = [], [], [], []

    def path(self, pts):
        for p in pts:
            self.sx.append(p[0]); self.sy.append(p[1]); self.sz.append(p[2])
        self.sx.append(None); self.sy.append(None); self.sz.append(None)

    def head(self, tip, d, size, text):
        """Cone with its point at tip, pointing along the unit vector d."""
        base = tuple(t - c * size for t, c in zip(tip, d))
        u, w = _perp(d)
        r = 0.32 * size
        first = len(self.mx)
        self.mx.append(tip[0]); self.my.append(tip[1]); self.mz.append(tip[2])
        for q in range(self.RING):
            ang = 2 * math.pi * q / self.RING
            cu, cw = r * math.cos(ang), r * math.sin(ang)
            self.mx.append(base[0] + cu * u[0] + cw * w[0])
            self.my.append(base[1] + cu * u[1] + cw * w[1])
            self.mz.append(base[2] + cu * u[2] + cw * w[2])
        self.mx.append(base[0]); self.my.append(base[1]); self.mz.append(base[2])
        center = first + self.RING + 1
        for q in range(self.RING):
            p1, p2 = first + 1 + q, first + 1 + (q + 1) % self.RING
            self.mi += [first, center]; self.mj += [p1, p2]; self.mk += [p2, p1]
        self.mt += [text] * (self.RING + 2)
        return base

    def hover(self, p, text):
        self.hx.append(p[0]); self.hy.append(p[1]); self.hz.append(p[2]); self.ht.append(text)

    def label(self, p, text):
        self.tx.append(p[0]); self.ty.append(p[1]); self.tz.append(p[2]); self.tt.append(text)

    def add_to(self, fig, name, color, legend=True):
        if not self.sx:
            return
        fig.add_trace(go.Scatter3d(
            x=self.sx, y=self.sy, z=self.sz, mode="lines", name=name, legendgroup=name, meta="load",
            showlegend=legend, line=dict(color=color, width=6), hoverinfo="skip"))
        fig.add_trace(go.Mesh3d(
            x=self.mx, y=self.my, z=self.mz, i=self.mi, j=self.mj, k=self.mk, color=color, flatshading=True,
            name=name, legendgroup=name, showlegend=False, meta="load",
            text=self.mt, hovertemplate="%{text}<extra></extra>",
            lighting=dict(ambient=0.9, diffuse=0.3, specular=0.0)))
        fig.add_trace(go.Scatter3d(
            x=self.hx, y=self.hy, z=self.hz, mode="markers", name=name, legendgroup=name, showlegend=False,
            meta="load", marker=dict(size=4, color=color), text=self.ht, hovertemplate="%{text}<extra></extra>"))
        if self.tt:
            fig.add_trace(go.Scatter3d(
                x=self.tx, y=self.ty, z=self.tz, mode="text", text=self.tt, name=name + " labels",
                legendgroup=name, showlegend=False, meta="load", textfont=dict(color=color, size=12),
                hoverinfo="skip"))


def _perp(d):
    """Unit vectors u, w perpendicular to the unit vector d, with u x w = d (right-hand rule)."""
    ref = (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = (d[1] * ref[2] - d[2] * ref[1], d[2] * ref[0] - d[0] * ref[2], d[0] * ref[1] - d[1] * ref[0])
    n = math.sqrt(sum(c * c for c in u))
    u = tuple(c / n for c in u)
    w = (d[1] * u[2] - d[2] * u[1], d[2] * u[0] - d[0] * u[2], d[0] * u[1] - d[1] * u[0])
    return u, w


def add_loads(fig: go.Figure, changes, case: int, show_before: bool = True, show_after: bool = True,
              components: Sequence[str] = ("Fx", "Fy", "Fz", "Mx", "My", "Mz"), length: Optional[float] = None,
              label_min: float = 0.0) -> go.Figure:
    """Draw the loads of one case, component by component: before mirroring in blue, after in red.

    Every force component is its own straight arrow along its global axis (Fz is vertical; +Fz is
    down, as in FB-MultiPier), starting at the loaded node or bearing and pointing the way it acts.
    Length is proportional to the value; the largest force component is ``length`` long (display
    units; default 12 % of the model size).  Every moment component is a curved arrow around its
    global axis, turning by the right-hand rule, with radius proportional to the value.  Each arrow
    is labelled with its signed value and shows all six components on hover.
    """
    _drop(fig, "load")
    k_scale = _scale(fig)
    chosen = [i for i, c in enumerate(_COMPONENTS) if c in components]
    if not length:
        length = 0.12 * _span(fig)
    sets = []
    for show, name, color, after in ((show_before, "Before (base)", BEFORE_COLOR, False),
                                     (show_after, "After (mirrored)", AFTER_COLOR, True)):
        if not show:
            continue
        items = []
        for ch in changes:
            if ch.line.case != case:
                continue
            pos = ch.new_pos if after else ch.pos
            if pos is None:
                continue
            vals = [float(x) for x in (ch.after if after else ch.before)]
            if not any(vals[i] for i in chosen):
                continue
            items.append((ch, tuple(c * k_scale for c in pos), vals, ch.new_label if after else ch.label))
        sets.append((name, color, after, items))
    fmax = max([abs(it[2][i]) for s in sets for it in s[3] for i in chosen if i < 3] or [0.0])
    mmax = max([abs(it[2][i]) for s in sets for it in s[3] for i in chosen if i >= 3] or [0.0])
    if fmax <= 0.0 and mmax <= 0.0:
        return fig
    for name, color, after, items in sets:
        if not items:
            continue
        g = _Glyphs()
        for ch, pos, vals, lab in items:
            where = "bearing" if ch.line.section == "LOADBP" else "node"
            detail = (f"{ch.line.type}, case {ch.line.case}, {'after mirroring' if after else 'before'}<br>{where} {lab}<br>"
                      + ", ".join(f"{c} {x:.4g}" for c, x in zip(_COMPONENTS[:3], vals[:3])) + " kips<br>"
                      + ", ".join(f"{c} {x:.4g}" for c, x in zip(_COMPONENTS[3:], vals[3:])) + " kip-ft")
            for i in chosen:
                value = vals[i]
                if value == 0.0:
                    continue
                comp = _COMPONENTS[i]
                axis = [0.0, 0.0, 0.0]
                axis[i % 3] = 1.0 if value > 0 else -1.0
                axis = tuple(axis)
                if i < 3:
                    note = " (+Fz is down)" if i == 2 else ""
                    text = f"<b>{comp} = {value:.4g} kips</b>{note}<br>" + detail
                    L = max(length * abs(value) / fmax, 0.03 * length)
                    tip = tuple(p + c * L for p, c in zip(pos, axis))
                    base = g.head(tip, axis, min(L, max(0.28 * L, 0.035 * length)), text)
                    g.path([pos, base])
                    g.hover(pos, text)
                    g.hover(tuple(p + c * L * 0.5 for p, c in zip(pos, axis)), text)
                    if abs(value) >= label_min:
                        g.label(tuple(t + c * 0.03 * length for t, c in zip(tip, axis)), f"{comp} {value:.3g} k")
                else:
                    text = f"<b>{comp} = {value:.4g} kip-ft</b> (right-hand rule about {comp[1].upper()})<br>" + detail
                    u, w = _perp(axis)
                    radius = max(0.45 * length * abs(value) / mmax, 0.12 * length)
                    sweep = math.radians(270.0)
                    steps = 28
                    pts = []
                    for q in range(steps + 1):
                        ang = sweep * q / steps
                        pts.append(tuple(p + radius * (math.cos(ang) * cu + math.sin(ang) * cw)
                                         for p, cu, cw in zip(pos, u, w)))
                    end = pts[-1]
                    tangent = tuple(-math.sin(sweep) * cu + math.cos(sweep) * cw for cu, cw in zip(u, w))
                    size = 0.45 * radius
                    g.head(tuple(e + c * size * 0.5 for e, c in zip(end, tangent)), tangent, size, text)
                    g.path(pts)
                    g.hover(pts[steps // 3], text)
                    g.hover(pts[2 * steps // 3], text)
                    if abs(value) >= label_min:
                        out = tuple((e - p) / radius for e, p in zip(end, pos))
                        g.label(tuple(e + c * 0.25 * radius for e, c in zip(end, out)), f"{comp} {value:.3g} k-ft")
        g.add_to(fig, name, color)
    return fig


def add_cut(fig: go.Figure, p0: Sequence[float], p1: Sequence[float], z: float,
            node_xyz: Optional[Sequence[Tuple[float, float, float]]] = None,
            name: str = "Section cut") -> go.Figure:
    """Draw a section-cut line at elevation z, with optional highlighted nodes."""
    _drop(fig, "cut")
    k_scale = _scale(fig)
    p0 = (p0[0] * k_scale, p0[1] * k_scale)
    p1 = (p1[0] * k_scale, p1[1] * k_scale)
    z = z * k_scale
    if node_xyz:
        node_xyz = [tuple(c * k_scale for c in p) for p in node_xyz]
    fig.add_trace(go.Scatter3d(
        x=[p0[0], p1[0]], y=[p0[1], p1[1]], z=[z, z], mode="lines", name=name, meta="cut",
        line=dict(color=CUT_COLOR, width=8), hoverinfo="name"))
    if node_xyz:
        fig.add_trace(go.Scatter3d(
            x=[p[0] for p in node_xyz], y=[p[1] for p in node_xyz], z=[p[2] for p in node_xyz],
            mode="markers", name=name + " nodes", meta="cut",
            marker=dict(size=6, color=CUT_COLOR, line=dict(color="#ffffff", width=1)),
            hovertemplate="x, y, z = %{x:.2f}, %{y:.2f}, %{z:.2f}<extra>" + name + "</extra>"))
    return fig


def add_highlight(fig: go.Figure, segments, points, picked=None, name: str = "On the cut plane") -> go.Figure:
    """Highlight elements (pairs of xyz, model units), nodes and the picked node."""
    _drop(fig, "cut")
    k = _scale(fig)
    x, y, z = _segments([(tuple(c * k for c in a), tuple(c * k for c in b)) for a, b in segments])
    if x:
        fig.add_trace(go.Scatter3d(x=x, y=y, z=z, mode="lines", name=name, meta="cut",
                                   line=dict(color=CUT_COLOR, width=9), hoverinfo="skip"))
    if points:
        fig.add_trace(go.Scatter3d(
            x=[p[0] * k for p in points], y=[p[1] * k for p in points], z=[p[2] * k for p in points],
            mode="markers", name=name + " (nodes)", meta="cut", marker=dict(size=3, color=CUT_COLOR), hoverinfo="skip"))
    if picked is not None:
        fig.add_trace(go.Scatter3d(
            x=[picked[0] * k], y=[picked[1] * k], z=[picked[2] * k], mode="markers", name="Picked node", meta="cut",
            marker=dict(size=9, color="#111111", symbol="diamond"), hoverinfo="skip"))
    return fig


def envelope_figure(stations: Sequence[float], series: Dict[str, Sequence[float]], title: str,
                    y_title: str, x_title: str = "Distance along cut (ft)", height: int = 380,
                    width: Optional[int] = None) -> go.Figure:
    """2D max/min (or any named series) plot along a section cut."""
    fig = go.Figure()
    colors = {"Max": AFTER_COLOR, "Min": BEFORE_COLOR}
    palette = ["#2a78d6", "#e34948", "#2e9b57", "#8a5cc2", "#d19a1f"]
    fill_pair = set(series) == {"Max", "Min"} and len(series) == 2
    order = ["Max", "Min"] if fill_pair else list(series)
    for k, name in enumerate(order):
        color = colors.get(name, palette[k % len(palette)])
        kw = {}
        if fill_pair and name == "Min":
            kw = dict(fill="tonexty", fillcolor="rgba(120,120,120,0.12)")
        fig.add_trace(go.Scatter(
            x=list(stations), y=list(series[name]), mode="lines+markers", name=name,
            line=dict(color=color, width=2), marker=dict(size=5),
            hovertemplate=f"{name}<br>station %{{x:.1f}}<br>%{{y:.2f}}<extra></extra>", **kw))
    fig.add_hline(y=0, line=dict(color="#888888", width=1, dash="dot"))
    fig.update_layout(title=title, height=height, margin=dict(l=60, r=20, t=50, b=50),
                      xaxis=dict(title=x_title), yaxis=dict(title=y_title, zeroline=False),
                      hovermode="x unified", legend=dict(orientation="h", y=1.1, x=0))
    return fig
