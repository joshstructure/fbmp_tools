# fbmp_tools

Tools for FB-MultiPier models with as-built (non-symmetric) pile layouts.

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/joshstructure/fbmp_tools/blob/main/FBMP_Tools.ipynb)

Instead of mirroring the pile grid to cover every angle of attack, the foundation is kept exactly as modeled
and the **directional loads** are mirrored about the pile cap centerlines. One base model gives three more
input files (`_MirrorX.in`, `_MirrorY.in`, `_MirrorXY.in`); pile and node numbers stay those of the as-built model.

## What is here

| | |
|---|---|
| `FBMP_Tools.ipynb` | Colab notebook. Self-contained: the package is embedded, nothing to install. |
| `fbmp_tools/` | Python package: file readers, mirroring, section cuts, plots, notebook panels. |
| `powershell/` | The original Windows tool (`Mirror Loads.bat`), kept for use without Python. It can also swap p-multiplier lists. |
| `scripts/build_notebook.py` | Rebuilds the notebook after any change to `fbmp_tools/`. |
| `tests/` | Synthetic model and tests (`python tests/test_fbmp_tools.py`). No project data. |

## The notebook

1. **Import** the base `.in` and the `.out` of a run of that model (buttons, or a path for large files).
2. **Choose the load cases to mirror**, by load type or case by case, then mirror. A table shows, for every
   load type and for Fx, Fy, Fz, whether it was mirrored, is zero, is already symmetric, or is a directional
   load left unmirrored.
3. **3D view** of nodes and elements with hover labels (node number, coordinates, pile, element nodes).
   Loads of one case are drawn before mirroring in blue and after in red, labelled with their magnitude.
4. **Section cut** through the pile cap: nodes and elements on the line, max and min moment and max shear
   over all load combinations, a plot per combination and an envelope for cuts across the cap. Results of the
   base run and of each mirrored run are shown side by side.

## Mirroring rules

| Mirror | Location | Sign changes |
|---|---|---|
| X | x to the other side of the cap centerline | Fx, My, Mz |
| Y | y to the other side of the cap centerline | Fy, Mx, Mz |
| XY | both | Fx, Fy, Mx, My |

`LOAD` lines move to the nearest node of the same kind (pile, pile cap, pier) at the mirror point.
`LOADBP` lines move to the bearing at the mirror point. Lines of cases that are not selected are copied
unchanged. Default selection: `WS`, `WL`, `BR`, `WA`, `TU`.

## Command line

```
python -m fbmp_tools "Pier 7.in" "Pier 7.out" --mirror X Y XY --types WS WL BR WA TU
```

## Section cuts: method and limits

The `.out` file prints pile cap stresses only when that print option is on. The cut forces here are from
statics of the pile cap as a free body (pile head forces, column forces through the connector elements,
cap self weight with the DC factor, loads on cap nodes). They are totals on the full cut, not a distribution
along it. Every results file is checked for equilibrium of the whole cap and the unbalance is reported.
The local-axis convention was calibrated on FB-MultiPier 6 output with vertical piles; battered piles are
not verified.

## Development

```
python scripts/build_notebook.py      # after editing fbmp_tools/
python tests/test_fbmp_tools.py
```

Needs Python 3.9+, numpy, plotly, ipywidgets (all present in Colab).
