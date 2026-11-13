"""Notebook panels (ipywidgets) for the FB-MultiPier tools.

    app = FbmpApp()
    app.import_panel()    # 1. base .in and .out
    app.mirror_panel()    # 2. choose load cases, mirror, audit, download
    app.view_panel()      # 3. 3D model with loads before / after
    app.cut_panel()       # 4. section cut forces from result files
"""
from __future__ import annotations

import html
import io
import os
import zipfile
from typing import Dict, List, Optional

import ipywidgets as W
import numpy as np
from IPython.display import HTML, display

from . import colab_upload, cuts, elemforces, viz
from .infile import COMPONENTS, InFile, load_family, parse_in
from .mirror import DEFAULT_FAMILIES, DIRECTIONS, MirrorResult, format_report, mirror_loads
from .outfile import OutModel, read_model, read_results

_LEVEL_COLOR = {"ok": "#c9efc9", "check": "#fff3a6", "problem": "#f6b4b4", "na": "#eeeeee"}
_WIDE = W.Layout(width="100%")
_STYLE = {"description_width": "170px"}
FT = 12.0     # model length units (inches) per foot; everything on screen is in feet
AXIS_WORDS = {"X": "X", "Y": "Y", "Z": "Z (down)"}


def _uploaded(widget) -> Optional[tuple]:
    """(name, bytes) of the first uploaded file, for ipywidgets 7 and 8."""
    value = widget.value
    if not value:
        return None
    if isinstance(value, dict):                       # ipywidgets 7 (Colab)
        name, item = next(iter(value.items()))
        return name, bytes(item["content"])
    item = value[0]                                   # ipywidgets 8
    return item["name"], bytes(item["content"])


def _source(upload, path_box):
    """Return (name, source) where source is bytes or a path; None when nothing was given."""
    path = path_box.value.strip().strip('"')
    if path:
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        return os.path.basename(path), path
    return _uploaded(upload)


def audit_html(result: MirrorResult, components=("Fx", "Fy", "Fz")) -> str:
    head = "".join(f"<th style='padding:4px 10px;border:1px solid #999'>{c}</th>" for c in ("Load type",) + tuple(components))
    rows = []
    for row in result.audit:
        cells = [f"<td style='padding:4px 10px;border:1px solid #999'><b>{row.family}</b>"
                 f"{'' if row.selected else ' <span style=\"color:#666\">(kept)</span>'}</td>"]
        for c in components:
            cells.append(f"<td style='padding:4px 10px;border:1px solid #999;background:{_LEVEL_COLOR[row.level[c]]};color:#111'>"
                         f"{html.escape(row.status[c])}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    warn = "".join(f"<li>{html.escape(w)}</li>" for w in result.warnings)
    warn = f"<ul style='color:#a33'>{warn}</ul>" if warn else ""
    return (f"<h4 style='margin:14px 0 4px'>Mirror {result.direction}</h4>"
            f"<table style='border-collapse:collapse;font-size:13px'><tr>{head}</tr>{''.join(rows)}</table>{warn}")


class FbmpApp:
    def __init__(self, output_dir: str = "fbmp_mirror_out"):
        self.output_dir = output_dir
        self.base_name = "model"
        self.infile: Optional[InFile] = None
        self.model: Optional[OutModel] = None
        self.results: Dict[str, MirrorResult] = {}
        self.cap_sets: Dict[str, cuts.CapForces] = {}
        self.elem_sets: Dict[str, elemforces.ElementForces] = {}
        self._listeners: List = []

    # ------------------------------------------------------------------ files
    def _read_base(self, name_in: str, data_in, name_out: str, src_out) -> None:
        if isinstance(data_in, str):
            with open(data_in, "rb") as fh:
                data_in = fh.read()
        self.infile = parse_in(data_in)
        self.model = read_model(src_out)
        self.base_name = os.path.splitext(name_in)[0]
        self.base_out = src_out
        self.results.clear()
        self.cap_sets.clear()
        self.elem_sets.clear()
        m = self.model
        x0, x1, y0, y1 = m.cap_extents()
        print(f"{name_in}: {len(self.infile.loads)} load lines, load types: {', '.join(self.infile.families())}")
        print(f"{name_out}: {len(m.nodes)} nodes ({len(m.pile_heads)} piles, {len(m.cap_nodes)} cap nodes, "
              f"{len(m.group('pier'))} pier nodes, {len(m.group('bearingL', 'bearingR'))} bearing nodes), "
              f"{len(m.combos)} load combinations")
        print(f"{'Pile cap' if m.cap_nodes else 'Structure (no pile cap)'} X {x0 / FT:.2f} to {x1 / FT:.2f} ft, Y {y0 / FT:.2f} to {y1 / FT:.2f} ft; "
              f"mirror planes X = {(x0 + x1) / 2 / FT:.3f} ft, Y = {(y0 + y1) / 2 / FT:.3f} ft")
        groups: Dict[str, int] = {}
        for n in m.nodes.values():
            groups[n.group] = groups.get(n.group, 0) + 1
        kinds: Dict[str, int] = {}
        for e in m.frame_elements:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        print(f"Structure type: {m.structure_type or 'not stated'}; node groups: "
              + ", ".join(f"{g} {c}" for g, c in groups.items())
              + "; elements: " + ", ".join(f"{k} {c}" for k, c in kinds.items())
              + (f", pile cap {len(m.cap_elements)}" if m.cap_elements else ""))
        known = {"pile", "cap", "pier", "bearingL", "bearingR", "Bent Cap"}
        if set(groups) - known:
            print("Note: node groups this tool has not been tried on: " + ", ".join(sorted(set(groups) - known))
                  + ". They are drawn and can be mirrored (nearest node of the same group), but check the result.")
        if m.substructures > 1:
            print(f"WARNING: the .out has {m.substructures} substructures (a multi-pier model). The tool was built on "
                  "single-pier models; node numbers and mirror planes are not handled per pier. Do not rely on it here.")
        if not m.cap_nodes:
            print("Note: no pile cap nodes in the .out (pile bent or similar). Mirror planes default to the middle of "
                  "all nodes; set them in step 2 if needed. Section cuts need a pile cap and are not available.")
        if not any(ld.section == "LOADBP" for ld in self.infile.loads):
            print("Note: no bearing loads (LOADBP) in the .in; only nodal loads will be mirrored.")
        if self.infile.unparsed:
            print(f"WARNING: {len(self.infile.unparsed)} line(s) in LOAD / LOADBP were not understood "
                  f"(first: line {self.infile.unparsed[0] + 1}); they will be copied unchanged.")
        missing = [ld.target for ld in self.infile.loads if ld.section == "LOAD" and ld.target not in m.nodes]
        if missing:
            print(f"WARNING: {len(set(missing))} loaded node(s) are not in the .out file. "
                  "Is the .out from the same model as the .in?")
        for fn in self._listeners:
            fn()

    def load_base(self, in_path: str = "", out_path: str = ""):
        """Read the base model.  With both paths given they are read directly.  Otherwise, in Colab a
        file chooser is shown (pick the .in and the .out together; the .out is trimmed in the browser,
        so its size does not matter); outside Colab the upload panel is shown."""
        in_path = (in_path or "").strip().strip('"')
        out_path = (out_path or "").strip().strip('"')
        for path in (in_path, out_path):
            if path and not os.path.exists(path):
                print(f"File not found: {path}")
                return None
        if in_path and out_path:
            try:
                self._read_base(os.path.basename(in_path), in_path, os.path.basename(out_path), out_path)
            except Exception as exc:
                print(f"Could not read the files: {exc}")
            return None
        if not colab_upload.in_colab():
            return display(self.import_panel())

        def done(files: Dict[str, str]):
            pin = in_path or next((p for n, p in files.items() if n.lower().endswith(".in")), "")
            pout = out_path or next((p for n, p in files.items() if n.lower().endswith(".out")), "")
            if not (pin and pout):
                print("Need one .in file and one .out file: choose both together.")
                return
            self._read_base(os.path.basename(pin), pin, os.path.basename(pout), pout)

        colab_upload.show_uploader(
            "<b>Choose the base .in and .out files together</b> (hold Ctrl to select both). "
            "The .out is read in your browser and only the few MB the tool needs are sent, so a large file is fine.",
            done)
        return None

    def _read_results(self, run_of: str, source) -> None:
        if run_of == "Base":
            infile = self.infile
        else:
            key = run_of.split()[-1]
            if key not in self.results:
                print(f"Mirror the loads first (step 2) so the {run_of} input is known.")
                return
            infile = parse_in(self.results[key].text)
        print(f"Reading results for {run_of} ...")
        forces = elemforces.read_element_forces(source, self.model)
        self.elem_sets[run_of] = forces
        have = int((~np.isnan(forces.data[0]).all(axis=(1, 2))).sum())
        print(f"{len(forces.combos)} load combinations; end forces read for {have:,} of {len(forces.elem_ids):,} "
              "frame elements (piles, columns, cap beams, extra members).")
        if self.model.cap_elements:
            capf = cuts.build_cap_forces(self.model, infile, read_results(source, self.model))
            self.cap_sets[run_of] = capf
            resid = np.abs(capf.residual())
            load = float(np.abs(capf.vertical_load()).max())
            print(f"Pile cap free-body check: largest unbalanced force {resid[:, :3].max():.1f} kips, moment "
                  f"{resid[:, 3:].max():.0f} kip-ft (total vertical load up to {load:,.0f} kips).")
            if resid[:, :3].max() > 0.02 * max(load, 1.0):
                print("WARNING: the pile cap free body does not close within 2 %. Do not use the pile cap totals "
                      "until this is understood (wrong .out for this model, or battered piles).")
            for note in capf.notes:
                print("Note:", note)
        for fn in self._listeners:
            fn()

    def load_results(self, run_of: str = "Base", path: str = ""):
        """Read the .out of a run for the section cuts.  run_of: 'Base', 'Mirror X', 'Mirror Y' or 'Mirror XY'.
        Blank path: the base .out from step 1 for 'Base'; otherwise Colab shows a file chooser."""
        if self.model is None or self.infile is None:
            print("Read the base files first (step 1).")
            return
        path = (path or "").strip().strip('"')
        if path and not os.path.exists(path):
            print(f"File not found: {path}")
            return
        if not path and run_of == "Base":
            path = self.base_out
        if path:
            try:
                self._read_results(run_of, path)
            except Exception as exc:
                print(f"Could not read the results: {exc}")
            return
        if not colab_upload.in_colab():
            print("Type the path of that run's .out file.")
            return

        def done(files: Dict[str, str]):
            pout = next((p for n, p in files.items() if n.lower().endswith(".out")), "")
            if not pout:
                print("That was not a .out file.")
                return
            self._read_results(run_of, pout)

        colab_upload.show_uploader(f"<b>Choose the .out file of the {run_of} run.</b>", done, accept=".out", multiple=False)

    # ------------------------------------------------------------------ 1
    def import_panel(self):
        in_up = W.FileUpload(accept=".in", multiple=False, description="Base .in file")
        out_up = W.FileUpload(accept=".out", multiple=False, description="Base .out file")
        in_path = W.Text(placeholder="or a path, e.g. /content/drive/MyDrive/Pier 7.in", layout=W.Layout(width="60%"))
        out_path = W.Text(placeholder="or a path (use this for large .out files)", layout=W.Layout(width="60%"))
        go = W.Button(description="Read files", button_style="primary")
        out = W.Output()

        def run(_):
            out.clear_output()
            with out:
                try:
                    src_in = _source(in_up, in_path)
                    src_out = _source(out_up, out_path)
                    if not src_in or not src_out:
                        print("Give both the base .in file and the .out file of a run of that same model.\n"
                              "If you picked files and still see this, the browser upload did not reach the notebook "
                              "(common with large .out files): type the file paths instead.")
                        return
                    self._read_base(src_in[0], src_in[1], src_out[0], src_out[1])
                except Exception as exc:          # show the reason in the notebook, not a traceback
                    print(f"Could not read the files: {exc}")

        go.on_click(run)
        return W.VBox([
            W.HTML("<b>1. Base model</b> &nbsp; the .in file and the .out file from a run of that same model "
                   "(the .out supplies the node coordinates)."),
            W.HBox([in_up, in_path]), W.HBox([out_up, out_path]), go, out,
        ])

    # ------------------------------------------------------------------ 2
    def mirror_panel(self):
        fam_box = W.VBox()
        case_sel = W.SelectMultiple(rows=10, layout=W.Layout(width="320px"), description="Cases")
        dirs = [W.Checkbox(value=True, description=f"Mirror {d}", indent=False, layout=W.Layout(width="110px")) for d in DIRECTIONS]
        cx = W.Text(description="Mirror plane X (ft)", placeholder="cap centerline", style=_STYLE)
        cy = W.Text(description="Mirror plane Y (ft)", placeholder="cap centerline", style=_STYLE)
        tol = W.FloatText(value=2.0, description="Node match tolerance (ft)", style=_STYLE)
        go = W.Button(description="Mirror loads", button_style="primary")
        out = W.Output()
        fam_checks: Dict[str, W.Checkbox] = {}
        busy = {"on": False}

        def from_families(_=None):
            if busy["on"] or self.infile is None:
                return
            busy["on"] = True
            chosen = {f for f, cb in fam_checks.items() if cb.value}
            case_sel.value = tuple(c for c, t in sorted(self.infile.case_types().items()) if load_family(t) in chosen)
            busy["on"] = False

        def refresh():
            if self.infile is None:
                return
            fam_checks.clear()
            types = self.infile.case_types()
            for fam in self.infile.families():
                n = sum(1 for t in types.values() if load_family(t) == fam)
                cb = W.Checkbox(value=fam in DEFAULT_FAMILIES, description=f"{fam}  ({n} case{'s' if n > 1 else ''})",
                                indent=False, layout=W.Layout(width="160px"))
                cb.observe(from_families, names="value")
                fam_checks[fam] = cb
            fam_box.children = [W.HBox(list(fam_checks.values()), layout=W.Layout(flex_flow="row wrap"))]
            case_sel.options = [(f"case {c}: {t}", c) for c, t in sorted(types.items())]
            from_families()

        def run(_):
            out.clear_output()
            with out:
                if self.infile is None or self.model is None:
                    print("Read the base files first (step 1).")
                    return
                try:
                    center = (float(cx.value) * FT if cx.value.strip() else None, float(cy.value) * FT if cy.value.strip() else None)
                    selected = set(case_sel.value)
                    os.makedirs(self.output_dir, exist_ok=True)
                    self.results.clear()
                    self.cap_sets = {k: v for k, v in self.cap_sets.items() if k == "Base"}
                    self.elem_sets = {k: v for k, v in self.elem_sets.items() if k == "Base"}
                    written = []
                    for d, cb in zip(DIRECTIONS, dirs):
                        if not cb.value:
                            continue
                        res = mirror_loads(self.infile, self.model, d, selected, center, tol.value * FT)
                        self.results[d] = res
                        stem = os.path.join(self.output_dir, f"{self.base_name}_Mirror{d}")
                        with open(stem + ".in", "w", encoding="latin-1", newline="") as fh:
                            fh.write(res.text)
                        with open(stem + "_report.txt", "w", encoding="utf-8") as fh:
                            fh.write(format_report(res))
                        written += [stem + ".in", stem + "_report.txt"]
                        display(HTML(audit_html(res)))
                    if not written:
                        print("Tick at least one mirror direction.")
                        return
                    display(HTML("<div style='font-size:12px;margin-top:6px'>"
                                 "<span style='background:#c9efc9;color:#111;padding:1px 6px'>done</span> "
                                 "<span style='background:#fff3a6;color:#111;padding:1px 6px'>check: nothing visibly changes</span> "
                                 "<span style='background:#f6b4b4;color:#111;padding:1px 6px'>directional load left unmirrored</span> "
                                 "<span style='background:#eeeeee;color:#111;padding:1px 6px'>N/A: component is zero</span></div>"))
                    zpath = os.path.join(self.output_dir, f"{self.base_name}_mirrored.zip")
                    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                        for p in written:
                            z.write(p, os.path.basename(p))
                    print(f"\nWrote {len(written)} files to {os.path.abspath(self.output_dir)}")
                    print(f"Zip of all files: {zpath}  (in Colab: Files panel on the left, right-click, Download)")
                    try:
                        from google.colab import files       # type: ignore
                        files.download(zpath)
                    except Exception:
                        pass
                    for fn in self._listeners:
                        fn()
                except Exception as exc:
                    print(f"Mirroring failed: {exc}")

        go.on_click(run)
        self._listeners.append(refresh)
        refresh()
        return W.VBox([
            W.HTML("<b>2. Load cases to mirror</b> &nbsp; tick load types, or pick individual cases in the list "
                   "(Ctrl/Shift-click). Everything else is copied unchanged."),
            fam_box, case_sel, W.HBox(dirs), W.HBox([cx, cy, tol]), go, out,
        ])

    # ------------------------------------------------------------------ 3
    def view_panel(self):
        direction = W.Dropdown(description="Mirror", options=[], style=_STYLE)
        case = W.Dropdown(description="Load case", options=[], style=_STYLE)
        before = W.Checkbox(value=True, description="Before (blue)", indent=False)
        after = W.Checkbox(value=True, description="After (red)", indent=False)
        pile_nodes = W.Checkbox(value=True, description="Pile nodes and elements", indent=False)
        depth = W.FloatText(value=0.0, description="Pile depth shown (ft, 0 = all)", style=_STYLE)
        comps = [W.Checkbox(value=True, description=c, indent=False, layout=W.Layout(width="60px"))
                 for c in ("Fx", "Fy", "Fz", "Mx", "My", "Mz")]
        arrow = W.FloatText(value=0.0, description="Longest arrow (ft, 0 = auto)", style=_STYLE)
        width = W.IntText(value=1100, description="Plot width (px)", style=_STYLE)
        go = W.Button(description="Draw", button_style="primary")
        out = W.Output()

        def refresh():
            if self.infile is None:
                return
            direction.options = list(self.results) or ["(base only)"]
            sel = next(iter(self.results.values())).selected_cases if self.results else set()
            opts = [(f"case {c}: {t}{'  (mirrored)' if c in sel else ''}", c)
                    for c, t in sorted(self.infile.case_types().items())]
            case.options = opts
            first = next((c for _, c in opts if c in sel), None)
            if first is not None:
                case.value = first

        def run(_):
            out.clear_output()
            with out:
                try:
                    if self.model is None:
                        print("Read the base files first (step 1).")
                        return
                    fig = viz.model_figure(self.model, show_pile_nodes=pile_nodes.value,
                                           max_pile_depth=(depth.value * FT) or None, title=self.base_name,
                                           width=width.value or None)
                    res = self.results.get(direction.value)
                    if res is None and self.infile is not None:      # nothing mirrored yet: show the base loads
                        res = mirror_loads(self.infile, self.model, "X", set())
                        after.value = False
                    if res is not None and case.value is not None:
                        viz.add_loads(fig, res.changes, case.value, show_before=before.value, show_after=after.value,
                                      components=[c.description for c in comps if c.value], length=arrow.value or None)
                        rows = [ch for ch in res.changes if ch.line.case == case.value]
                        moved = sum(1 for ch in rows if ch.mirrored)
                        print(f"Case {case.value} ({rows[0].line.type if rows else ''}): {len(rows)} load lines, {moved} mirrored. "
                              "Each component is its own arrow along its axis: straight = force, curved = moment "
                          "(right-hand rule). +Fz is down. Size is proportional to the value; hover for all six.")
                    meta = fig.layout.meta
                    print(f"Showing {meta['nodes_shown']:,} of {meta['nodes_total']:,} nodes and {meta['elements_shown']:,} of "
                          f"{meta['elements_total']:,} elements, in feet. Click a legend entry to hide or show that group.")
                    fig.show()
                except Exception as exc:
                    print(f"Something went wrong: {type(exc).__name__}: {exc}")


        go.on_click(run)
        self._listeners.append(refresh)
        refresh()
        return W.VBox([
            W.HTML("<b>3. 3D view</b> &nbsp; nodes and elements with hover labels; loads before mirroring in blue, after in red, "
                   "as arrows scaled to the force, labelled in kips; hover an arrow for all six components."),
            W.HBox([direction, case]), W.HBox([before, after, W.HTML('&nbsp; Show:'), *comps]),
            W.HBox([pile_nodes, depth]), W.HBox([arrow, width]), go, out,
        ])

    # ------------------------------------------------------------------ 4
    def cut_panel(self):
        run_of = W.Dropdown(description="This .out is a run of", options=["Base"], style=_STYLE)
        res_path = W.Text(placeholder="path of that .out (blank = the base .out from step 1)", layout=W.Layout(width="55%"))
        add = W.Button(description="Read results")
        sets_label = W.HTML()
        node = W.IntText(description="Node number", style=_STYLE)
        axis = W.ToggleButtons(options=[("Plane X = node's X", "X"), ("Plane Y = node's Y", "Y"), ("Plane Z = node's Z", "Z")],
                               description="Cut plane", style=_STYLE)
        comp = W.Dropdown(description="Force to plot", options=list(elemforces.COMPONENTS) + ["All six"], value="M33",
                          style=_STYLE)
        tol = W.FloatText(value=0.1, description="In-plane tolerance (ft)", style=_STYLE)
        width = W.IntText(value=1000, description="Plot width (px)", style=_STYLE)
        go = W.Button(description="Cut and plot", button_style="primary")
        log = W.Output()
        out = W.Output()

        def refresh():
            run_of.options = ["Base"] + [f"Mirror {d}" for d in self.results]
            sets_label.value = "Results loaded: " + (", ".join(self.elem_sets) or "none yet")
            if self.model is not None and node.value == 0:
                heads = self.model.pile_heads
                if heads:
                    node.value = heads[0].id

        def read(_):
            log.clear_output()
            with log:
                path = res_path.value.strip().strip('"')
                if not path and run_of.value != "Base":
                    print("Type the path of that run's .out file, or use app.load_results() in the cell above to upload it.")
                    return
                self.load_results(run_of.value, path)

        def cell(text, right=True):
            return f"<td style='padding:3px 10px;border:1px solid #999{';text-align:right' if right else ''}'>{text}</td>"

        def run(_):
            out.clear_output()
            with out:
                try:
                    if not self.elem_sets:
                        print("Read at least one results file first (cell 4a).")
                        return
                    m = self.model
                    try:
                        plane = elemforces.select_plane(m, node.value, axis.value, tol.value * FT)
                    except KeyError:
                        print(f"Node {node.value} is not in the model.")
                        return
                    n0 = m.nodes[node.value]
                    print(f"Node {n0.id} is at X = {n0.x / FT:.3f}, Y = {n0.y / FT:.3f}, Z = {n0.z / FT:.3f} ft. "
                          f"Cut plane: {AXIS_WORDS[axis.value]} = {plane.value / FT:.3f} ft.")
                    print(f"On the plane: {len(plane.nodes)} nodes, {len(plane.frame_elems)} frame elements in "
                          f"{len(plane.members)} members"
                          + (f", {len(plane.cap_elems)} pile cap elements" if m.cap_elements else "") + ".")
                    if not plane.frame_elems:
                        print("No frame element lies in this plane. Pick a node on a pile, column or cap beam, or another axis.")

                    # pile cap totals by statics on the same plane
                    if axis.value in ("X", "Y") and self.cap_sets:
                        x0, x1, y0, y1 = m.cap_extents()
                        if axis.value == "X":
                            p0, p1 = (plane.value, y0), (plane.value, y1)
                        else:
                            p0, p1 = (x0, plane.value), (x1, plane.value)
                        rows = []
                        for name, capf in self.cap_sets.items():
                            res = cuts.cut(capf, p0, p1)
                            rows.append("<tr>" + cell(f"<b>{html.escape(name)}</b>", False)
                                        + "".join(cell(f"{v:,.1f}") + cell(c) for v, c in res.summary().values())
                                        + cell(f"{float(np.abs(res.unbalance).max()):,.0f}") + "</tr>")
                        head = ("<tr><th style='padding:3px 10px;border:1px solid #999'>Results</th>"
                                + "".join(f"<th style='padding:3px 10px;border:1px solid #999'>{h}</th>"
                                          "<th style='padding:3px 10px;border:1px solid #999'>Comb</th>"
                                          for h in ("Max. moment (+), bottom steel, kip-ft", "Min. moment (-), top steel, kip-ft",
                                                    "Max. shear (abs), kips"))
                                + "<th style='padding:3px 10px;border:1px solid #999'>Two-side difference, kip-ft</th></tr>")
                        display(HTML("<h4 style='margin:10px 0 4px'>Pile cap on this plane (totals across the full cut, by statics)</h4>"
                                     f"<table style='border-collapse:collapse;font-size:13px'>{head}{''.join(rows)}</table>"
                                     "<div style='font-size:12px;color:#666'>The .out has no pile cap shell forces, so these are "
                                     "resultants of pile heads, columns, self weight and cap loads on one side of the plane.</div>"))

                    # frame elements in the plane: max / min of every member
                    comps = list(elemforces.COMPONENTS) if comp.value == "All six" else [comp.value]
                    for name, forces in self.elem_sets.items():
                        if not plane.frame_elems:
                            break
                        rows = elemforces.summary_rows(forces, plane)
                        body = "".join(
                            "<tr>" + cell(html.escape(r[0]), False) + cell(r[1], False) + cell(f"{r[2]:,.2f}") + cell(r[3]) + cell(r[4])
                            + cell(f"{r[5]:,.2f}") + cell(r[6]) + cell(r[7]) + "</tr>"
                            for r in rows if r[1] in comps)
                        display(HTML(
                            f"<h4 style='margin:14px 0 4px'>Element forces on the plane: {html.escape(name)}</h4>"
                            "<details><summary>Max and min per member (table)</summary>"
                            "<table style='border-collapse:collapse;font-size:12px'><tr>"
                            + "".join(f"<th style='padding:3px 10px;border:1px solid #999'>{h}</th>" for h in
                                      ("Member", "Force", "Max", "Element", "Comb", "Min", "Element", "Comb"))
                            + f"</tr>{body}</table><div style='font-size:12px;color:#666'>FAX, F22, F33 in kips; M22, M33, "
                            "TORQUE in kip-ft; element local axes, internal-force sign (continuous along a member).</div></details>"))
                        for c in comps:
                            for fig in elemforces.envelope_figures(forces, plane, c, name, width.value or None, m):
                                fig.show()

                    fig = viz.model_figure(m, show_pile_nodes=False, title="Elements on the cut plane", height=560,
                                           show_elements=False, width=width.value or None)
                    on = set(plane.frame_elems)
                    segs = [((m.nodes[e.i].x, m.nodes[e.i].y, m.nodes[e.i].z), (m.nodes[e.j].x, m.nodes[e.j].y, m.nodes[e.j].z))
                            for e in m.frame_elements if e.id in on]
                    viz.add_highlight(fig, segs, [(m.nodes[i].x, m.nodes[i].y, m.nodes[i].z) for i in plane.nodes],
                                      (n0.x, n0.y, n0.z))
                    fig.show()
                except Exception as exc:
                    print(f"Something went wrong: {type(exc).__name__}: {exc}")

        add.on_click(read)
        go.on_click(run)
        self._listeners.append(refresh)
        refresh()
        return W.VBox([
            W.HTML("<b>4. Section cut</b> &nbsp; give a node number and an axis. The cut plane passes through that node, "
                   "normal to the axis; every element lying in the plane is collected and its forces are plotted as "
                   "max / min over all load combinations."),
            W.HBox([run_of, res_path]), add, sets_label, log,
            W.HBox([node, comp]), axis, W.HBox([tol, width]), go, out,
        ])
