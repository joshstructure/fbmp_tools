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
        mode="markers", name=name, legendgroup=name, visible="legendonly",
        marker=dict(size=2.5, color=color, opacity=0.15),
        text=texts, hovertemplate="%{text}<extra></extra>",
    )


def model_figure(model, show_pile_nodes: bool = False, max_pile_depth: Optional[float] = None,
                 title: Optional[str] = None, height: int = 720) -> go.Figure:
    """Build the 3D view of the structural model (piles, pier, cap mesh, nodes)."""
    nodes = model.nodes
    cap_z = model.cap_z
    z_limit = None if max_pile_depth is None else cap_z + max_pile_depth

    def keep(nid: int) -> bool:
        n = nodes.get(nid)
        if n is None:
            return False
        return not (z_limit is not None and n.group == "pile" and n.z > z_limit)

    pile_pairs, pier_pairs = [], []
    pile_mid, pile_txt, pier_mid, pier_txt = [], [], [], []
    for el in model.frame_elements:
        a, b = nodes.get(el.i), nodes.get(el.j)
        if a is None or b is None:
            continue
        if el.kind == "pile" and z_limit is not None and (a.z > z_limit or b.z > z_limit):
            continue
        pa, pb = (a.x, a.y, a.z), (b.x, b.y, b.z)
        mid = ((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2)
        txt = f"Element {el.id}<br>{el.kind} element<br>nodes {el.i}-{el.j}<br>section {el.section}"
        if el.kind == "pile":
            pile_pairs.append((pa, pb))
            if show_pile_nodes:
                pile_mid.append(mid)
                pile_txt.append(txt)
        else:
            pier_pairs.append((pa, pb))
            pier_mid.append(mid)
            pier_txt.append(txt)

    cap_edges: Dict[Tuple[int, int], Tuple[int, int]] = {}
    cap_mid, cap_txt = [], []
    for ce in model.cap_elements:
        ids = ce.nodes
        if len(ids) < 9:
            continue
        corners = (ids[0], ids[2], ids[8], ids[6])
        pts = [nodes.get(c) for c in corners]
        if any(p is None for p in pts):
            continue
        for k in range(4):
            a, b = corners[k], corners[(k + 1) % 4]
            cap_edges.setdefault((a, b) if a < b else (b, a), (a, b))
        cap_mid.append((sum(p.x for p in pts) / 4, sum(p.y for p in pts) / 4, sum(p.z for p in pts) / 4))
        cap_txt.append(f"Element {ce.id}<br>cap shell element<br>nodes {', '.join(str(i) for i in ids)}"
                       f"<br>thickness {ce.thick:.3f} ft (self weight {ce.thick_sw:.3f} ft)")
    cap_pairs = [((nodes[a].x, nodes[a].y, nodes[a].z), (nodes[b].x, nodes[b].y, nodes[b].z))
                 for a, b in cap_edges.values()]

    fig = go.Figure()

    def line(name, pairs, color, width):
        x, y, z = _segments(pairs)
        fig.add_trace(go.Scatter3d(x=x, y=y, z=z, mode="lines", name=name, legendgroup=name,
                                   line=dict(color=color, width=width), hoverinfo="skip"))

    line("Piles", pile_pairs, _PILE_COLOR, 4)
    line("Pier", pier_pairs, _PIER_COLOR, 6)
    line("Pile cap mesh", cap_pairs, _CAP_COLOR, 1.5)

    # node markers grouped by trace name
    buckets: Dict[str, List] = {"Cap nodes": [], "Pile heads": [], "Pier nodes": [], "Bearings": [], "Pile nodes": []}
    styles = {"Cap nodes": ("#6b7178", 2.5), "Pile heads": ("#6b4f3a", 5), "Pier nodes": ("#2f3b4a", 4),
              "Bearings": ("#2e9b57", 6), "Pile nodes": ("#a08a74", 2.5)}
    for n in nodes.values():
        if not keep(n.id):
            continue
        if n.group == "pile":
            if n.pile_head:
                buckets["Pile heads"].append(n)
            elif show_pile_nodes:
                buckets["Pile nodes"].append(n)
        elif n.group in _GROUP_STYLE:
            buckets[_GROUP_STYLE[n.group][0]].append(n)
    for name, lst in buckets.items():
        if not lst or (name == "Pile nodes" and not show_pile_nodes):
            continue
        color, size = styles[name]
        fig.add_trace(go.Scatter3d(
            x=[n.x for n in lst], y=[n.y for n in lst], z=[n.z for n in lst],
            mode="markers", name=name, legendgroup=name,
            marker=dict(size=size, color=color),
            text=[_node_text(n) for n in lst], hovertemplate="%{text}<extra></extra>",
            visible=True,
        ))

    if pier_mid:
        fig.add_trace(_mid_trace("Pier elements", pier_mid, pier_txt, _PIER_COLOR))
    if pile_mid:
        fig.add_trace(_mid_trace("Pile elements", pile_mid, pile_txt, _PILE_COLOR))
    if cap_mid:
        fig.add_trace(_mid_trace("Cap elements", cap_mid, cap_txt, _CAP_COLOR))

    x0, x1, y0, y1 = model.cap_extents()
    fig.update_layout(
        title=title or getattr(model, "title", "") or None,
        height=height, margin=dict(l=0, r=0, t=40 if (title or getattr(model, "title", "")) else 10, b=0),
        uirevision="model",
        scene=dict(
            aspectmode="data",
            xaxis=dict(title="X (in)"), yaxis=dict(title="Y (in)"),
            zaxis=dict(title="Z (in, down)", autorange="reversed"),
        ),
        legend=dict(itemsizing="constant"),
        meta={"span": float(max(x1 - x0, y1 - y0, 1.0)), "cap_z": float(cap_z)},
    )
    return fig


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


def add_loads(fig: go.Figure, changes, case: int, show_before: bool = True, show_after: bool = True,
              components: Sequence[str] = ("Fx", "Fy", "Fz"), length: Optional[float] = None,
              label_min: float = 0.0) -> go.Figure:
    """Draw load arrows (before = blue, after = red) for one load case; returns fig."""
    _drop(fig, "load")
    idx = [_COMPONENTS.index(c) for c in components if c in _COMPONENTS]
    if length is None:
        length = 0.12 * _span(fig)
    sets = []   # (name, color, items)
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
            vals = ch.after if after else ch.before
            vec = [0.0, 0.0, 0.0]
            for c in idx:
                if c < 3 and c < len(vals):
                    vec[c] = float(vals[c])
            mag = math.sqrt(sum(v * v for v in vec))
            if mag <= 0.0:
                continue
            items.append((ch, pos, vec, mag, vals, ch.new_label if after else ch.label))
        sets.append((name, color, items))
    mags = [it[3] for _, _, items in sets for it in items]
    if not mags:
        return fig
    vmax = max(mags)
    floor = 0.15 * length
    for name, color, items in sets:
        if not items:
            continue
        sx, sy, sz = [], [], []
        hx, hy, hz, hu, hv, hw = [], [], [], [], [], []
        tx, ty, tz, tt, th = [], [], [], [], []
        for ch, pos, vec, mag, vals, lab in items:
            L = max(length * mag / vmax, floor)
            d = [v / mag for v in vec]
            tail = pos
            # FB-MultiPier forces act toward +direction; arrow points along the force, tip at pos + L*d
            tip = (pos[0] + d[0] * L, pos[1] + d[1] * L, pos[2] + d[2] * L)
            head = min(0.35 * L, 0.4 * length)
            base = (tip[0] - d[0] * head, tip[1] - d[1] * head, tip[2] - d[2] * head)
            sx += [tail[0], tip[0], None]
            sy += [tail[1], tip[1], None]
            sz += [tail[2], tip[2], None]
            hx.append(tip[0]); hy.append(tip[1]); hz.append(tip[2])
            hu.append(d[0] * head); hv.append(d[1] * head); hw.append(d[2] * head)
            if mag >= label_min:
                off = 0.04 * length
                tx.append(tip[0] + d[0] * off); ty.append(tip[1] + d[1] * off); tz.append(tip[2] + d[2] * off)
                tt.append(f"{mag:.3g}")
            hover = [f"{ch.line.type} (case {ch.line.case})", f"{ch.line.section} target {lab}"]
            hover.append("<br>".join(f"{c} = {v:.2f}" for c, v in zip(_COMPONENTS, vals)))
            th.append("<br>".join(hover))
        fig.add_trace(go.Scatter3d(
            x=sx, y=sy, z=sz, mode="lines", name=name, legendgroup=name, meta="load",
            line=dict(color=color, width=5), hoverinfo="skip"))
        # arrow heads: a diamond at the tip (cones do not size reliably across plotly versions)
        fig.add_trace(go.Scatter3d(
            x=hx, y=hy, z=hz, mode="markers", name=name, legendgroup=name, showlegend=False, meta="load",
            marker=dict(size=5, color=color, symbol="diamond"), hoverinfo="skip"))
        # tail markers carry hover text for every load; labels only where above label_min
        tails = [it[1] for it in items]
        fig.add_trace(go.Scatter3d(
            x=[p[0] for p in tails], y=[p[1] for p in tails], z=[p[2] for p in tails],
            mode="markers", name=name, legendgroup=name, showlegend=False, meta="load",
            marker=dict(size=3, color=color, opacity=0.6),
            text=th, hovertemplate="%{text}<extra></extra>"))
        if tt:
            fig.add_trace(go.Scatter3d(
                x=tx, y=ty, z=tz, mode="text", text=tt, name=name + " labels", legendgroup=name,
                showlegend=False, meta="load", textfont=dict(color=color, size=11), hoverinfo="skip"))
    return fig


def add_cut(fig: go.Figure, p0: Sequence[float], p1: Sequence[float], z: float,
            node_xyz: Optional[Sequence[Tuple[float, float, float]]] = None,
            name: str = "Section cut") -> go.Figure:
    """Draw a section-cut line at elevation z, with optional highlighted nodes."""
    _drop(fig, "cut")
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


def envelope_figure(stations: Sequence[float], series: Dict[str, Sequence[float]], title: str,
                    y_title: str, x_title: str = "Distance along cut (in)", height: int = 380) -> go.Figure:
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
