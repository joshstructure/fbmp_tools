"""Read the LOAD and LOADBP sections of an FB-MultiPier .in file.

Every line of the file is kept as text so a rewritten file differs from the
base only on the load lines that were changed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

COMPONENTS = ("Fx", "Fy", "Fz", "Mx", "My", "Mz")

_SECTION = re.compile(r"^[A-Z_]+\s*$")
_LOAD = re.compile(
    r"^(?P<pre>\s*)(?P<node>\d+)(?P<mid>\s+L=\s*(?P<case>\d+)\s+F=\s*)"
    r"(?P<f1>\S+)\s+(?P<f2>\S+)\s+(?P<f3>\S+)\s+(?P<f4>\S+)\s+(?P<f5>\S+)\s+(?P<f6>\S+)(?P<rest>.*)$"
)
_LOADBP = re.compile(
    r"^(?P<pre>\s*)(?P<node>\d+)(?P<mid>\s+L=\s*(?P<case>\d+)\s+F=\s*)"
    r"(?P<f1>\S+)\s+(?P<f2>\S+)\s+(?P<f3>\S+)\s+(?P<f4>\S+)\s+(?P<f5>\S+)\s+(?P<f6>\S+)"
    r"(?P<rest1>\s+T=\s*\S+\s+B=\s*)(?P<side>[LR])(?P<rest2>.*)$"
)
_TYPE = re.compile(r"T=\s*(\S+)")


@dataclass
class LoadLine:
    index: int                 # line index in InFile.lines
    section: str               # 'LOAD' or 'LOADBP'
    target: int                # node number (LOAD) or bearing number (LOADBP)
    case: int
    values: Tuple[str, ...]    # six components as written
    type: str                  # T= label, e.g. 'WS3'
    side: str = ""             # 'L' / 'R' for LOADBP
    pre: str = ""
    mid: str = ""
    rest: str = ""             # LOAD: text after the six values. LOADBP: text up to the side letter
    rest2: str = ""            # LOADBP: text after the side letter

    @property
    def floats(self) -> Tuple[float, ...]:
        return tuple(float(v) for v in self.values)

    @property
    def family(self) -> str:
        return load_family(self.type)

    def render(self, target: Optional[int] = None, values: Optional[Tuple[str, ...]] = None,
               side: Optional[str] = None) -> str:
        target = self.target if target is None else target
        values = self.values if values is None else values
        if self.section == "LOADBP":
            return f"{self.pre}{target}{self.mid}{' '.join(values)}{self.rest}{side or self.side}{self.rest2}"
        return f"{self.pre}{target}{self.mid}{' '.join(values)}{self.rest}"


@dataclass
class InFile:
    lines: List[str]
    sections: List[str]                 # per line: '', 'NAME', 'NAME#HEADER', 'NAME#END'
    newline: str = "\r\n"
    loads: List[LoadLine] = field(default_factory=list)
    unparsed: List[int] = field(default_factory=list)   # LOAD / LOADBP line indexes that were not understood

    def case_types(self) -> Dict[int, str]:
        """Load case number -> load type label."""
        out: Dict[int, str] = {}
        for ld in self.loads:
            out.setdefault(ld.case, ld.type)
        return out

    def types(self) -> List[str]:
        seen: Dict[str, None] = {}
        for ld in sorted(self.loads, key=lambda l: l.case):
            seen.setdefault(ld.type)
        return list(seen)

    def families(self) -> List[str]:
        seen: Dict[str, None] = {}
        for name in self.types():
            seen.setdefault(load_family(name))
        return list(seen)

    def text(self, lines: Optional[List[str]] = None) -> str:
        return self.newline.join(self.lines if lines is None else lines) + self.newline


def load_family(type_label: str) -> str:
    """'WS12' -> 'WS', 'LL3' -> 'LL', 'TU' -> 'TU'."""
    return re.sub(r"\d+$", "", type_label) or type_label


def decode(data) -> str:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data).decode("latin-1")
    return data


def parse_in(data) -> InFile:
    text = decode(data)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = re.split(r"\r?\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    sections: List[str] = []
    current: Optional[str] = None
    for line in lines:
        if current is None:
            if _SECTION.match(line):
                current = line.strip()
                sections.append(current + "#HEADER")
            else:
                sections.append("")
        elif line.strip() == ":":
            sections.append(current + "#END")
            current = None
        else:
            sections.append(current)
    infile = InFile(lines, sections, newline)
    for i, (line, sec) in enumerate(zip(lines, sections)):
        if sec == "LOAD":
            m = _LOAD.match(line)
            if m:
                t = _TYPE.search(m.group("rest"))
                infile.loads.append(LoadLine(
                    i, "LOAD", int(m.group("node")), int(m.group("case")),
                    tuple(m.group(f"f{k}") for k in range(1, 7)), t.group(1) if t else "",
                    "", m.group("pre"), m.group("mid"), m.group("rest"),
                ))
            elif line.strip():
                infile.unparsed.append(i)
        elif sec == "LOADBP":
            m = _LOADBP.match(line)
            if m:
                t = _TYPE.search(m.group("rest1"))
                infile.loads.append(LoadLine(
                    i, "LOADBP", int(m.group("node")), int(m.group("case")),
                    tuple(m.group(f"f{k}") for k in range(1, 7)), t.group(1) if t else "",
                    m.group("side"), m.group("pre"), m.group("mid"), m.group("rest1"), m.group("rest2"),
                ))
            elif line.strip():
                infile.unparsed.append(i)
    return infile
