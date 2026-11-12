FB-MultiPier Load Mirroring Tool
================================

PURPOSE
-------
When a pier is re-analyzed with as-built (non-symmetric) pile locations, the design
loading has to be applied from every angle of attack. Instead of mirroring the pile
grid and rebuilding the cap, missing piles, p-multipliers and node loads for each
case, this tool keeps the as-built foundation exactly as modeled and mirrors the
DIRECTIONAL LOADS: wind, braking, water and uniform temperature. Gravity and all other loads stay exactly
as in the base model. It writes three new input files from one base model:

    <name>_MirrorX.in    loads mirrored across the cap centerline normal to X
    <name>_MirrorY.in    loads mirrored across the cap centerline normal to Y
    <name>_MirrorXY.in   both (equivalent to rotating those loads 180 degrees)

By default the mirrored load types are:
    WS*  wind on structure
    WL*  wind on live load
    BR*  braking force
    WA*  water load
    TU*  uniform temperature (bearing forces along the bridge)
Every other type is copied unchanged: DC, CR, SH, LL, IM, CE and any others.
Note: creep (CR) and shrinkage (SH) can also carry forces along the bridge; add
"CR*" or "SH*" to -MirrorTypes if they should follow the mirrored direction.
Use -MirrorTypes to change this (see below).

Run the base model plus these three in FB-MultiPier and envelope the results.
Because the foundation never changes, pile numbers and node numbers in all
four sets of results refer to the same as-built piles.

The tool is generic: every pier-specific fact (grid, node numbering, pile layout,
number of bearings, p-multiplier list lengths) is read from the files you give it.

WHAT YOU NEED
-------------
1. The base .in file: as-built foundation, with the full loading applied.
2. The .out file from a run of that same base model. FB-MultiPier prints the
   coordinates of every node in the "NODAL INFORMATION" section of the .out, and the
   tool uses that to find each load's mirror-image node. Any analysis run of the
   base model will do; only the first part of the .out is read.
3. Optional: .in files whose p-multiplier lists were calibrated for the mirrored
   directions (see P-MULTIPLIERS below).

HOW TO RUN
----------
Easiest: drag the base .in file onto "Mirror Loads.bat". If a .out with the same
name sits next to it, it is picked up automatically; otherwise you are asked for it.
You are then asked, optionally, for .in files that supply the X and Y p-multiplier
lists. Output goes next to the base .in.

From PowerShell (more options):

    powershell -ExecutionPolicy Bypass -File FBMP-MirrorLoads.ps1 `
        -InFile "Pier 7_AASHTO_STR_1.in" -OutFile "Pier 7_AASHTO_STR_1.out"

    Options:
      -Mirror X|Y|XY|All        which files to write (default All)
      -OutputDir <folder>       where to write (default: folder of the .in)
      -Tolerance <inches>       flag node matches farther than this (default 24)
      -CenterX / -CenterY       override the mirror planes (default: cap extents midpoint)
      -PMultXFile <file.in>     take the X p-multiplier list from this file (X and XY cases)
      -PMultYFile <file.in>     take the Y p-multiplier list from this file (Y and XY cases)
      -PMultX "a,b,c,..."       give the X list directly (same order as in the .in)
      -PMultY "a,b,c,..."       give the Y list directly
      -MirrorTypes "WS*,WL*,BR*,WA*,TU*"
                                load types (T= value) to mirror; this is the default.
                                Examples: "WS*,WL*" mirrors wind only,
                                "*" mirrors every load type.
      -TitleSuffix "<text>"     text appended to the last PROBLEM line

WHAT THE TOOL CHANGES
---------------------
Only these parts of the .in file are rewritten; everything else is copied verbatim.
Within LOAD and LOADBP, only lines whose type matches -MirrorTypes (default WS*, WL*,
BR*, WA*) are changed; all other load lines are copied byte for byte.

  LOAD    (nodal loads)      each mirrored load moves to the node at the mirror-image
                             position and its components change sign as follows:
                               Mirror X : Fx, My, Mz
                               Mirror Y : Fy, Mx, Mz
                               Mirror XY: Fx, Fy, Mx, My
  LOADBP  (bearing loads)    Mirror X swaps bearing i with bearing N+1-i;
                             Mirror Y swaps the L and R bearing rows; same sign rules.
  PILE    (p-multipliers)    only if you supply replacement lists.
  PROBLEM (title line)       suffix " - Mirror X WS WL BR WA TU" etc.

Node matching: a loaded node is matched to the nearest node of the same kind (pile
node, cap node, pier node) at the mirrored position. Pier and cap nodes should match
almost exactly (fractions of an inch). Pile nodes on an as-built layout can be off by
the as-built deviation (typically several inches); the match is still the right pile
as long as the deviation is well under half the pile spacing.

CHECK THE REPORT
----------------
Each generated file comes with <name>_Mirror?_report.txt and <name>_Mirror?_nodes.csv.
Look at:
  - "Load types mirrored" and "Load types copied unchanged". Confirm the wind,
    braking and water cases are in the first list and the gravity and live load
    cases in the second.
  - "largest offset" per node kind. Pier and cap should be near zero. Pile offsets
    should be no larger than the as-built deviations.
  - Any "CHECK" entries (match farther than the tolerance) or "WARNING" lines
    (node not found, two loads stacked on one node, bearing rows not symmetric).
  - The resultant table: for every load case the new sums of forces and the moments
    of those forces about the mirror planes are listed next to the values expected
    from an exact mirror. Small differences come from node offsets only.
  - The nodes.csv lists every loaded node, its coordinates, the ideal mirror point,
    the node chosen and the distance between them.

Also open one generated file in FB-MultiPier and confirm visually that a
distinctive load (for example a wind case) now sits on the opposite column.

P-MULTIPLIERS
-------------
FB-MultiPier applies the p-multiplier list "from lead to trail row based on the
actual displacement" (see the P-Multiplier table printed with each load combination
in the .out). The list in the .in is therefore relative to the displacement direction
and does not need to be reordered for a mirrored load set. The tool leaves the base
lists unchanged unless told otherwise.

If the lists were calibrated to the irregular as-built spacing for a specific
direction (as in the mirrored-foundation workflow), supply the list calibrated for the
mirrored direction with -PMultXFile / -PMultYFile (for example the existing
mirrored-foundation .in for that direction) or -PMultX / -PMultY. The report shows
exactly which lists were written.

ASSUMPTIONS AND LIMITS
----------------------
- The pier, cap and bearing layout are symmetric about the cap centerlines. The tool
  checks this (pier node mirror offsets and bearing symmetry are reported) and warns
  if they are not. With a non-symmetric pier, mirroring loads is not equivalent to
  mirroring the foundation and the approach should be reconsidered.
- Mirror planes default to the midpoint of the pile cap extents from the .out file.
  Use -CenterX / -CenterY if the cap is not what the loading should be mirrored about.
- Load types are recognized by the T= label FB-MultiPier writes on each load line.
  If a model labels wind, braking or water differently, set -MirrorTypes to match.
- Only LOAD and LOADBP are mirrored. If a model carries other load types (for example
  distributed loads, soil movements, dynamic load functions), handle those by hand.
- Wind cases are mirrored as nodal loads. Do not regenerate wind loads from the WIND
  page in the GUI for a generated file; that would overwrite them.
- The .out must come from the same foundation model as the .in (same grid, missing
  piles and pier). If the node numbering differs, matches will be wrong; the report's
  offsets will show it.
- Requires Windows PowerShell 5.1 or later (standard on Windows 10/11). No other
  software is needed.
