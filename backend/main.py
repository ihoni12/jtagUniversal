from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import tempfile
import os
import re
from typing import Dict, Any, List, Optional

app = FastAPI(title="BSDL JTAG Chip Tester")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def run_cmd(command: str, timeout: int = 45) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            executable="/bin/bash",
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "code": result.returncode,
            "command": command,
        }
    except Exception as e:
        return {
            "ok": False,
            "stdout": "",
            "stderr": str(e),
            "code": -1,
            "command": command,
        }

def clean_bsdl(text: str) -> str:
    return re.sub(r"--.*?$", "", text, flags=re.MULTILINE)

def opcode_to_hex(bits: str) -> str:
    bits = re.sub(r"[^01]", "", bits)
    if not bits:
        return ""
    return hex(int(bits, 2))

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

def parse_idcode(text: str) -> Dict[str, Optional[str]]:
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
    bits = re.sub(r"[^01Xx]", "", "".join(parts))
    if not bits:
        return out

    out["bits"] = bits
    bits_for_hex = bits.replace("X", "0").replace("x", "0")
    if re.fullmatch(r"[01]+", bits_for_hex):
        out["hex"] = hex(int(bits_for_hex, 2))
    return out

def parse_boundary(text: str) -> Dict[str, Any]:
    m = re.search(
        r"BOUNDARY_REGISTER\s+of\s+\w+\s*:\s*entity\s+is\s+(.*?);",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    cells = []
    pins_map = {}

    if not m:
        return {
            "length": 0,
            "cells": [],
            "pins": [],
            "counts": {"input": 0, "output": 0, "bidir": 0, "control": 0, "other": 0},
        }

    block = m.group(1)
    strings = re.findall(r'"([^"]+)"', block)
    joined = " ".join(strings)

    for bit_s, inside in re.findall(r"(\d+)\s*\(([^)]*)\)", joined):
        parts = [p.strip() for p in inside.split(",")]
        while len(parts) < 4:
            parts.append("")

        bit = int(bit_s)
        port = parts[1]
        function = parts[2].lower()

        cell = {
            "bit": bit,
            "cell": parts[0],
            "port": port,
            "function": function,
            "safe": parts[3],
            "raw": parts,
        }
        cells.append(cell)

        if not port or port in ["*", "INTERNAL", "internal"] or port.upper() in ["VCC", "GND", "NC"]:
            continue

        if port not in pins_map:
            pins_map[port] = {
                "pin": port,
                "input_bits": [],
                "output_bits": [],
                "bidir_bits": [],
                "control_bits": [],
                "functions": set(),
            }

        pins_map[port]["functions"].add(function)

        if "input" in function or "observe" in function:
            pins_map[port]["input_bits"].append(bit)
        elif "output" in function:
            pins_map[port]["output_bits"].append(bit)
        elif "bidir" in function or "inout" in function:
            pins_map[port]["bidir_bits"].append(bit)
        elif "control" in function or "enable" in function:
            pins_map[port]["control_bits"].append(bit)

    pins = []
    for _, p in pins_map.items():
        if p["bidir_bits"]:
            ptype = "bidir"
        elif p["output_bits"]:
            ptype = "output"
        elif p["input_bits"]:
            ptype = "input"
        elif p["control_bits"]:
            ptype = "control"
        else:
            ptype = "other"

        pins.append({
            "pin": p["pin"],
            "type": ptype,
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

    length = max([c["bit"] for c in cells], default=-1) + 1

    return {"length": length, "cells": cells, "pins": pins, "counts": counts}

def parse_bsdl(text: str) -> Dict[str, Any]:
    text = clean_bsdl(text)

    entity = None
    ir_length = None

    m = re.search(r"entity\s+([A-Za-z0-9_]+)\s+is", text, re.IGNORECASE)
    if m:
        entity = m.group(1)

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

    warnings = []
    if not entity:
        warnings.append("No se encontró entity en el BSDL.")
    if not ir_length:
        warnings.append("No se encontró INSTRUCTION_LENGTH.")
    if not idcode["hex"]:
        warnings.append("No se encontró IDCODE_REGISTER.")
    if not boundary["cells"]:
        warnings.append("No se encontró BOUNDARY_REGISTER.")
    if "IDCODE" not in opcodes:
        warnings.append("No se encontró instrucción IDCODE.")
    if "SAMPLE" not in opcodes and "SAMPLE_PRELOAD" not in opcodes:
        warnings.append("No se encontró instrucción SAMPLE/SAMPLE_PRELOAD.")
    if "EXTEST" not in opcodes:
        warnings.append("No se encontró instrucción EXTEST. No se puede hacer prueba de cortos manejando pines.")

    return {
        "entity": entity,
        "ir_length": ir_length,
        "idcode_bits": idcode["bits"],
        "idcode_hex": idcode["hex"],
        "opcodes": opcodes,
        "boundary_length": boundary["length"],
        "pin_count": len(boundary["pins"]),
        "pins": boundary["pins"],
        "counts": boundary["counts"],
        "warnings": warnings,
    }

def get_opcode(info: Dict[str, Any], names: List[str]) -> Optional[str]:
    ops = info.get("opcodes") or {}
    for name in names:
        if name.upper() in ops:
            return ops[name.upper()]
    return None

def make_target_cfg(info: Dict[str, Any]) -> str:
    ir_length = info.get("ir_length") or 4
    expected_id = info.get("idcode_hex") or "0x00000000"
    return f"""
transport select jtag
adapter speed 100

set _CHIPNAME auto_chip
jtag newtap $_CHIPNAME cpu -irlen {ir_length} -expected-id {expected_id}
"""

def make_safe_tcl(target_path: str, info: Dict[str, Any]) -> str:
    idcode = get_opcode(info, ["IDCODE"])
    sample = get_opcode(info, ["SAMPLE", "SAMPLE_PRELOAD"])
    boundary_len = info.get("boundary_length") or 0

    lines = [
        "source [find interface/raspberrypi-native.cfg]",
        f"source {target_path}",
        "init",
        "scan_chain",
    ]

    if idcode:
        lines.append(f"irscan auto_chip.cpu {idcode}")
        lines.append("drscan auto_chip.cpu 32 0x0")

    if sample and boundary_len:
        lines.append(f"irscan auto_chip.cpu {sample}")
        lines.append(f"drscan auto_chip.cpu {boundary_len} 0x0")
        lines.append(f"drscan auto_chip.cpu {boundary_len} 0x0")

    lines.append("shutdown")
    return "\n".join(lines) + "\n"

def vec_to_hex(bits: List[int]) -> str:
    v = 0
    for i, b in enumerate(bits):
        if b:
            v |= 1 << i
    return hex(v)

def make_extest_tcl(target_path: str, info: Dict[str, Any], max_pins: int = 80) -> str:
    extest = get_opcode(info, ["EXTEST"])
    boundary_len = info.get("boundary_length") or 0
    pins = info.get("pins") or []

    lines = [
        "source [find interface/raspberrypi-native.cfg]",
        f"source {target_path}",
        "init",
        "scan_chain",
    ]

    if not extest or not boundary_len:
        lines.append("shutdown")
        return "\n".join(lines) + "\n"

    lines.append(f"irscan auto_chip.cpu {extest}")
    lines.append(f"drscan auto_chip.cpu {boundary_len} 0x0")

    drive_pins = [p for p in pins if p.get("output_bits") or p.get("bidir_bits")][:max_pins]

    for p in drive_pins:
        bits = p.get("output_bits") or p.get("bidir_bits") or []
        if not bits:
            continue

        vec = [0] * boundary_len
        for bit in bits:
            if 0 <= bit < boundary_len:
                vec[bit] = 1

        lines.append(f"# TEST_PIN {p['pin']} HIGH")
        lines.append(f"drscan auto_chip.cpu {boundary_len} {vec_to_hex(vec)}")
        lines.append(f"drscan auto_chip.cpu {boundary_len} {vec_to_hex(vec)}")
        lines.append(f"# TEST_PIN {p['pin']} LOW")
        lines.append(f"drscan auto_chip.cpu {boundary_len} 0x0")
        lines.append(f"drscan auto_chip.cpu {boundary_len} 0x0")

    lines.append("shutdown")
    return "\n".join(lines) + "\n"

def analyze_openocd(info: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    text = (result.get("stdout", "") + "\n" + result.get("stderr", "")).lower()
    errors = []
    passed = []

    if result["ok"]:
        passed.append("OpenOCD terminó correctamente.")
    else:
        errors.append("OpenOCD terminó con error.")

    expected_id = (info.get("idcode_hex") or "").lower()
    if expected_id:
        if expected_id in text:
            passed.append(f"IDCODE esperado encontrado: {expected_id}")
        else:
            errors.append(f"No se encontró el IDCODE esperado: {expected_id}")

    if "jtag scan chain interrogation failed" in text:
        errors.append("Falló la detección de cadena JTAG.")
    if "all zeroes" in text:
        errors.append("JTAG devolvió todo ceros. Revisa alimentación, GND, TCK/TMS/TDI/TDO o velocidad.")
    if "ir capture error" in text:
        errors.append("Error de IR capture. Puede ser cableado, target incorrecto o chip sin alimentación.")
    if "tap/device found" in text or "idcode" in text:
        passed.append("Se detectó actividad JTAG.")

    return {"ok": len(errors) == 0, "passed": passed, "errors": errors}

def build_short_report(info: Dict[str, Any], safe_analysis: Dict[str, Any], extest_result: Optional[Dict[str, Any]], allow_extest: bool):
    errors = []
    warnings = []
    ok_items = []

    if info.get("warnings"):
        warnings.extend(info["warnings"])

    if safe_analysis.get("passed"):
        ok_items.extend(safe_analysis["passed"])

    if safe_analysis.get("errors"):
        errors.extend(safe_analysis["errors"])

    if allow_extest:
        if extest_result and extest_result["ok"]:
            ok_items.append("EXTEST ejecutado. Se hizo prueba básica de pines manejables.")
            warnings.append("EXTEST básico busca posibles problemas, pero con solo BSDL no confirma conexiones esperadas de placa.")
        else:
            errors.append("EXTEST falló o no es compatible.")
    else:
        warnings.append("No se ejecutó EXTEST. La revisión fue segura: IDCODE + SAMPLE.")

    final_ok = len(errors) == 0

    return {
        "ok": final_ok,
        "status": "APROBADO" if final_ok else "FALLÓ",
        "chip": info.get("entity"),
        "idcode": info.get("idcode_hex"),
        "ir_length": info.get("ir_length"),
        "boundary_length": info.get("boundary_length"),
        "pin_count": info.get("pin_count"),
        "pin_counts": info.get("counts"),
        "ok_items": ok_items,
        "warnings": warnings,
        "errors": errors,
    }

@app.get("/")
def root():
    return {"status": "BSDL JTAG Chip Tester funcionando"}

@app.post("/jtag/analyze")
async def analyze(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode(errors="ignore")
    info = parse_bsdl(text)
    return {"info": info}

@app.post("/jtag/run")
async def run_jtag(file: UploadFile = File(...), allow_extest: bool = Form(False)):
    content = await file.read()
    text = content.decode(errors="ignore")
    info = parse_bsdl(text)

    with tempfile.TemporaryDirectory() as tmp:
        target_path = os.path.join(tmp, "auto_target.cfg")
        safe_path = os.path.join(tmp, "safe_test.tcl")
        extest_path = os.path.join(tmp, "extest_test.tcl")

        with open(target_path, "w", encoding="utf-8") as f:
            f.write(make_target_cfg(info))

        with open(safe_path, "w", encoding="utf-8") as f:
            f.write(make_safe_tcl(target_path, info))

        safe_result = run_cmd(f"sudo openocd -f {safe_path}", timeout=45)
        safe_analysis = analyze_openocd(info, safe_result)

        extest_result = None
        if allow_extest:
            with open(extest_path, "w", encoding="utf-8") as f:
                f.write(make_extest_tcl(target_path, info))
            extest_result = run_cmd(f"sudo openocd -f {extest_path}", timeout=90)

    report = build_short_report(info, safe_analysis, extest_result, allow_extest)

    return {
        "report": report,
        "details": {
            "bsdl_info": info,
            "safe_openocd": safe_result,
            "safe_analysis": safe_analysis,
            "extest_openocd": extest_result,
        },
    }
