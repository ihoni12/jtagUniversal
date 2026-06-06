from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import re, uuid, json

BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
UPLOADS.mkdir(exist_ok=True)

app = FastAPI(title="BSDL Test Station")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
DB = {}

def clean_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)

def remove_comments(text: str) -> str:
    # BSDL/VHDL comments start with --
    return re.sub(r"--.*", "", text)

def get_attribute(text: str, attr: str):
    pat = rf"attribute\s+{re.escape(attr)}\s+of\s+\w+\s*:\s*\w+\s+is\s*(.*?);"
    m = re.search(pat, text, re.I | re.S)
    if not m:
        return None
    return m.group(1).strip()

def strings_join(value: str) -> str:
    if not value:
        return ""
    parts = re.findall(r'"(.*?)"', value, re.S)
    return "".join(parts).replace("&", " ").strip() if parts else value.strip().strip('"')

def parse_int_attr(text, attr):
    v = get_attribute(text, attr)
    if not v: return None
    m = re.search(r"\d+", v)
    return int(m.group(0)) if m else None

def parse_port_map(text: str):
    ports = []
    # entity X is port (...); generic parser
    m = re.search(r"port\s*\((.*?)\)\s*;", text, re.I | re.S)
    if not m: return ports
    body = m.group(1)
    for line in body.split(';'):
        line = line.strip()
        if not line or ':' not in line: continue
        left, right = line.split(':', 1)
        names = [x.strip() for x in left.split(',') if x.strip()]
        right_clean = " ".join(right.split())
        direction = "unknown"
        typ = right_clean
        mm = re.search(r"\b(inout|buffer|linkage|in|out)\b\s*(.*)", right_clean, re.I)
        if mm:
            direction = mm.group(1).lower()
            typ = mm.group(2).strip()
        for n in names:
            ports.append({"name": n, "direction": direction, "type": typ})
    return ports

def parse_pin_map(text: str):
    raw = strings_join(get_attribute(text, "PIN_MAP") or "")
    pins = []
    if not raw: return pins
    # examples: PA0: "78", PA1: "77" or PA0: 78
    for m in re.finditer(r"([A-Za-z_][\w\[\]\(\)\.]*?)\s*:\s*\"?([A-Za-z0-9_\-\.]+)\"?", raw):
        pins.append({"port": m.group(1).strip(), "package_pin": m.group(2).strip()})
    return pins

def parse_instruction_opcode(text: str):
    raw = strings_join(get_attribute(text, "INSTRUCTION_OPCODE") or "")
    out = []
    if not raw: return out
    # EXTEST (0000), BYPASS (1111), SAMPLE (0010)
    for m in re.finditer(r"([A-Za-z0-9_/]+)\s*\(([^\)]+)\)", raw):
        out.append({"instruction": m.group(1).strip(), "opcode": m.group(2).replace(' ', '').strip()})
    return out

def parse_register_access(text: str):
    raw = strings_join(get_attribute(text, "REGISTER_ACCESS") or "")
    out = []
    if not raw: return out
    for m in re.finditer(r"([A-Za-z0-9_]+)\s*\(([^\)]+)\)", raw):
        out.append({"register": m.group(1).strip(), "instructions": [x.strip() for x in m.group(2).split(',') if x.strip()]})
    return out

def parse_idcode(text: str):
    raw = strings_join(get_attribute(text, "IDCODE_REGISTER") or "")
    bits = re.sub(r"[^01Xx]", "", raw)
    hexval = None
    if bits:
        try:
            hexval = "0x" + format(int(bits.replace('X','0').replace('x','0'), 2), "08x")
        except Exception:
            pass
    return {"bits": bits or None, "hex": hexval}

def parse_boundary(text: str):
    raw = strings_join(get_attribute(text, "BOUNDARY_REGISTER") or "")
    cells = []
    if not raw: return cells
    # accepts: 0 (BC_1, PA0, input, X),
    for m in re.finditer(r"(\d+)\s*\((.*?)\)", raw, re.S):
        bit = int(m.group(1))
        parts = [p.strip() for p in m.group(2).split(',')]
        cell = {
            "bit": bit,
            "cell": parts[0] if len(parts)>0 else "",
            "port": parts[1] if len(parts)>1 else "",
            "function": parts[2] if len(parts)>2 else "",
            "safe": parts[3] if len(parts)>3 else "",
            "control_cell": parts[4] if len(parts)>4 else "",
            "disable_value": parts[5] if len(parts)>5 else "",
            "disable_result": parts[6] if len(parts)>6 else "",
        }
        cells.append(cell)
    return cells

def parse_bsdl(text: str):
    t = remove_comments(text)
    entity = None
    m = re.search(r"entity\s+(\w+)\s+is", t, re.I)
    if m: entity = m.group(1)
    ports = parse_port_map(t)
    pin_map = parse_pin_map(t)
    pin_lookup = {p['port']: p['package_pin'] for p in pin_map}
    boundary = parse_boundary(t)
    for c in boundary:
        c['package_pin'] = pin_lookup.get(c.get('port',''), '')
    instructions = parse_instruction_opcode(t)
    idcode = parse_idcode(t)
    attrs = {
        "INSTRUCTION_LENGTH": parse_int_attr(t, "INSTRUCTION_LENGTH"),
        "BOUNDARY_LENGTH": parse_int_attr(t, "BOUNDARY_LENGTH"),
        "TAP_SCAN_IN": strings_join(get_attribute(t, "TAP_SCAN_IN") or ""),
        "TAP_SCAN_OUT": strings_join(get_attribute(t, "TAP_SCAN_OUT") or ""),
        "TAP_SCAN_MODE": strings_join(get_attribute(t, "TAP_SCAN_MODE") or ""),
        "TAP_SCAN_CLOCK": strings_join(get_attribute(t, "TAP_SCAN_CLOCK") or ""),
        "TAP_SCAN_RESET": strings_join(get_attribute(t, "TAP_SCAN_RESET") or ""),
        "COMPLIANCE_PATTERNS": strings_join(get_attribute(t, "COMPLIANCE_PATTERNS") or ""),
    }
    counts = {}
    for c in boundary:
        f = (c.get('function') or 'unknown').lower()
        key = 'control' if 'control' in f else 'input' if 'input' in f else 'output' if 'output' in f else 'bidir' if 'bidir' in f else 'internal' if c.get('port') in ('*','internal') else 'other'
        counts[key] = counts.get(key, 0) + 1
    return {
        "entity": entity,
        "attributes": attrs,
        "idcode": idcode,
        "instructions": instructions,
        "register_access": parse_register_access(t),
        "ports": ports,
        "pin_map": pin_map,
        "boundary_register": boundary,
        "summary": {
            "ports": len(ports), "mapped_pins": len(pin_map), "boundary_cells": len(boundary),
            "instructions": len(instructions), "cell_counts": counts
        }
    }

@app.get("/")
def root():
    return {"ok": True, "name": "BSDL Test Station", "message": "Backend listo"}

@app.post("/upload/bsdl")
async def upload_bsdl(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".bsdl", ".bsd", ".txt")):
        return {"ok": False, "error": "Sube un archivo .bsdl, .bsd o .txt"}
    data = await file.read()
    text = data.decode("utf-8", errors="ignore")
    info = parse_bsdl(text)
    bid = str(uuid.uuid4())
    path = UPLOADS / f"{bid}_{clean_filename(file.filename)}"
    path.write_bytes(data)
    DB[bid] = {"id": bid, "filename": file.filename, "path": str(path), "info": info}
    return {"ok": True, "id": bid, "filename": file.filename, "info": info}

@app.get("/uploads")
def uploads():
    return {"items": [{"id": v["id"], "filename": v["filename"], "info": v["info"]} for v in DB.values()]}

@app.get("/bsdl/{bid}")
def get_bsdl(bid: str):
    item = DB.get(bid)
    if not item: return {"ok": False, "error": "No encontrado"}
    return {"ok": True, **item}

@app.post("/parse-text")
async def parse_text(payload: dict):
    text = payload.get("text", "")
    return {"ok": True, "info": parse_bsdl(text)}
