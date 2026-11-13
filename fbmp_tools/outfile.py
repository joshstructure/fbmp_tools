"""Read an FB-MultiPier .out file: nodes, elements, load combinations, results.

Only text printed by FB-MultiPier is used.  Coordinates are in the units the
.out file prints (inches for English models); forces in kips, moments kip-ft.
FB-MultiPier's global Z axis points DOWN (gravity loads are +Fz).
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Union

Source = Union[str, Path, bytes, bytearray, Iterable[str]]

_NUM = r"-?\d+\.?\d*(?:[eE][+-]?\d+)?"
_NODE_ROW = re.compile(
    r"^\s*(\d+)\s+([AN])\s+([AN])\s+([AN])\s+([AN])\s+([AN])\s+([AN])\s+"
    rf"({_NUM})\s+({_NUM})\s+({_NUM})\s*$"
)
_BANNER = re.compile(r"^\s*\*{20,}\s*$")
_TITLE = re.compile(r"^\s*\*\s+(.*?)\s+\*\s*$")
_FORCE_HEAD = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(" + _NUM + r"(?:\s+" + _NUM + r"){5,6})\s*$")


@dataclass
class Node:
    id: int
    x: float
    y: float
    z: float
    group: str            # 'pile', 'cap', 'pier', 'bearingL', 'bearingR'
    active: bool = True
    pile: int = 0         # pile number for pile nodes, else 0
    pile_head: bool = False


@dataclass
class FrameElement:
    id: int
    i: int
    j: int
    section: int
    kind: str             # 'pier' (columns, pier cap, connectors) or 'pile'


@dataclass
class CapElement:
    id: int
    nodes: Tuple[int, ...]   # 9 nodes, row-major 3 x 3
    thick: float             # ft
    thick_sw: float          # ft, thickness used for self weight


@dataclass
class OutModel:
    nodes: Dict[int, Node] = field(default_factory=dict)
    frame_elements: List[FrameElement] = field(default_factory=list)
    cap_elements: List[CapElement] = field(default_factory=list)
    n_bearings: Optional[int] = None
    npx: Optional[int] = None
    npy: Optional[int] = None
    cap_unit_weight_pcf: Optional[float] = None
    case_names: List[str] = field(default_factory=list)             # combination matrix columns
    combos: Dict[int, Dict[str, float]] = field(default_factory=dict)  # combo -> {case name: factor}
    title: str = ""
    structure_type: str = ""
    substructures: int = 1

    # -- convenience -----------------------------------------------------
    def group(self, *names: str) -> List[Node]:
        return [n for n in self.nodes.values() if n.group in names]

    @property
    def cap_nodes(self) -> List[Node]:
        return self.group("cap")

    @property
    def pile_heads(self) -> List[Node]:
        return [n for n in self.nodes.values() if n.pile_head]

    def cap_extents(self) -> Tuple[float, float, float, float]:
        """Plan extents of the pile cap; without one (pile bent), of the whole structure."""
        pts = self.cap_nodes or list(self.nodes.values())
        xs = [n.x for n in pts]
        ys = [n.y for n in pts]
        return min(xs), max(xs), min(ys), max(ys)

    def mirror_center(self) -> Tuple[float, float]:
        x0, x1, y0, y1 = self.cap_extents()
        return (x0 + x1) / 2.0, (y0 + y1) / 2.0

    @property
    def cap_z(self) -> float:
        pts = self.cap_nodes or self.pile_heads      # a pile bent has no pile cap: use the pile tops
        return pts[0].z if pts else 0.0


@dataclass
class ComboResult:
    combo: int
    pile_head: Dict[int, Tuple[int, Tuple[float, ...]]] = field(default_factory=dict)   # pile -> (node, 6 local forces)
    connector: Dict[int, Tuple[int, Tuple[float, ...]]] = field(default_factory=dict)   # element -> (cap node, 6 local forces)
    column_base: Dict[int, Tuple[int, Tuple[float, ...]]] = field(default_factory=dict)  # column -> (node, 6 local forces)


def _lines(source: Source) -> Iterator[str]:
    if isinstance(source, (bytes, bytearray)):
        yield from io.TextIOWrapper(io.BytesIO(bytes(source)), encoding="latin-1")
    elif isinstance(source, (str, Path)):
        with open(source, encoding="latin-1") as handle:
            yield from handle
    else:
        yield from source


def read_model(source: Source) -> OutModel:
    """Read the input-interpretation part of a .out file (stops before the results)."""
    model = OutModel()
    section = ""
    group = ""
    pile_no = 0
    first_in_group = False
    matrix_cols: List[str] = []
    after_banner = False
    for raw in _lines(source):
        line = raw.rstrip("\r\n")
        if _BANNER.match(line):
            after_banner = True
            continue
        if after_banner:
            after_banner = False
            m = _TITLE.match(line)
            if m:
                section = re.sub(r"\s+", " ", m.group(1)).upper()
                group = ""
                continue
        if "OUTPUT STAGE 2" in line:
            break
        if not line.strip():
            continue

        if section == "PROJECT DATA":
            m = re.match(r"\s*Project description\s*=\s*(.*)$", line)
            if m:
                model.title = m.group(1).strip()
        elif section == "PILE CAP DATA":
            m = re.search(r"Number of Grid Points in Xp direction.*=\s*(\d+)", line)
            if m:
                model.npx = int(m.group(1))
            m = re.search(r"Number of Grid Points in Yp direction.*=\s*(\d+)", line)
            if m:
                model.npy = int(m.group(1))
        if "SUBSTRUCTURE #" in line:
            m = re.search(r"SUBSTRUCTURE #\s*(\d+)", line)
            if m:
                model.substructures = max(model.substructures, int(m.group(1)))
        m = re.match(r"\s*(?:Type of Structure|Structure Type)\s*=\s*(.+?)\s*$", line)
        if m:
            model.structure_type = m.group(1)

        if section == "PROJECT DATA":
            pass
        elif section == "COLUMN AND PIER CAP DATA":
            m = re.search(r"Number of Bearing Locations Specified\s*=\s*(\d+)", line)
            if m:
                model.n_bearings = int(m.group(1))
        elif section == "NODAL INFORMATION":
            m = re.match(r"^\s*Nodal Coordinates for (.+?)\s*$", line)
            if m:
                name = m.group(1)
                pile_no = 0
                pm = re.match(r"Pile #\s*(\d+)", name)
                if pm:
                    group, pile_no = "pile", int(pm.group(1))
                elif "Pile Cap" in name:
                    group = "cap"
                elif "Substructure" in name:
                    group = "pier"
                elif "Left Bearing" in name:
                    group = "bearingL"
                elif "Right Bearing" in name:
                    group = "bearingR"
                else:
                    group = name
                first_in_group = True
                continue
            m = _NODE_ROW.match(line)
            if m and group:
                nid = int(m.group(1))
                if nid not in model.nodes:
                    model.nodes[nid] = Node(
                        nid, float(m.group(8)), float(m.group(9)), float(m.group(10)), group,
                        m.group(2) == "A", pile_no, group == "pile" and first_in_group,
                    )
                first_in_group = False
        elif section.startswith("PILE CAP PROPERTIES"):
            m = re.search(r"Unit Weight\s*=\s*(" + _NUM + ")", line)
            if m:
                model.cap_unit_weight_pcf = float(m.group(1))
        elif section.endswith("CONNECTIVITY") and not section.startswith("PILE CAP"):
            parts = line.split()
            if len(parts) >= 4 and all(p.isdigit() for p in parts[:4]):   # extra members add an end-fixity column
                if section.startswith("PILE"):
                    kind = "pile"
                elif section.startswith("COLUMN"):
                    kind = "pier"
                else:
                    kind = section.replace("CONNECTIVITY", "").replace("ELEMENT", "").strip().lower() or "frame"
                model.frame_elements.append(FrameElement(int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), kind))
        elif section == "PILE CAP ELEMENT CONNECTIVITY":
            parts = line.split()
            if len(parts) == 13 and all(p.isdigit() for p in parts[:11]):
                model.cap_elements.append(
                    CapElement(int(parts[0]), tuple(int(p) for p in parts[2:11]), float(parts[11]), float(parts[12]))
                )
        elif section == "LOAD COMBINATION MATRIX":
            parts = line.split()
            if parts[:2] == ["LOAD", "COMB"]:
                matrix_cols = parts[2:]
                for name in matrix_cols:
                    if name not in model.case_names:
                        model.case_names.append(name)
            elif matrix_cols and parts[0].isdigit() and len(parts) == len(matrix_cols) + 1:
                row = model.combos.setdefault(int(parts[0]), {})
                for name, value in zip(matrix_cols, parts[1:]):
                    row[name] = float(value)
    if not model.nodes:
        raise ValueError(
            "No nodal coordinates found. Give the FB-MultiPier .out file of a run of the base model "
            "(it must contain the NODAL INFORMATION section)."
        )
    return model


def read_results(source: Source, model: OutModel, combos: Optional[Iterable[int]] = None) -> Dict[int, ComboResult]:
    """Read pile-head, connector and column-base forces for every load combination.

    Forces are returned as printed (element local axes 1-2-3, force on the element end).
    """
    wanted = set(combos) if combos is not None else None
    results: Dict[int, ComboResult] = {}
    current: Optional[ComboResult] = None
    mode = ""            # 'pile', 'connector', 'column', ''
    index = 0            # pile or column number
    need_first = False
    in_pile = False      # pile displacement and soil tables reuse the "Pile Number" heading
    for raw in _lines(source):
        if "RESULTS FOR LOAD COMBINATION" in raw:
            m = re.search(r"RESULTS FOR LOAD COMBINATION\s+(\d+)", raw)
            combo = int(m.group(1))
            current = None if (wanted is not None and combo not in wanted) else results.setdefault(combo, ComboResult(combo))
            mode = ""
            in_pile = False
            continue
        if current is None:
            continue
        if raw.startswith(" -") or raw.startswith(" *") or raw.startswith(" _"):
            if "- Analytical Force Results for each Pile" in raw:
                in_pile = True
            m = re.search(r"- Pile Number\s+(\d+)", raw) if in_pile else None
            if m:
                mode, index, need_first = "pile", int(m.group(1)), True
                continue
            if "- Connector Beam Sections" in raw:
                mode = "connector"
                continue
            m = re.search(r"- Column Sections For Column Number\s+(\d+)", raw)
            if m:
                mode, index, need_first = "column", int(m.group(1)), True
                continue
            if raw.startswith(" - ") or raw.startswith(" **"):
                mode = ""
            continue
        if not mode:
            continue
        if mode == "connector":
            # second row of each element is the pile cap end: "prop node  f1..f6 d/c"
            parts = raw.split()
            lead = 0
            while lead < len(parts) and parts[lead].isdigit():
                lead += 1
            if lead == 4 and len(parts) >= 10:
                index = int(parts[0])
            elif lead in (2, 3) and len(parts) >= lead + 6:
                try:
                    vals = tuple(float(v) for v in parts[lead:lead + 6])
                except ValueError:
                    continue
                current.connector[index] = (int(parts[1]), vals)
            continue
        if need_first:
            m = _FORCE_HEAD.match(raw)
            if m:
                node = int(m.group(3))
                vals = tuple(float(v) for v in m.group(5).split()[:6])
                if mode == "pile":
                    current.pile_head[index] = (node, vals)
                else:
                    current.column_base[index] = (node, vals)
                need_first = False
    return results
