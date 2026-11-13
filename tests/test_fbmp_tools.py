import subprocess
import sys
import unittest

import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture  # noqa: E402
from fbmp_tools import cases_for, default_cases, mirror_loads, parse_in, read_model, read_results  # noqa: E402
from fbmp_tools import cuts  # noqa: E402


def load():
    model = read_model(fixture.out_text().splitlines(keepends=True))
    infile = parse_in(fixture.in_text())
    return model, infile


def lines_of(result, type_label, section):
    return [ch for ch in result.changes if ch.line.type == type_label and ch.line.section == section]


class ReadTests(unittest.TestCase):
    def test_model(self):
        model, infile = load()
        self.assertEqual(len(model.nodes), 21)
        self.assertEqual(len(model.pile_heads), 4)
        self.assertEqual(model.n_bearings, 2)
        self.assertEqual(model.mirror_center(), (120.0, 60.0))
        self.assertEqual(model.combos[2], {"DC": 1.0, "BR1": 1.0, "WS1": 1.0, "TU": 1.0})
        self.assertEqual(len(model.cap_elements), 1)
        self.assertEqual(infile.families(), ["DC", "BR", "WS", "TU"])
        self.assertEqual(len(infile.loads), 16)

    def test_default_selection_includes_braking_water_and_temperature(self):
        _, infile = load()
        self.assertEqual(default_cases(infile), {2, 3, 4})
        self.assertEqual(cases_for(infile, ["WS*"]), {3})


class MirrorTests(unittest.TestCase):
    def test_mirror_y_reverses_fy_and_swaps_bearing_rows(self):
        model, infile = load()
        res = mirror_loads(infile, model, "Y")
        br = lines_of(res, "BR1", "LOADBP")
        self.assertEqual([(c.label, c.new_label, c.new_values[1], c.new_values[2]) for c in br], [
            ("1L", "1R", "-4.17", "0.36"), ("1R", "1L", "-4.17", "-0.36"),
            ("2L", "2R", "-4.17", "0.36"), ("2R", "2L", "-4.17", "-0.36")])
        tu = lines_of(res, "TU", "LOADBP")
        self.assertEqual([(c.new_label, c.new_values[1]) for c in tu], [("1R", "6.02"), ("1L", "-8.77")])
        ws = lines_of(res, "WS1", "LOAD")          # pile nodes 21 (y=120) and 23 (y=0) trade places
        self.assertEqual([(c.new_target, c.new_values[0], c.new_values[1]) for c in ws],
                         [(23, "1.5", "-0.25"), (21, "2.5", "-0.25")])

    def test_mirror_x_reverses_fx_and_my_and_moves_bearings(self):
        model, infile = load()
        res = mirror_loads(infile, model, "X")
        ws = lines_of(res, "WS1", "LOADBP")
        self.assertEqual([(c.new_label, c.new_values[0], c.new_values[2]) for c in ws], [("2L", "-6", "-2"), ("1R", "-7", "-3")])
        tu = lines_of(res, "TU", "LOAD")[0]
        self.assertEqual((tu.new_target, tu.new_values[0], tu.new_values[4]), (30, "-3", "90"))
        self.assertIn("30 L= 4 F= -3 0 0 0 90 0 T=TU", res.text)

    def test_unselected_cases_are_copied_byte_for_byte(self):
        model, infile = load()
        res = mirror_loads(infile, model, "XY", {3})
        base = infile.text().split("\r\n")
        new = res.text.split("\r\n")
        changed = {i for i, (a, b) in enumerate(zip(base, new)) if a != b}
        expected = {ld.index for ld in infile.loads if ld.case == 3} | {2}      # plus the title line
        self.assertTrue(changed <= expected)
        self.assertTrue(new[2].endswith(" - Mirror XY WS"))
        for ch in res.changes:
            if ch.line.case != 3:
                self.assertFalse(ch.mirrored)

    def test_mirroring_twice_returns_the_base_loads(self):
        model, infile = load()
        for d in ("X", "Y", "XY"):
            once = mirror_loads(infile, model, d, title_suffix="")
            twice = mirror_loads(parse_in(once.text), model, d, title_suffix="")
            self.assertEqual(sorted(twice.text.split("\r\n")), sorted(infile.text().split("\r\n")), d)

    def test_resultants_match_an_exact_mirror(self):
        model, infile = load()
        for d in ("X", "Y", "XY"):
            for r in mirror_loads(infile, model, d).resultants:
                for a, e in zip(r.after, r.expected):
                    self.assertAlmostEqual(a, e, places=6)

    def test_audit_flags(self):
        model, infile = load()
        rows = {r.family: r for r in mirror_loads(infile, model, "Y").audit}
        self.assertEqual(rows["BR"].level["Fy"], "ok")
        self.assertEqual(rows["BR"].level["Fx"], "na")
        self.assertEqual(rows["TU"].level["Fy"], "ok")
        self.assertEqual(rows["DC"].level["Fz"], "ok")
        rows = {r.family: r for r in mirror_loads(infile, model, "Y", cases_for(infile, ["WS*"])).audit}
        self.assertEqual(rows["TU"].level["Fy"], "problem")      # directional load left unmirrored
        self.assertEqual(rows["BR"].level["Fy"], "problem")
        rows = {r.family: r for r in mirror_loads(infile, model, "X").audit}
        self.assertEqual(rows["BR"].level["Fy"], "ok")           # uniform across bearings: same after mirroring
        self.assertIn("same after mirroring", rows["BR"].status["Fy"])


class CutTests(unittest.TestCase):
    def setUp(self):
        self.model, self.infile = load()
        results = read_results(fixture.out_text().splitlines(keepends=True), self.model)
        self.capf = cuts.build_cap_forces(self.model, None, results)

    def test_free_body_closes(self):
        resid = self.capf.residual()
        self.assertLess(abs(resid).max(), 0.05)

    def test_cut_moment_and_shear(self):
        # cut at x = 120 (the column sits on the line): two piles on each side, each (P + W) / 4 up,
        # 10 ft from the cut, less half the cap weight at 5 ft
        res = cuts.cut(self.capf, (120, 0), (120, 120))
        p, w = fixture.COLUMN_LOAD[1], fixture.WEIGHT
        moment = 2 * (p + w) / 4 * 10.0 - w / 2 * 5.0
        self.assertAlmostEqual(res.moment_a[0], moment, delta=0.02)
        self.assertAlmostEqual(res.moment_b[0], moment, delta=0.02)
        self.assertAlmostEqual(abs(res.shear_a[0]), p / 2, delta=0.02)
        self.assertAlmostEqual(abs(res.shear_b[0]), p / 2, delta=0.02)
        self.assertEqual(res.summary()["Max. moment (+), bottom steel"][1], 1)
        self.assertLess(res.closure(), 1e-3)

    def test_nodes_and_elements_on_line(self):
        on = cuts.nodes_on_line(self.model, (120, 0), (120, 120))
        self.assertEqual([n.id for _, n in on], [14, 12, 10])
        self.assertEqual(len(cuts.elements_on_line(self.model, (120, 0), (120, 120))), 1)


class BrowserFilterTests(unittest.TestCase):
    """The JavaScript that trims a .out in the browser must not drop anything the readers use."""

    def trimmed(self, text):
        import shutil, tempfile
        from fbmp_tools.colab_upload import FILTER_JS
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = FILTER_JS + """
const fs = require('fs');
const keep = makeOutFilter();
const lines = fs.readFileSync(process.argv[2], 'latin1').split('\\n').map(l => l.endsWith('\\r') ? l.slice(0, -1) : l);
fs.writeFileSync(process.argv[3], lines.map(keep).filter(l => l !== null).join('\\n') + '\\n', 'latin1');
"""
        with tempfile.TemporaryDirectory() as tmp:
            src, dst, js = (str(Path(tmp) / n) for n in ("full.out", "trim.out", "run.js"))
            Path(src).write_text(text, encoding="latin-1", newline="")
            Path(js).write_text(script, encoding="utf-8")
            subprocess.run([node, js, src, dst], check=True)
            return Path(dst).read_text(encoding="latin-1")

    def test_trimmed_file_reads_the_same(self):
        full = fixture.out_text()
        small = self.trimmed(full)
        self.assertLess(len(small), len(full))
        m1 = read_model(full.splitlines(keepends=True))
        m2 = read_model(small.splitlines(keepends=True))
        self.assertEqual(m1, m2)
        r1 = read_results(full.splitlines(keepends=True), m1)
        r2 = read_results(small.splitlines(keepends=True), m2)
        self.assertEqual(r1, r2)
        self.assertEqual(len(r2[1].pile_head), 4)
        from fbmp_tools import elemforces
        f1 = elemforces.read_element_forces(full.splitlines(keepends=True), m1)
        f2 = elemforces.read_element_forces(small.splitlines(keepends=True), m2)
        self.assertEqual(f1.combos, f2.combos)
        self.assertTrue(np.array_equal(f1.data, f2.data, equal_nan=True))
        self.assertFalse(np.isnan(f2.data).all())


class ElementForceTests(unittest.TestCase):
    def setUp(self):
        from fbmp_tools import elemforces
        self.ef = elemforces
        self.model, _ = load()
        self.forces = elemforces.read_element_forces(fixture.out_text().splitlines(keepends=True), self.model)

    def test_reads_both_ends_and_internal_sign(self):
        f = self.forces
        row = f.index[1]                                   # the column
        self.assertEqual(f.nodes[row].tolist(), [30, 31])
        self.assertAlmostEqual(f.data[0, row, 0, 0], fixture.COLUMN_LOAD[1])
        self.assertAlmostEqual(f.data[0, row, 1, 0], -fixture.COLUMN_LOAD[1])
        internal = f.internal()[0, row, :, 0]
        self.assertAlmostEqual(internal[0], internal[1])    # continuous along the member
        vmax, vmin, cmax, cmin = f.envelope()
        self.assertAlmostEqual(vmin[row, 1, 0], -fixture.COLUMN_LOAD[1])
        self.assertEqual(int(cmin[row, 1, 0]), 1)

    def test_plane_from_node_and_axis(self):
        plane = self.ef.select_plane(self.model, 1, "X")        # x = 0: piles 1 and 3
        self.assertEqual(sorted(plane.frame_elems), [11, 13])
        self.assertEqual(sorted(m.label for m in plane.members), ["Pile 1", "Pile 3"])
        self.assertEqual(plane.cap_elems, [1])
        plane = self.ef.select_plane(self.model, 30, "Y")       # y = 60: the column and the pier cap beam
        self.assertEqual(sorted(plane.frame_elems), [1, 2, 3])
        self.assertEqual(sorted((m.kind, m.direction, len(m.elems)) for m in plane.members), [("pier", 0, 2), ("pier", 2, 1)])
        figs = self.ef.envelope_figures(self.forces, plane, "FAX")
        self.assertTrue(figs)


class NotebookTests(unittest.TestCase):
    def test_committed_notebook_equals_a_fresh_build(self):
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_notebook.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
