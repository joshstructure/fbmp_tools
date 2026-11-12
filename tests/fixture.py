"""A small synthetic FB-MultiPier model (no project data) written in the .in / .out text formats.

Pile cap 240 x 120 in, one 9-node element, four vertical piles at the corners, one column at the
center carried to the cap through its base node, two bearings per row.  Z is down.
"""
from __future__ import annotations

CAP_NODES = {  # id: (x, y)
    1: (0, 120), 2: (240, 120), 3: (0, 0), 4: (240, 0),            # pile heads
    10: (120, 120), 11: (0, 60), 12: (120, 60), 13: (240, 60), 14: (120, 0),
}
PILE_LOWER = {1: 21, 2: 22, 3: 23, 4: 24}
CAP_Z = -12.0
WEIGHT = 240 * 120 / 144 * 2.0 * 0.150      # kips, 2 ft thick for self weight
COLUMN_LOAD = {1: 1000.0, 2: 700.0}          # combination -> factored column axial
FY = {1: 0.0, 2: 40.0}                       # combination -> column shear carried to the cap in +Y


def _row(node, x, y, z, flag="A"):
    return f" {node:4d}    {flag}    {flag}    {flag}    {flag}    {flag}    {flag}    {x:10.3f}    {y:10.3f}    {z:10.3f}\n"


def out_text() -> str:
    s = []
    star = " " + "*" * 63 + "\n"

    def banner(title):
        s.extend(["\n", star, f" *{title:^61}*\n", star, "\n"])

    banner("PROJECT DATA")
    s.append(" Project description      = Synthetic pier\n")
    banner("PILE CAP DATA")
    s.append(" Number of Grid Points in Xp direction (NPX) =    3\n Number of Grid Points in Yp direction (NPY) =    3\n")
    banner("COLUMN AND PIER CAP DATA")
    s.append(" Number of Bearing Locations Specified      =          2\n")
    banner("NODAL INFORMATION")
    for pile, head in enumerate((1, 2, 3, 4), start=1):
        x, y = CAP_NODES[head]
        s.append(f" Nodal Coordinates for Pile #{pile:5d}\n\n")
        s.append(_row(head, x, y, CAP_Z))
        s.append(_row(PILE_LOWER[head], x, y, CAP_Z + 100))
        s.append("\n")
    s.append(" Nodal Coordinates for Pile Cap\n\n")
    for node in (10, 11, 12, 13, 14):
        s.append(_row(node, *CAP_NODES[node], CAP_Z, "A" if node == 12 else "N"))
    s.append("\n Nodal Coordinates for Substructure Frame Elements\n\n")
    s.append(_row(30, 120, 60, CAP_Z))
    s.append(_row(31, 120, 60, CAP_Z - 200))
    s.append(_row(32, 60, 60, CAP_Z - 200))
    s.append(_row(33, 180, 60, CAP_Z - 200))
    s.append("\n Nodal Coordinates for Left Bearing Row\n\n")
    s.append(_row(40, 60, 50, CAP_Z - 200))
    s.append(_row(41, 180, 50, CAP_Z - 200))
    s.append("\n Nodal Coordinates for Right Bearing Row\n\n")
    s.append(_row(42, 60, 70, CAP_Z - 200))
    s.append(_row(43, 180, 70, CAP_Z - 200))
    banner("PILE CAP PROPERTIES (SHELL ELEMENTS)")
    s.append(" Unit Weight           =     150.0000   pcf\n")
    banner("COLUMN AND PIER CAP CONNECTIVITY")
    s.append(" ELEMENT     NODES     SECTION\n  NUMBER     I    J     NUMBER\n")
    s.append("    1       30   31          1\n    2       32   31          2\n    3       31   33          2\n")
    banner("PILE ELEMENT CONNECTIVITY")
    for k, head in enumerate((1, 2, 3, 4)):
        s.append(f"   {11 + k}     {PILE_LOWER[head]:4d} {head:4d}          1\n")
    banner("PILE CAP ELEMENT CONNECTIVITY")
    s.append("    1    1    1   10    2   11   12   13    3   14    4     2.00     2.00\n")
    banner("LOAD COMBINATION MATRIX")
    s.append(" LOAD COMB       DC         BR1        WS1        TU \n")
    s.append("         1   1.0000E+00 0.0000E+00 0.0000E+00 0.0000E+00\n")
    s.append("         2   1.0000E+00 1.0000E+00 1.0000E+00 1.0000E+00\n")
    s.append("\n * * * * * * * *\n *  OUTPUT STAGE 2 OF 2:  ANALYSIS RESULTS  *\n * * * * * * * *\n")
    for combo in (1, 2):
        banner(f"RESULTS FOR LOAD COMBINATION {combo:6d}")
        p = (COLUMN_LOAD[combo] + WEIGHT) / 4.0
        fy = FY[combo] / 4.0
        s.append(" - Analytical Force Results for each Pile\n")
        for k, head in enumerate((1, 2, 3, 4), start=1):
            s.append(f" - Pile Number {k:3d}                                             -\n")
            s.append(" ELEM PROP NODE  LOAD           FAX        F22        F33        M22        M33     TORQUE        D/C\n")
            # force on the pile element at its head; local 1 points up, local 3 = -Y
            s.append(f"   {10 + k}   1  {head:4d}  {combo:4d}  {-p:11.2f}  {0:9.2f}  {-fy:9.2f}  {0:9.2f}  {0:9.2f}  {0:9.2f}  {0:9.2f}\n")
            s.append(f"        1  {PILE_LOWER[head]:4d}  {combo:4d}  {p:11.2f}  {0:9.2f}  {fy:9.2f}  {0:9.2f}  {0:9.2f}  {0:9.2f}  {0:9.2f}\n")
        banner("ANALYTICAL FORCE RESULTS FOR SUBSTRUCTURE (EXCLUDING PILES)")
        s.append(" - Column Sections For Column Number   1\n")
        s.append(" ELEM PROP NODE  LOAD           FAX        F22        F33        M22        M33     TORQUE        D/C\n")
        s.append(f"    1   1    30  {combo:4d}  {COLUMN_LOAD[combo]:11.2f}  {0:9.2f}  {FY[combo]:9.2f}  {0:9.2f}  {0:9.2f}  {0:9.2f}  {0:9.2f}\n")
        s.append(f"        1    31        {-COLUMN_LOAD[combo]:11.2f}  {0:9.2f}  {-FY[combo]:9.2f}  {0:9.2f}  {0:9.2f}  {0:9.2f}  {0:9.2f}\n")
        s.append(" - Pier Cap Sections\n")
    return "".join(s)


def in_text() -> str:
    return "\r\n".join([
        "PROBLEM", "Synthetic pier", "Units are Kips and Inches", ":",
        "LOAD",
        "12 L= 1 F= 0 0 5 0 0 0 T=DC",
        "21 L= 3 F= 1.5 0.25 0 0 0 0 T=WS1",
        "23 L= 3 F= 2.5 0.25 0 0 0 0 T=WS1",
        "30 L= 4 F= 3 0 0 0 -90 0 T=TU",
        ":",
        "LOADBP",
        "1 L= 1 F= 0 0 100 0 0 0 T=DC B= L",
        "1 L= 1 F= 0 0 100 0 0 0 T=DC B= R",
        "2 L= 1 F= 0 0 120 0 0 0 T=DC B= L",
        "2 L= 1 F= 0 0 120 0 0 0 T=DC B= R",
        "1 L= 2 F= 0 4.17 0.36 0 0 0 T=BR1 B= L",
        "1 L= 2 F= 0 4.17 -0.36 0 0 0 T=BR1 B= R",
        "2 L= 2 F= 0 4.17 0.36 0 0 0 T=BR1 B= L",
        "2 L= 2 F= 0 4.17 -0.36 0 0 0 T=BR1 B= R",
        "1 L= 3 F= 6 1 -2 0 0 0 T=WS1 B= L",
        "2 L= 3 F= 7 1 -3 0 0 0 T=WS1 B= R",
        "1 L= 4 F= 0 -6.02 0 0 0 0 T=TU B= L",
        "1 L= 4 F= 0 8.77 0 0 0 0 T=TU B= R",
        ":",
        "",
    ])
