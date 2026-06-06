from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
import subprocess
import tempfile
import socket
import time
import uuid
import re
import os
from typing import Dict, Any, List, Optional

BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
BSDL_DIR = UPLOADS / "bsdl"
FW_DIR = UPLOADS / "firmware"
REPORT_DIR = BASE / "reports"

for d in [BSDL_DIR, FW_DIR, REPORT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="JTAG Universal Test Station")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BSDL_DB: Dict[str, Dict[str, Any]] = {}
FW_DB: Dict[str, Dict[str, Any]] = {}


class RunFunctionalRequest(BaseModel):
    name: str
    firmware_id: str
    flash_command: str
    listen_host: str = "0.0.0.0"
    listen_port: int = 9000
    expected_text: str = "OK"
    timeout_seconds: int = 30


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def run_cmd(command: str, timeout: int = 30) -> Dict[str, Any]:
    try:
        r = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            executable="/bin/bash",
        )
        return {
            "ok": r.returncode == 0,
            "code": r.returncode,
            "stdout": r.stdout,
            "stderr": r.stderr,
            "command": command,
        }
    except Exception as e:
        return {
            "ok": False,
            "code": -1,
            "stdout": "",
            "stderr": str(e),
            "command": command,
        }


def clean_bsdl(text: str) -> str:
    return re.sub(r"--.*?$", "", text, flags=re.MULTILINE)


def opcode_to_hex(bits: str) -> str:
    bits = re.sub(r"[^01]", "", bits)
    return hex(int(bits, 2)) if bits else ""


def parse_opcodes(text: str) -> Dict[str, str]:
    opcodes = {}
    m = re.search(
        r"INSTRUCTION_OPCODE\s+of\s+\w+\s*:\s*entity\s+is\s+(.*?);",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return opcodes

    block = m.group(1)
    for name, bits in re.findall(r"([A-Za-z0-9_]+)\s*\(\s*([01]+)\s*\)", block):
        opcodes[name.upper()] = opcode_to_hex(bits)

    return opcodes


def parse_idcode(text: str) -> Dict[str, Any]:
    out = {"bits": None, "hex": None}

    m = re.search(
        r"IDCODE_REGISTER\s+of\s+\w+\s*:\s*entity\s+is\s+(.*?);",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return out

    block = m.group(1)
    parts = re.findall(r'"([^"]+)"', block)
    joined = "".join(parts)
    joined = re.sub(r"\s+", "", joined)
    if not joined:
        return out

    out["bits"] = joined
    bits01 = joined.replace("X", "0").replace("x", "0")
    bits01 = re.sub(r"[^01]", "", bits01)
    if bits01:
        out["hex"] = hex(int(bits01, 2))

    return out


def parse_pin_map(text: str) -> Dict[str, str]:
    pin_map = {}
    m = re.search(
        r"PIN_MAP_STRING\s+of\s+\w+\s*:\s*entity\s+is\s+(.*?);",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return pin_map

    joined = "".join(re.findall(r'"([^"]+)"', m.group(1)))
    for name, pin in re.findall(r"([A-Za-z0-9_]+)\s*:\s*([A-Za-z0-9_.-]+)", joined):
        pin_map[name] = pin
    return pin_map


def parse_boundary(text: str) -> Dict[str, Any]:
    # Busca bloque BOUNDARY_REGISTER. Muchos BSDL reales usan strings concatenados con &
    m = re.search(
        r"BOUNDARY_REGISTER\s+of\s+\w+\s*:\s*entity\s+is\s+(.*?);",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        # a veces aparece attribute BOUNDARY_LENGTH pero no register completo
        lm = re.search(
            r"BOUNDARY_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)",
            text,
            re.IGNORECASE,
        )
        length = int(lm.group(1)) if lm else 0
        return {"length": length, "cells": [], "pins": [], "counts": {}}

    block = m.group(1)
    strings = re.findall(r'"([^"]+)"', block)
    joined = " ".join(strings)

    matches = re.findall(r"(\d+)\s*\(([^)]*)\)", joined)
    cells = []

    for bit_s, inside in matches:
        parts = [p.strip() for p in inside.split(",")]
        while len(parts) < 4:
            parts.append("")
        bit = int(bit_s)
        function = parts[2].strip().lower()
        port = parts[1].strip()

        cell = {
            "bit": bit,
            "cell": parts[0].strip(),
            "port": port,
            "function": function,
            "safe": parts[3].strip(),
            "control_bit": None,
            "disable_value": None,
            "disable_result": None,
            "raw": parts,
        }

        if len(parts) >= 7:
            try:
                cell["control_bit"] = int(parts[4])
            except Exception:
                cell["control_bit"] = None
            cell["disable_value"] = parts[5].strip()
            cell["disable_result"] = parts[6].strip()

        cells.append(cell)

    length = max([c["bit"] for c in cells], default=-1) + 1

    pins_map: Dict[str, Dict[str, Any]] = {}
    for c in cells:
        port = c["port"]
        if not port or port in ["*", "internal", "INTERNAL"]:
            continue
        if port.upper() in ["VCC", "GND", "NC", "N/C"]:
            continue

        if port not in pins_map:
            pins_map[port] = {
                "pin": port,
                "cells": [],
                "input_bits": [],
                "output_bits": [],
                "bidir_bits": [],
                "control_bits": [],
                "functions": set(),
            }

        pins_map[port]["cells"].append(c)
        pins_map[port]["functions"].add(c["function"])
        f = c["function"]

        if "input" in f or "observe" in f:
            pins_map[port]["input_bits"].append(c["bit"])
        if "output" in f:
            pins_map[port]["output_bits"].append(c["bit"])
        if "bidir" in f or "inout" in f:
            pins_map[port]["bidir_bits"].append(c["bit"])
        if "control" in f or "enable" in f:
            pins_map[port]["control_bits"].append(c["bit"])

    pins = []
    for pin, p in pins_map.items():
        if p["bidir_bits"]:
            typ = "bidir"
        elif p["output_bits"]:
            typ = "output"
        elif p["input_bits"]:
            typ = "input"
        elif p["control_bits"]:
            typ = "control"
        else:
            typ = "other"

        pins.append({
            "pin": pin,
            "type": typ,
            "input_bits": p["input_bits"],
            "output_bits": p["output_bits"],
            "bidir_bits": p["bidir_bits"],
            "control_bits": p["control_bits"],
            "functions": sorted(list(p["functions"])),
        })

    counts = {
        "input": len([p for p in pins if p["type"] == "input"]),
        "output": len([p for p in pins if p["type"] == "output"]),
        "bidir": len([p for p in pins if p["type"] == "bidir"]),
        "control": len([p for p in pins if p["type"] == "control"]),
        "other": len([p for p in pins if p["type"] == "other"]),
    }

    return {"length": length, "cells": cells, "pins": pins, "counts": counts}


def parse_bsdl(text: str) -> Dict[str, Any]:
    text = clean_bsdl(text)

    entity = None
    m = re.search(r"entity\s+([A-Za-z0-9_]+)\s+is", text, re.IGNORECASE)
    if m:
        entity = m.group(1)

    ir_length = None
    m = re.search(
        r"INSTRUCTION_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)",
        text,
        re.IGNORECASE,
    )
    if m:
        ir_length = int(m.group(1))

    opcodes = parse_opcodes(text)
    idcode = parse_idcode(text)
    boundary = parse_boundary(text)
    pin_map = parse_pin_map(text)

    warnings = []
    if not entity:
        warnings.append("No se detectó entity")
    if not ir_length:
        warnings.append("No se detectó INSTRUCTION_LENGTH")
    if not idcode.get("hex"):
        warnings.append("No se detectó IDCODE_REGISTER")
    if not boundary.get("cells"):
        warnings.append("No se detectó BOUNDARY_REGISTER completo")
    if "IDCODE" not in opcodes:
        warnings.append("No se detectó opcode IDCODE")
    if "SAMPLE" not in opcodes and "SAMPLE_PRELOAD" not in opcodes:
        warnings.append("No se detectó opcode SAMPLE/SAMPLE_PRELOAD")
    if "EXTEST" not in opcodes:
        warnings.append("No se detectó opcode EXTEST")

    return {
        "entity": entity,
        "ir_length": ir_length,
        "idcode_bits": idcode.get("bits"),
        "idcode_hex": idcode.get("hex"),
        "opcodes": opcodes,
        "boundary_length": boundary.get("length", 0),
        "boundary_cell_count": len(boundary.get("cells", [])),
        "pins": boundary.get("pins", []),
        "pin_count": len(boundary.get("pins", [])),
        "counts": boundary.get("counts", {}),
        "pin_map": pin_map,
        "warnings": warnings,
    }


def create_target_cfg(info: Dict[str, Any]) -> str:
    ir_length = info.get("ir_length") or 4
    expected_id = info.get("idcode_hex") or "0x00000000"

    return f"""
# Generated automatically by JTAG Universal Test Station
transport select jtag
adapter speed 100
set _CHIPNAME auto_chip
jtag newtap $_CHIPNAME cpu -irlen {ir_length} -expected-id {expected_id}
"""


def get_opcode(info: Dict[str, Any], names: List[str]) -> Optional[str]:
    ops = info.get("opcodes") or {}
    for name in names:
        if name.upper() in ops:
            return ops[name.upper()]
    return None


def openocd_script_basic(info: Dict[str, Any], target_path: str) -> str:
    idcode_op = get_opcode(info, ["IDCODE"])
    sample_op = get_opcode(info, ["SAMPLE", "SAMPLE_PRELOAD"])
    boundary_len = info.get("boundary_length") or 0

    lines = [
        "source [find interface/raspberrypi-native.cfg]",
        f"source {target_path}",
        "init",
        "scan_chain",
    ]

    if idcode_op:
        lines.append(f"irscan auto_chip.cpu {idcode_op}")
        lines.append("drscan auto_chip.cpu 32 0x0")

    if sample_op and boundary_len:
        lines.append(f"irscan auto_chip.cpu {sample_op}")
        lines.append(f"drscan auto_chip.cpu {boundary_len} 0x0")
        lines.append(f"drscan auto_chip.cpu {boundary_len} 0x0")

    lines.append("shutdown")
    return "\n".join(lines) + "\n"


def bits_to_hex_value(bits: List[int]) -> str:
    value = 0
    for i, bit in enumerate(bits):
        if bit:
            value |= (1 << i)
    return hex(value)


def openocd_script_extest(info: Dict[str, Any], target_path: str, max_pins: int = 64) -> str:
    extest_op = get_opcode(info, ["EXTEST"])
    boundary_len = info.get("boundary_length") or 0
    pins = info.get("pins") or []

    lines = [
        "source [find interface/raspberrypi-native.cfg]",
        f"source {target_path}",
        "init",
        "scan_chain",
    ]

    if not extest_op or not boundary_len:
        lines.append("shutdown")
        return "\n".join(lines) + "\n"

    lines.append(f"irscan auto_chip.cpu {extest_op}")

    drive_pins = [p for p in pins if p.get("output_bits") or p.get("bidir_bits")][:max_pins]

    # baseline all zero
    lines.append(f"drscan auto_chip.cpu {boundary_len} 0x0")

    for p in drive_pins:
        drive_bits = p.get("output_bits") or p.get("bidir_bits") or []
        vec = [0] * boundary_len
        for b in drive_bits:
            if 0 <= b < boundary_len:
                vec[b] = 1
        lines.append(f"# TEST_PIN {p['pin']}")
        lines.append(f"drscan auto_chip.cpu {boundary_len} {bits_to_hex_value(vec)}")
        lines.append(f"drscan auto_chip.cpu {boundary_len} {bits_to_hex_value(vec)}")
        lines.append(f"drscan auto_chip.cpu {boundary_len} 0x0")

    lines.append("shutdown")
    return "\n".join(lines) + "\n"


def build_test_plan(info: Dict[str, Any]) -> Dict[str, Any]:
    tests = [
        {
            "id": "scan_chain",
            "name": "Detectar cadena JTAG",
            "type": "non_invasive",
            "description": "OpenOCD init + scan_chain",
        },
        {
            "id": "idcode",
            "name": "Verificar IDCODE",
            "type": "non_invasive",
            "expected": info.get("idcode_hex"),
        },
        {
            "id": "sample",
            "name": "Leer Boundary Register",
            "type": "non_invasive",
            "boundary_length": info.get("boundary_length"),
        },
    ]

    for p in info.get("pins", []):
        tests.append({
            "id": f"pin_{p['pin']}",
            "name": f"Pin {p['pin']}",
            "type": p["type"],
            "input_bits": p.get("input_bits", []),
            "output_bits": p.get("output_bits", []),
            "bidir_bits": p.get("bidir_bits", []),
            "control_bits": p.get("control_bits", []),
            "description": "Prueba generada desde el BSDL",
        })

    tests.append({
        "id": "possible_shorts",
        "name": "Posibles cortos con EXTEST",
        "type": "optional_drive",
        "description": "Puede manejar pines. Úsalo solo si la placa lo permite.",
    })

    return {
        "total_tests": len(tests),
        "tests": tests,
        "limitations": [
            "Con BSDL solamente no se sabe qué pines deben estar conectados en la placa.",
            "Para revisar conexiones esperadas, pistas cortadas y redes correctas hace falta Netlist.",
            "EXTEST puede manejar pines físicos; usar con cuidado.",
        ],
    }


def analyze_openocd_output(info: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    text = (result.get("stdout", "") + "\n" + result.get("stderr", ""))
    low = text.lower()
    findings = []
    ok = result["ok"]

    expected = (info.get("idcode_hex") or "").lower()
    if expected:
        expected_no_0x = expected.replace("0x", "")
        found = expected in low or expected_no_0x in low
        if found:
            findings.append({"level": "ok", "message": f"IDCODE esperado encontrado: {expected}"})
        else:
            findings.append({"level": "error", "message": f"IDCODE esperado no encontrado: {expected}"})
            ok = False

    if "jtag scan chain interrogation failed" in low:
        findings.append({"level": "error", "message": "Falló interrogación de cadena JTAG"})
        ok = False
    if "all zeroes" in low:
        findings.append({"level": "error", "message": "Respuesta todo ceros: revisar alimentación, GND, TDO/TDI/TCK/TMS o velocidad"})
        ok = False
    if "ir capture error" in low:
        findings.append({"level": "error", "message": "IR capture error: posible cableado, target incorrecto o chip sin alimentación"})
        ok = False
    if "tap/device found" in low or "idcode" in low:
        findings.append({"level": "ok", "message": "OpenOCD detectó actividad JTAG"})

    return {"ok": ok, "findings": findings}


def save_upload(file_data: bytes, directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4()}_{safe_filename(filename)}"
    path.write_bytes(file_data)
    return path


def rebuild_upload_db():
    # para no perder uploads después de reiniciar backend, reconstruye listado básico
    for p in BSDL_DIR.glob("*"):
        if p.is_file():
            try:
                text = p.read_text(errors="ignore")
                info = parse_bsdl(text)
                bid = p.name.split("_", 1)[0]
                BSDL_DB[bid] = {"id": bid, "filename": p.name.split("_", 1)[-1], "path": str(p), "info": info}
            except Exception:
                pass
    for p in FW_DIR.glob("*"):
        if p.is_file():
            fid = p.name.split("_", 1)[0]
            FW_DB[fid] = {"id": fid, "filename": p.name.split("_", 1)[-1], "path": str(p)}


rebuild_upload_db()


@app.get("/")
def root():
    return {
        "status": "Universal Test Station backend OK",
        "features": [
            "BSDL parser",
            "JTAG scan_chain",
            "IDCODE check",
            "SAMPLE boundary read",
            "optional EXTEST short scan",
            "functional firmware upload/listen",
        ],
    }


@app.get("/uploads")
def uploads():
    return {"bsdl": list(BSDL_DB.values()), "firmware": list(FW_DB.values())}


@app.post("/upload/bsdl")
async def upload_bsdl(file: UploadFile = File(...)):
    data = await file.read()
    if not file.filename.lower().endswith((".bsdl", ".bsd", ".txt")):
        return {"ok": False, "error": "Sube un archivo .bsdl, .bsd o .txt"}

    path = save_upload(data, BSDL_DIR, file.filename)
    bid = path.name.split("_", 1)[0]
    text = data.decode(errors="ignore")
    info = parse_bsdl(text)
    item = {"id": bid, "filename": file.filename, "path": str(path), "info": info}
    BSDL_DB[bid] = item
    return {"ok": True, "bsdl_id": bid, "filename": file.filename, "info": info, "auto_test_plan": build_test_plan(info)}


@app.post("/jtag/analyze-bsdl")
async def analyze_bsdl(file: UploadFile = File(...)):
    data = await file.read()
    text = data.decode(errors="ignore")
    info = parse_bsdl(text)
    return {"ok": True, "info": info, "auto_test_plan": build_test_plan(info)}


@app.post("/jtag/run-complete")
async def run_complete_jtag(
    file: UploadFile = File(...),
    allow_drive: bool = Form(False),
):
    data = await file.read()
    text = data.decode(errors="ignore")
    info = parse_bsdl(text)
    plan = build_test_plan(info)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target_path = tmp_path / "auto_target.cfg"
        basic_path = tmp_path / "basic_test.tcl"
        extest_path = tmp_path / "extest_test.tcl"

        target_path.write_text(create_target_cfg(info))
        basic_path.write_text(openocd_script_basic(info, str(target_path)))

        basic_result = run_cmd(f"sudo openocd -f {basic_path}", timeout=40)
        basic_analysis = analyze_openocd_output(info, basic_result)

        extest_result = None
        extest_status = {
            "executed": False,
            "ok": None,
            "message": "No ejecutado. allow_drive=false para no manejar pines.",
        }

        if allow_drive:
            extest_path.write_text(openocd_script_extest(info, str(target_path)))
            extest_result = run_cmd(f"sudo openocd -f {extest_path}", timeout=90)
            extest_status = {
                "executed": True,
                "ok": extest_result["ok"],
                "message": "EXTEST ejecutado" if extest_result["ok"] else "EXTEST falló o no compatible",
            }

    ok = bool(basic_analysis["ok"] and (extest_status["ok"] is not False))

    return {
        "ok": ok,
        "message": "Revisión JTAG completada" if ok else "Revisión JTAG con errores",
        "bsdl_info": info,
        "auto_test_plan": plan,
        "summary": {
            "entity": info.get("entity"),
            "idcode": info.get("idcode_hex"),
            "ir_length": info.get("ir_length"),
            "boundary_length": info.get("boundary_length"),
            "pin_count": info.get("pin_count"),
            "pin_counts": info.get("counts"),
            "warnings": info.get("warnings"),
        },
        "steps": [
            {"name": "Analizar BSDL", "ok": len(info.get("warnings", [])) == 0, "details": info},
            {"name": "Generar plan automático", "ok": True, "details": plan},
            {"name": "JTAG básico: scan_chain, IDCODE, SAMPLE", "ok": basic_result["ok"], "details": basic_result, "analysis": basic_analysis},
            {"name": "Posibles cortos con EXTEST", "ok": extest_status["ok"], "details": extest_status, "raw": extest_result},
        ],
        "important_note": "Con solo BSDL no se sabe qué pines deben estar conectados entre sí. Para eso agrega Netlist en una versión futura.",
    }


@app.post("/run/jtag")
async def run_jtag_from_uploaded(
    bsdl_id: str = Form(...),
    allow_drive: bool = Form(False),
):
    item = BSDL_DB.get(bsdl_id)
    if not item:
        return {"ok": False, "message": "BSDL no encontrado. Súbelo otra vez.", "steps": []}

    path = Path(item["path"])
    if not path.exists():
        return {"ok": False, "message": "Archivo BSDL no existe en disco.", "steps": []}

    class FakeUpload:
        filename = item["filename"]
        async def read(self):
            return path.read_bytes()

    return await run_complete_jtag(FakeUpload(), allow_drive)


@app.post("/upload/firmware")
async def upload_firmware(file: UploadFile = File(...)):
    data = await file.read()
    path = save_upload(data, FW_DIR, file.filename)
    fid = path.name.split("_", 1)[0]
    item = {"id": fid, "filename": file.filename, "path": str(path)}
    FW_DB[fid] = item
    return {"ok": True, "firmware_id": fid, "filename": file.filename, "path": str(path)}


@app.post("/run/functional")
def run_functional(req: RunFunctionalRequest):
    item = FW_DB.get(req.firmware_id)
    if not item:
        return {"ok": False, "message": "Firmware no encontrado. Súbelo otra vez.", "steps": []}

    fw_path = item["path"]
    command = req.flash_command.replace("{firmware}", fw_path)

    # 1. Programar firmware
    flash_result = run_cmd(command, timeout=120)

    # 2. Escuchar respuesta de la placa
    listen_result = listen_tcp(
        host=req.listen_host,
        port=req.listen_port,
        expected=req.expected_text,
        timeout_seconds=req.timeout_seconds,
    )

    ok = flash_result["ok"] and listen_result["ok"]

    return {
        "ok": ok,
        "message": "Prueba funcional OK" if ok else "Prueba funcional falló",
        "steps": [
            {"name": "Cargar firmware de prueba", "ok": flash_result["ok"], "details": flash_result},
            {"name": "Escuchar respuesta de la placa", "ok": listen_result["ok"], "details": listen_result},
        ],
    }


def listen_tcp(host: str, port: int, expected: str, timeout_seconds: int) -> Dict[str, Any]:
    start = time.time()
    received = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, int(port)))
            s.listen(1)
            s.settimeout(timeout_seconds)
            conn, addr = s.accept()
            with conn:
                conn.settimeout(5)
                data = conn.recv(8192)
                received = data.decode(errors="ignore")
                ok = expected in received
                return {
                    "ok": ok,
                    "message": "Mensaje esperado recibido" if ok else "Conexión recibida, pero mensaje incorrecto",
                    "addr": str(addr),
                    "expected": expected,
                    "received": received,
                    "seconds": round(time.time() - start, 2),
                }
    except Exception as e:
        return {
            "ok": False,
            "message": "No se recibió respuesta de la placa",
            "error": str(e),
            "expected": expected,
            "received": received,
            "seconds": round(time.time() - start, 2),
        }


@app.post("/functional/listen")
def listen_only(port: int = Form(9000), expected_text: str = Form("OK"), timeout_seconds: int = Form(30)):
    return listen_tcp("0.0.0.0", port, expected_text, timeout_seconds)
