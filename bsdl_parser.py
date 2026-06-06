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
    return re.sub(r"--.*", "", text)


def _collect_attribute(text: str, name: str) -> str:
    m = re.search(rf"attribute\s+{re.escape(name)}\s+of\s+\w+\s*:\s*entity\s+is\s*(.*?);", text, re.I | re.S)
    if not m:
        return ""
    raw = m.group(1)
    pieces = re.findall(r'"(.*?)"', raw, re.S)
    return "".join(pieces) if pieces else raw.strip().strip('"')


def _to_int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


def normalize_instruction_name(name: str) -> str:
    return name.strip().upper().replace("-", "_")


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
    # Accept names like SAMPLE/PRELOAD, SAMPLE_PRELOAD, AVR_RESET.
    for name, code in re.findall(r"([A-Za-z0-9_./-]+)\s*\(([^)]*)\)", inst_raw):
        clean_code = re.sub(r"[^01]", "", code)
        if clean_code:
            instructions[normalize_instruction_name(name)] = clean_code
            if normalize_instruction_name(name) == "SAMPLE/PRELOAD":
                instructions.setdefault("SAMPLE", clean_code)
                instructions.setdefault("SAMPLE_PRELOAD", clean_code)

    pins = set()
    pm = re.search(r"port\s*\((.*?)\)\s*;", text, re.I | re.S)
    if pm:
        for decl in re.split(r";", pm.group(1)):
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
    for idx, inside in re.findall(r"(\d+)\s*\((.*?)\)", braw, re.S):
        parts = [p.strip() for p in inside.split(",")]
        while len(parts) < 4:
            parts.append("")
        cell = BoundaryCell(
            index=int(idx),
            cell_type=parts[0],
            port=parts[1],
            function=parts[2].lower(),
            safe=parts[3],
            ccell=_to_int(parts[4]) if len(parts) > 4 else None,
            disval=parts[5].strip() if len(parts) > 5 else None,
            rslt=parts[6].strip() if len(parts) > 6 else None,
        )
        boundary.append(cell)
        if cell.port and cell.port not in {"*", "internal", "control", "CONTROL"}:
            pins.add(cell.port)

    boundary.sort(key=lambda c: c.index)
    return BsdlInfo(entity, instruction_length, boundary_length, idcode, instructions, sorted(pins), boundary)


def pin_cells(info: BsdlInfo):
    by_index = {c.index: c for c in info.boundary}
    d = {}
    for c in info.boundary:
        if not c.port or c.port in {"*", "internal", "control", "CONTROL"}:
            continue
        p = d.setdefault(c.port, {"input": [], "output": [], "control": [], "other": []})
        fn = c.function.lower()
        if "input" in fn or "observe" in fn:
            p["input"].append(c)
        elif "output" in fn:
            p["output"].append(c)
            if c.ccell is not None and c.ccell in by_index:
                p["control"].append(by_index[c.ccell])
        elif "control" in fn or "enable" in fn:
            p["control"].append(c)
        else:
            p["other"].append(c)
    # remove duplicate controls
    for p in d.values():
        seen = set(); uniq = []
        for c in p["control"]:
            if c.index not in seen:
                uniq.append(c); seen.add(c.index)
        p["control"] = uniq
    return d
