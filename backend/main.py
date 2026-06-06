from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
import subprocess, re, socket, threading, time, json, uuid, os
from typing import Optional

BASE = Path(__file__).parent
BSDL_DIR = BASE / "uploads" / "bsdl"
FW_DIR = BASE / "uploads" / "firmware"
REPORT_DIR = BASE / "reports"
for d in [BSDL_DIR, FW_DIR, REPORT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Universal Test Station")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# memoria simple para MVP
BSDL_DB = {}
FW_DB = {}

class RunJtagRequest(BaseModel):
    name: str
    bsdl_id: str
    openocd_command: Optional[str] = None  # opcional: openocd -f interface/... -f target/... -c "init; scan_chain; shutdown"
    do_boundary_info: bool = True
    do_idcode_check: bool = True

class RunFunctionalRequest(BaseModel):
    name: str
    firmware_id: str
    flash_command: str  # ejemplo: openocd ... -c "program {firmware} verify reset exit"
    listen_host: str = "0.0.0.0"
    listen_port: int = 9000
    expected_text: str = "OK"
    timeout_seconds: int = 30

class ValidateRequest(BaseModel):
    type: str
    config: dict


def run_cmd(command: str, timeout: int = 20):
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "code": r.returncode, "stdout": r.stdout.strip(), "stderr": r.stderr.strip(), "command": command}
    except Exception as e:
        return {"ok": False, "code": -1, "stdout": "", "stderr": str(e), "command": command}


def clean_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def parse_bsdl(text: str):
    # Parser básico pero útil. BSDL real puede variar; esto extrae lo principal.
    entity = None
    m = re.search(r"entity\s+(\w+)\s+is", text, re.I)
    if m: entity = m.group(1)

    ir_length = None
    m = re.search(r"attribute\s+INSTRUCTION_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)", text, re.I)
    if m: ir_length = int(m.group(1))

    idcode_bits = None
    m = re.search(r"attribute\s+IDCODE_REGISTER\s+of\s+\w+\s*:\s*entity\s+is\s*\n?\s*\"([01Xx\s]+)\"", text, re.I)
    if m:
        idcode_bits = re.sub(r"\s+", "", m.group(1)).replace("X", "0").replace("x", "0")
    idcode_hex = None
    if idcode_bits and set(idcode_bits) <= set("01"):
        try: idcode_hex = hex(int(idcode_bits, 2))
        except Exception: pass

    boundary_len = None
    m = re.search(r"attribute\s+BOUNDARY_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)", text, re.I)
    if m: boundary_len = int(m.group(1))

    pins = []
    # Ejemplo: "0 (BC_1, PE0, input, X),"
    for m in re.finditer(r"\"\s*(\d+)\s*\(([^\"]+)\)\s*\"", text):
        bit = int(m.group(1)); parts = [p.strip() for p in m.group(2).split(',')]
        if len(parts) >= 3:
            cell, port, function = parts[0], parts[1], parts[2]
            if port not in ["*", "internal", "CONTROL", "control"]:
                pins.append({"bit": bit, "cell": cell, "pin": port, "function": function})

    counts = {"input":0, "output":0, "bidir":0, "control":0, "other":0}
    for p in pins:
        f = p["function"].lower()
        if "input" in f: counts["input"] += 1
        elif "output" in f: counts["output"] += 1
        elif "bidir" in f: counts["bidir"] += 1
        elif "control" in f: counts["control"] += 1
        else: counts["other"] += 1

    return {"entity": entity, "ir_length": ir_length, "idcode_bits": idcode_bits, "idcode_hex": idcode_hex, "boundary_length": boundary_len, "pins": pins[:500], "pin_count": len(pins), "counts": counts}

@app.get("/")
def root():
    return {"status": "Universal Test Station backend OK"}

@app.get("/module-types")
def module_types():
    return {
        "jtag_bsdl": {"label":"JTAG Básico con BSDL", "description":"Sube un BSDL y ejecuta revisión estructural básica."},
        "functional_firmware": {"label":"Prueba funcional con firmware", "description":"Carga firmware de prueba, escucha resultado en la Pi y valida respuesta."}
    }

@app.post("/upload/bsdl")
async def upload_bsdl(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".bsdl", ".bsd", ".txt")):
        return {"ok": False, "error": "Sube un archivo .bsdl, .bsd o .txt"}
    bid = str(uuid.uuid4())
    path = BSDL_DIR / f"{bid}_{clean_filename(file.filename)}"
    data = await file.read()
    path.write_bytes(data)
    text = data.decode(errors="ignore")
    info = parse_bsdl(text)
    BSDL_DB[bid] = {"id": bid, "filename": file.filename, "path": str(path), "info": info}
    return {"ok": True, "bsdl_id": bid, "filename": file.filename, "info": info}

@app.post("/upload/firmware")
async def upload_firmware(file: UploadFile = File(...)):
    fid = str(uuid.uuid4())
    path = FW_DIR / f"{fid}_{clean_filename(file.filename)}"
    path.write_bytes(await file.read())
    FW_DB[fid] = {"id": fid, "filename": file.filename, "path": str(path)}
    return {"ok": True, "firmware_id": fid, "filename": file.filename, "path": str(path)}

@app.get("/uploads")
def uploads():
    return {"bsdl": list(BSDL_DB.values()), "firmware": list(FW_DB.values())}

@app.post("/validate")
def validate(req: ValidateRequest):
    errors=[]
    c=req.config
    if req.type == "jtag_bsdl":
        if not c.get("name"): errors.append("Falta nombre")
        if not c.get("bsdl_id"): errors.append("Falta archivo BSDL")
    elif req.type == "functional_firmware":
        for k, label in [("name","nombre"),("firmware_id","firmware"),("flash_command","comando para cargar firmware"),("expected_text","mensaje esperado")]:
            if not c.get(k): errors.append(f"Falta {label}")
    else:
        errors.append("Tipo desconocido")
    return {"ok": not errors, "errors": errors}

@app.post("/run/jtag")
def run_jtag(req: RunJtagRequest):
    item = BSDL_DB.get(req.bsdl_id)
    if not item:
        return {"ok": False, "message": "BSDL no encontrado. Sube el BSDL otra vez.", "steps": []}
    info = item["info"]
    steps = []
    # Paso 1: análisis BSDL
    bsdl_ok = bool(info.get("boundary_length") or info.get("pin_count"))
    steps.append({"name":"Analizar BSDL", "ok": bsdl_ok, "details": info})
    # Paso 2: revisión automática según pines declarados
    basic_checks = []
    for p in info.get("pins", [])[:100]:
        f = p["function"].lower()
        if "input" in f:
            action = "Se puede leer por boundary scan; revisar stuck HIGH/LOW comparando lecturas."
        elif "output" in f:
            action = "Se puede intentar forzar HIGH/LOW por boundary scan y observar cambios."
        elif "bidir" in f:
            action = "Se puede probar como salida y como entrada usando control cell si existe."
        else:
            action = "Celda no clasificada; requiere revisión manual."
        basic_checks.append({"pin": p["pin"], "bit": p["bit"], "type": p["function"], "planned_check": action})
    steps.append({"name":"Generar revisiones básicas según BSDL", "ok": len(basic_checks)>0, "details": {"checks_generated": len(basic_checks), "sample": basic_checks[:30]}})
    # Paso 3 opcional: OpenOCD real
    if req.openocd_command:
        result = run_cmd(req.openocd_command, timeout=20)
        text = (result.get("stdout","")+"\n"+result.get("stderr","")).lower()
        id_ok = True
        if req.do_idcode_check and info.get("idcode_hex"):
            id_ok = info["idcode_hex"].lower() in text or info["idcode_hex"].lower().replace("0x","") in text
        steps.append({"name":"Ejecutar herramienta JTAG", "ok": result["ok"], "details": result})
        steps.append({"name":"Comparar IDCODE del BSDL", "ok": id_ok, "details": {"expected": info.get("idcode_hex"), "note":"Si falla, puede ser formato distinto en la salida de la herramienta."}})
    else:
        steps.append({"name":"Herramienta JTAG", "ok": True, "details": {"note":"No se ejecutó OpenOCD porque no se configuró comando. Solo se analizó el BSDL y se generó plan de revisión."}})
    ok = all(s["ok"] for s in steps)
    report = {"ok": ok, "message":"JTAG/BSDL revisado" if ok else "JTAG/BSDL con advertencias", "steps": steps, "bsdl_info": info}
    (REPORT_DIR / f"jtag_{int(time.time())}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def tcp_listener(host, port, timeout, expected):
    result = {"ok": False, "received": "", "client": None, "error": "timeout"}
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.settimeout(timeout)
    try:
        srv.bind((host, port)); srv.listen(1)
        conn, addr = srv.accept()
        with conn:
            conn.settimeout(3)
            data = conn.recv(4096)
            text = data.decode(errors="ignore")
            result = {"ok": expected in text, "received": text, "client": f"{addr[0]}:{addr[1]}", "error": "" if expected in text else "mensaje esperado no encontrado"}
            conn.sendall(b"PI_RECEIVED\n")
    except Exception as e:
        result["error"] = str(e)
    finally:
        srv.close()
    return result

@app.post("/run/functional")
def run_functional(req: RunFunctionalRequest):
    item = FW_DB.get(req.firmware_id)
    if not item:
        return {"ok": False, "message": "Firmware no encontrado. Sube el firmware otra vez.", "steps": []}
    firmware_path = item["path"]
    command = req.flash_command.replace("{firmware}", firmware_path)
    listen_result = {}
    def listen_job():
        nonlocal listen_result
        listen_result = tcp_listener(req.listen_host, req.listen_port, req.timeout_seconds, req.expected_text)
    th = threading.Thread(target=listen_job, daemon=True)
    th.start()
    time.sleep(0.5)
    flash = run_cmd(command, timeout=max(10, req.timeout_seconds))
    th.join(timeout=req.timeout_seconds + 2)
    if not listen_result:
        listen_result = {"ok": False, "received":"", "client":None, "error":"No llegó conexión antes del timeout"}
    steps = [
        {"name":"Cargar firmware de prueba", "ok": flash["ok"], "details": flash},
        {"name":"Escuchar respuesta de la placa", "ok": listen_result["ok"], "details": listen_result},
    ]
    ok = all(s["ok"] for s in steps)
    report = {"ok": ok, "message":"Prueba funcional PASÓ" if ok else "Prueba funcional FALLÓ", "steps": steps}
    (REPORT_DIR / f"functional_{int(time.time())}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
