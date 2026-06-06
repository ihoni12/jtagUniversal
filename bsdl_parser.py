import re
from dataclasses import dataclass
from typing import Dict, List, Optional

@dataclass
class BoundaryCell:
    index: int
    cell_type: str
    port: str
    function: str
    safe: str
    ccell: Optional[int] = None
    disval: Optional[str] = None
    rslt: Optional[str] = None

@dataclass
class BsdlInfo:
    entity: str
    instruction_length: int
    boundary_length: int
    idcode: Optional[str]
    instructions: Dict[str, str]
    pins: List[str]
    boundary: List[BoundaryCell]


def _clean(text: str) -> str:
    text = re.sub(r"--.*", "", text)
    return text


def _collect_attribute(text: str, name: str) -> str:
    # BSDL attributes are often split in quoted strings joined by &.
    m = re.search(rf"attribute\s+{re.escape(name)}\s+of\s+\w+\s*:\s*entity\s+is\s*(.*?);", text, re.I | re.S)
    if not m:
        return ""
    raw = m.group(1)
    pieces = re.findall(r'"(.*?)"', raw, re.S)
    return "".join(pieces) if pieces else raw.strip().strip('"')


def parse_bsdl(text: str) -> BsdlInfo:
    text = _clean(text)
    ent = re.search(r"entity\s+(\w+)\s+is", text, re.I)
    entity = ent.group(1) if ent else "UNKNOWN"

    ilen_m = re.search(r"attribute\s+INSTRUCTION_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)\s*;", text, re.I)
    instruction_length = int(ilen_m.group(1)) if ilen_m else 0

    blen_m = re.search(r"attribute\s+BOUNDARY_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)\s*;", text, re.I)
    boundary_length = int(blen_m.group(1)) if blen_m else 0

    idcode = _collect_attribute(text, "IDCODE_REGISTER") or None

    inst_raw = _collect_attribute(text, "INSTRUCTION_OPCODE")
    instructions: Dict[str, str] = {}
    for name, code in re.findall(r"(\w+)\s*\(([^)]*)\)", inst_raw):
        instructions[name.upper()] = re.sub(r"[^01]", "", code)

    # PORT declarations: port (A : in bit; B : inout bit_vector(...));
    pins = set()
    pm = re.search(r"port\s*\((.*?)\)\s*;", text, re.I | re.S)
    if pm:
        port_body = pm.group(1)
        for decl in re.split(r";", port_body):
            decl = decl.strip()
            if not decl or ":" not in decl:
                continue
            names, _rest = decl.split(":", 1)
            for n in names.split(","):
                name = n.strip()
                if name:
                    pins.add(name)

    braw = _collect_attribute(text, "BOUNDARY_REGISTER")
    boundary: List[BoundaryCell] = []
    # Handles entries like: 0 (BC_1, PINA0, input, X),
    for idx, inside in re.findall(r"(\d+)\s*\((.*?)\)", braw, re.S):
        parts = [p.strip() for p in inside.split(",")]
        while len(parts) < 4:
            parts.append("")
        def to_int(v):
            try:
                return int(v)
            except Exception:
                return None
        boundary.append(BoundaryCell(
            index=int(idx),
            cell_type=parts[0],
            port=parts[1],
            function=parts[2].lower(),
            safe=parts[3],
            ccell=to_int(parts[4]) if len(parts) > 4 else None,
            disval=parts[5] if len(parts) > 5 else None,
            rslt=parts[6] if len(parts) > 6 else None,
        ))
        if parts[1] and parts[1] not in {"*", "internal", "control", "CONTROL"}:
            pins.add(parts[1])

    boundary.sort(key=lambda c: c.index)
    return BsdlInfo(entity, instruction_length, boundary_length, idcode, instructions, sorted(pins), boundary)


def pin_cells(info: BsdlInfo):
    d = {}
    for c in info.boundary:
        if not c.port or c.port in {"*", "internal", "control", "CONTROL"}:
            continue
        p = d.setdefault(c.port, {"input": [], "output": [], "control": [], "other": []})
        if "input" in c.function or "observe" in c.function:
            p["input"].append(c)
        elif "output" in c.function:
            p["output"].append(c)
        elif "control" in c.function or "enable" in c.function:
            p["control"].append(c)
        else:
            p["other"].append(c)
    return d
