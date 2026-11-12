"""Build FBMP_Tools.ipynb with the fbmp_tools package embedded, so the notebook runs in
Colab with nothing to install and no access to this repository.

    python scripts/build_notebook.py            # rewrite the notebook
    python scripts/build_notebook.py --check    # exit 1 when the committed notebook is stale
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "fbmp_tools"
NOTEBOOK = ROOT / "FBMP_Tools.ipynb"
COLAB = "https://colab.research.google.com/github/joshstructure/fbmp_tools/blob/main/FBMP_Tools.ipynb"


def package_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(PACKAGE.glob("*.py")):
            info = zipfile.ZipInfo(f"fbmp_tools/{path.name}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8"))
    return buf.getvalue()


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text: str) -> dict:
    return {"cell_type": "code", "metadata": {"cellView": "form"}, "execution_count": None, "outputs": [],
            "source": text.strip("\n").splitlines(keepends=True)}


def build() -> str:
    blob = package_zip()
    revision = hashlib.sha256(blob).hexdigest()[:12]
    b64 = base64.b64encode(blob).decode("ascii")
    chunks = "\n".join(f'    "{b64[i:i + 100]}"' for i in range(0, len(b64), 100))
    setup = f'''
# @title Start the tool (run this first)
import base64, io, sys, zipfile
from pathlib import Path

revision = "{revision}"
PACKAGE = (
{chunks}
)
target = Path("fbmp_tools_pkg") / revision
if not (target / "fbmp_tools" / "__init__.py").exists():
    zipfile.ZipFile(io.BytesIO(base64.b64decode(PACKAGE))).extractall(target)
if str(target) not in sys.path:
    sys.path.insert(0, str(target))
for name in [m for m in sys.modules if m == "fbmp_tools" or m.startswith("fbmp_tools.")]:
    del sys.modules[name]
try:
    from google.colab import output
    output.enable_custom_widget_manager()
except Exception:
    pass
from fbmp_tools.widgets import FbmpApp
app = FbmpApp()
print("fbmp_tools", revision, "ready")
'''
    cells = [
        md(f'''
# FB-MultiPier tools: load mirroring, 3D review, section cuts

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)]({COLAB})

Keeps the as-built foundation exactly as modeled and mirrors the **directional loads** about the pile cap
centerlines, writing `<name>_MirrorX.in`, `<name>_MirrorY.in` and `<name>_MirrorXY.in`.

| Mirror | Location | Components that change sign |
|---|---|---|
| X | x to the other side of the cap centerline | Fx, My, Mz |
| Y | y to the other side of the cap centerline | Fy, Mx, Mz |
| XY | both | Fx, Fy, Mx, My |

Run the cells in order. Units are those of the model (kips and inches in the .in file; the .out prints moments in kip-ft).
'''),
        code(setup),
        code('''
# @title 1. Import the base model (.in and .out)
display(app.import_panel())
'''),
        code('''
# @title 2. Choose the load cases to mirror, then mirror
display(app.mirror_panel())
'''),
        code('''
# @title 3. 3D view: nodes, elements, loads before (blue) and after (red)
display(app.view_panel())
'''),
        code('''
# @title 4. Section cut: max and min forces
display(app.cut_panel())
'''),
        md('''
## Notes

**What is mirrored.** Only the LOAD and LOADBP lines of the load cases you select are rewritten. Each nodal load
moves to the nearest node of the same kind (pile, pile cap, pier) at the mirror-image position; each bearing load
moves to the bearing at the mirror-image position. All other lines of the .in file are copied unchanged.
By default wind (WS, WL), braking (BR), water (WA) and uniform temperature (TU) are mirrored.

**Reading the table in step 2.** Green: the component was mirrored (or needs nothing). Yellow: the case was
mirrored but the loads come out the same as the original, because the load pattern is already symmetric
(check that this is what you expect). Red: a directional load type has that component and was left unmirrored.
Gray: the component is zero in the model.

**The .out file.** Step 1 needs only the first part of the .out (node coordinates). Step 4 reads the whole file.
Large .out files are easier to read from a path (Google Drive) than to upload through the browser.

**Section cuts.** FB-MultiPier prints pile cap stresses only when that print option is on, and these files
usually have it off. The cut forces here come from statics of the pile cap as a free body: pile head forces,
column forces, cap self weight and loads applied to cap nodes. They are totals across the full cut
(moment in kip-ft, shear in kips), not a distribution per inch along the cut. Each results file reports how
well the whole cap closes; treat a cut as unreliable if that check is poor. Battered piles have not been checked.

**To compare a mirrored run.** Run the generated `_Mirror?.in` in FB-MultiPier, then in step 4 choose
"Mirror X" (or Y, XY) and read that run's .out. The table then shows every loaded results set side by side.

**P-multipliers.** The lists in the .in are not changed. If they were calibrated for one direction, edit them in
the generated files (the PowerShell tool in `powershell/` can swap them from another .in).

**Wind.** Mirrored wind cases are nodal loads. Do not regenerate wind from the WIND page in the GUI for a
generated file, or the mirrored loads are overwritten.
'''),
    ]
    nb = {
        "cells": cells,
        "metadata": {
            "colab": {"provenance": []},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(nb, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        current = NOTEBOOK.read_text(encoding="utf-8").replace("\r\n", "\n") if NOTEBOOK.exists() else ""
        if current != text:
            print("FBMP_Tools.ipynb is stale: run python scripts/build_notebook.py")
            return 1
        print("FBMP_Tools.ipynb is current")
        return 0
    NOTEBOOK.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {NOTEBOOK} ({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
