#!/usr/bin/env bash
set -e
PROJECT="$HOME/jtagUniversal"
if [ ! -d "$PROJECT" ]; then
  echo "No existe $PROJECT"
  exit 1
fi
mkdir -p "$PROJECT/backend" "$PROJECT/frontend/src"
cat > "$PROJECT/backend/main.py" <<'PY'
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import subprocess, tempfile, os, re, json, socket, time

app = FastAPI(title="JTAG Universal Test Station")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

PI_JTAG_INTERFACE = "interface/raspberrypi-native.cfg"
DEFAULT_ADAPTER_SPEED = 100

def run_cmd(command, timeout=25):
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr, "code": r.returncode, "command": command}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e), "code": -1, "command": command}

def clean_bits(s):
    return re.sub(r"[^01]", "", s or "")

def parse_bsdl(text):
    info = {"entity": None, "ir_length": None, "idcode_bits": None, "idcode_hex": None, "boundary_length": 0, "pins": [], "counts": {"input":0,"output":0,"bidir":0,"control":0,"other":0}}
    m = re.search(r"entity\s+([A-Za-z0-9_]+)\s+is", text, re.I)
    if m: info["entity"] = m.group(1)
    m = re.search(r"INSTRUCTION_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)", text, re.I)
    if m: info["ir_length"] = int(m.group(1))
    m = re.search(r"IDCODE_REGISTER\s+of\s+\w+\s*:\s*entity\s+is\s*(.*?);", text, re.I|re.S)
    if m:
        bits = clean_bits(m.group(1))
        if bits:
            info["idcode_bits"] = bits
            info["idcode_hex"] = hex(int(bits, 2))
    # Extrae líneas tipo: "  12 (BC_1, PE0, input, X),"
    matches = re.findall(r"(?:^|\")\s*(\d+)\s*\(\s*([^,]+)\s*,\s*([^,\)]+)\s*,\s*([^,\)]+)", text, re.I|re.M)
    seen = set()
    max_bit = -1
    pins = []
    for bit_s, cell, port, func in matches:
        bit = int(bit_s); max_bit = max(max_bit, bit)
        port = port.strip().strip('"')
        func_raw = func.strip().lower()
        if port in seen: pass
        if port.upper() in ["*", "INTERNAL", "VCC", "GND", "NC"]: continue
        ptype = "other"
        if "bidir" in func_raw or "inout" in func_raw: ptype = "bidir"
        elif "output" in func_raw: ptype = "output"
        elif "input" in func_raw: ptype = "input"
        elif "control" in func_raw or "controlr" in func_raw: ptype = "control"
        pins.append({"bit": bit, "pin": port, "type": ptype, "cell": cell.strip(), "raw": func.strip()})
    info["pins"] = sorted(pins, key=lambda x: x["bit"])
    info["pin_count"] = len(pins)
    info["boundary_length"] = max_bit + 1 if max_bit >= 0 else 0
    for p in pins:
        info["counts"][p["type"]] = info["counts"].get(p["type"],0) + 1
    return info

def make_openocd_cfg(info):
    ir = info.get("ir_length") or 4
    idh = info.get("idcode_hex") or "0x00000000"
    expected = f" -expected-id {idh}" if idh and idh != "0x0" else ""
    return f"""
# Auto generado por JTAG Universal
transport select jtag
adapter speed {DEFAULT_ADAPTER_SPEED}
set _CHIPNAME auto_chip
jtag newtap $_CHIPNAME cpu -irlen {ir}{expected}
""".strip() + "\n"

def make_test_plan(info):
    pins = info.get("pins", [])
    inputs = [p for p in pins if p["type"] == "input"]
    outputs = [p for p in pins if p["type"] == "output"]
    bidirs = [p for p in pins if p["type"] == "bidir"]
    return [
        {"name":"Detectar cadena JTAG", "type":"jtag_chain", "description":"OpenOCD debe detectar el TAP y el scan chain."},
        {"name":"Verificar IDCODE", "type":"idcode", "description":"Compara el IDCODE real con el IDCODE del BSDL."},
        {"name":"Verificar Boundary Register", "type":"boundary", "description":f"Verifica que hay {info.get('boundary_length',0)} bits de boundary scan."},
        {"name":"Revisar pines de entrada", "type":"inputs", "description":f"Detectados {len(inputs)} pines input. Se revisan stuck HIGH/LOW cuando se pueda leer el boundary register."},
        {"name":"Revisar pines de salida", "type":"outputs", "description":f"Detectados {len(outputs)} pines output. Se preparan pruebas HIGH/LOW según BSDL."},
        {"name":"Revisar pines bidireccionales", "type":"bidirs", "description":f"Detectados {len(bidirs)} pines bidir. Se preparan pruebas entrada/salida."},
        {"name":"Buscar cortos básicos", "type":"shorts", "description":"Activa pines controlables uno por uno y observa cambios anormales en otros pines. Requiere boundary cells controlables."},
    ]

def analyze_openocd_text(text, info):
    low = text.lower()
    idh = (info.get("idcode_hex") or "").lower()
    chain_ok = ("jtag tap" in low or "tap/device" in low or "scan chain" in low) and ("error:" not in low)
    id_ok = True if not idh or idh == "0x0" else idh in low
    return chain_ok, id_ok

@app.get("/")
def root():
    return {"status":"JTAG Universal funcionando"}

@app.post("/jtag/analyze-bsdl")
async def analyze_bsdl(file: UploadFile = File(...)):
    text = (await file.read()).decode(errors="ignore")
    info = parse_bsdl(text)
    return {"info": info, "generated_tests": make_test_plan(info)}

@app.post("/jtag/run-auto")
async def run_auto(file: UploadFile = File(...)):
    text = (await file.read()).decode(errors="ignore")
    info = parse_bsdl(text)
    cfg = make_openocd_cfg(info)
    plan = make_test_plan(info)
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = os.path.join(tmp, "auto_target.cfg")
        with open(cfg_path, "w") as f: f.write(cfg)
        cmd = f"sudo openocd -f {PI_JTAG_INTERFACE} -f {cfg_path} -c 'init; scan_chain; shutdown'"
        result = run_cmd(cmd, timeout=25)
    combined = (result.get("stdout","") + "\n" + result.get("stderr","")).strip()
    chain_ok, id_ok = analyze_openocd_text(combined, info)
    steps = [
        {"name":"Leer y analizar BSDL", "ok": bool(info.get("ir_length")) and info.get("boundary_length",0) > 0, "details": info},
        {"name":"Generar pruebas automáticas según BSDL", "ok": True, "details": plan},
        {"name":"Crear configuración OpenOCD automática", "ok": True, "details": cfg},
        {"name":"Detectar cadena JTAG con OpenOCD", "ok": chain_ok, "details": result},
        {"name":"Verificar IDCODE del BSDL", "ok": id_ok, "details": {"expected": info.get("idcode_hex"), "found_in_openocd_output": id_ok}},
        {"name":"Plan de revisión de pines", "ok": True, "details": {"inputs": info["counts"].get("input",0), "outputs": info["counts"].get("output",0), "bidirs": info["counts"].get("bidir",0), "note":"La prueba real HIGH/LOW por pin requiere agregar instrucciones SAMPLE/PRELOAD/EXTEST específicas del BSDL. Este backend ya genera el plan y valida JTAG/IDCODE."}},
    ]
    ok = steps[0]["ok"] and steps[3]["ok"] and steps[4]["ok"]
    return {"ok": ok, "message": "JTAG automático OK" if ok else "JTAG automático falló o quedó parcial", "bsdl_info": info, "generated_tests": plan, "steps": steps}

@app.post("/functional/flash")
async def flash_firmware(firmware: UploadFile = File(...), flash_command: str = Form(...)):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, firmware.filename)
        with open(path, "wb") as f: f.write(await firmware.read())
        cmd = flash_command.replace("{firmware}", path)
        result = run_cmd(cmd, timeout=90)
    return {"ok": result["ok"], "message": "Firmware cargado" if result["ok"] else "Falló carga de firmware", "result": result}

@app.post("/functional/listen")
def listen_for_result(expected_message: str = Form("OK"), port: int = Form(9000), timeout_seconds: int = Form(30)):
    start = time.time(); data = ""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(timeout_seconds)
    try:
        s.bind(("0.0.0.0", port)); s.listen(1)
        conn, addr = s.accept(); conn.settimeout(5)
        with conn:
            data = conn.recv(4096).decode(errors="ignore")
        ok = expected_message in data
        return {"ok": ok, "message": "Mensaje esperado recibido" if ok else "Mensaje recibido pero no coincide", "from": addr[0], "received": data, "expected": expected_message, "elapsed": round(time.time()-start,2)}
    except Exception as e:
        return {"ok": False, "message": "No se recibió respuesta funcional", "error": str(e), "expected": expected_message}
    finally:
        s.close()
PY

cat > "$PROJECT/frontend/src/App.jsx" <<'JS'
import { useState } from 'react';
import './App.css';

const API = `http://${window.location.hostname}:8000`;

export default function App(){
  const [bsdl,setBsdl]=useState(null);
  const [analysis,setAnalysis]=useState(null);
  const [run,setRun]=useState(null);
  const [loading,setLoading]=useState(false);
  const [fw,setFw]=useState(null);
  const [flashCommand,setFlashCommand]=useState("sudo openocd -f interface/raspberrypi-native.cfg -f target/CHIP.cfg -c \"program {firmware} verify reset exit\"");
  const [listen,setListen]=useState(null);

  async function postFile(url,file){
    const fd=new FormData(); fd.append('file',file);
    const r=await fetch(url,{method:'POST',body:fd}); return await r.json();
  }
  async function analyze(){ if(!bsdl)return; setLoading(true); setAnalysis(await postFile(`${API}/jtag/analyze-bsdl`,bsdl)); setLoading(false); }
  async function runAuto(){ if(!bsdl)return; setLoading(true); setRun(await postFile(`${API}/jtag/run-auto`,bsdl)); setLoading(false); }
  async function flash(){ if(!fw)return; setLoading(true); const fd=new FormData(); fd.append('firmware',fw); fd.append('flash_command',flashCommand); const r=await fetch(`${API}/functional/flash`,{method:'POST',body:fd}); setListen(await r.json()); setLoading(false); }
  async function listenResult(){ setLoading(true); const fd=new FormData(); fd.append('expected_message','OK'); fd.append('port','9000'); fd.append('timeout_seconds','30'); const r=await fetch(`${API}/functional/listen`,{method:'POST',body:fd}); setListen(await r.json()); setLoading(false); }
  const info=analysis?.info || run?.bsdl_info;
  return <div className="page">
    <h1>JTAG Universal Test Station</h1>
    <p>El usuario sube BSDL. El sistema analiza, genera pruebas, crea OpenOCD temporal y ejecuta revisión básica.</p>
    <div className="card">
      <h2>1) JTAG automático con BSDL</h2>
      <input type="file" accept=".bsdl,.bsd,.txt" onChange={e=>setBsdl(e.target.files[0])}/>
      <div className="actions"><button disabled={!bsdl||loading} onClick={analyze}>Analizar BSDL</button><button disabled={!bsdl||loading} onClick={runAuto}>Ejecutar JTAG automático</button></div>
      {loading && <div className="warn">Trabajando...</div>}
      {info && <div className="grid">
        <div><b>Chip</b><br/>{info.entity || 'No detectado'}</div>
        <div><b>IR Length</b><br/>{String(info.ir_length)}</div>
        <div><b>IDCODE</b><br/>{info.idcode_hex || 'No detectado'}</div>
        <div><b>Boundary</b><br/>{info.boundary_length} bits</div>
        <div><b>Pines</b><br/>{info.pin_count}</div>
        <div><b>Input/Output/Bidir</b><br/>{info.counts.input}/{info.counts.output}/{info.counts.bidir}</div>
      </div>}
      {analysis?.generated_tests && <Result title="Pruebas generadas" data={analysis.generated_tests}/>} 
      {run && <RunResult data={run}/>} 
      {info?.pins?.length>0 && <Pins pins={info.pins}/>} 
    </div>
    <div className="card">
      <h2>2) Pruebas funcionales con firmware</h2>
      <p>Para Wi‑Fi/Ethernet/Bluetooth: sube firmware de prueba y escribe el comando que lo carga. Usa <code>{'{firmware}'}</code> donde va el archivo.</p>
      <input type="file" onChange={e=>setFw(e.target.files[0])}/>
      <textarea value={flashCommand} onChange={e=>setFlashCommand(e.target.value)} />
      <div className="actions"><button disabled={!fw||loading} onClick={flash}>Cargar firmware</button><button disabled={loading} onClick={listenResult}>Escuchar respuesta TCP :9000 OK</button></div>
      {listen && <RunResult data={listen}/>} 
    </div>
  </div>
}
function Result({title,data}){return <div className="result"><h3>{title}</h3><pre>{JSON.stringify(data,null,2)}</pre></div>}
function RunResult({data}){return <div className={data.ok?'ok':'bad'}><h3>{data.message || (data.ok?'OK':'Falló')}</h3><pre>{JSON.stringify(data,null,2)}</pre></div>}
function Pins({pins}){return <div><h3>Pines detectados</h3><div className="table"><div className="row head"><span>Bit</span><span>Pin</span><span>Tipo</span><span>Raw</span></div>{pins.slice(0,200).map((p,i)=><div className="row" key={i}><span>{p.bit}</span><span>{p.pin}</span><span>{p.type}</span><span>{p.raw}</span></div>)}</div></div>}
JS

cat > "$PROJECT/frontend/src/App.css" <<'CSS'
body{margin:0;background:#eef2f7;font-family:Arial,sans-serif;color:#172033}.page{max-width:1100px;margin:auto;padding:24px}.card{background:white;border-radius:16px;padding:18px;margin:16px 0;box-shadow:0 5px 20px #0001}button{border:0;background:#2563eb;color:white;border-radius:10px;padding:10px 14px;font-weight:700;cursor:pointer}button:disabled{background:#9ca3af}.actions{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}.grid>div{background:#f8fafc;border-radius:12px;padding:12px}.ok{background:#dcfce7;border-radius:12px;padding:12px;margin-top:12px}.bad{background:#fee2e2;border-radius:12px;padding:12px;margin-top:12px}.warn{background:#fef9c3;border-radius:12px;padding:12px;margin:12px 0}pre{background:#0f172a;color:#e5e7eb;padding:12px;border-radius:10px;white-space:pre-wrap;max-height:420px;overflow:auto}textarea{width:100%;height:90px;margin-top:10px;border-radius:10px;border:1px solid #cbd5e1;padding:10px}.table{border:1px solid #e5e7eb;border-radius:10px;overflow:hidden}.row{display:grid;grid-template-columns:80px 1fr 120px 1fr;border-top:1px solid #e5e7eb}.row span{padding:8px}.head{background:#f1f5f9;font-weight:bold;border-top:0}
CSS

# Ensure deps
cd "$PROJECT/backend"
python3 -m venv venv >/dev/null 2>&1 || true
source venv/bin/activate
pip install -q fastapi uvicorn python-multipart
cd "$PROJECT/frontend"
npm install >/dev/null 2>&1 || true

echo "Actualización completada. Reinicia backend y frontend."
