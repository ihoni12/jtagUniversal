from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
import subprocess
import os
import uuid
import threading
import queue
import time
import json
import re
import shutil

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
REPORT_BASE_DIR = os.path.join(BASE_DIR, "jtag_web_reports")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(REPORT_BASE_DIR, exist_ok=True)

jobs = {}

IMPORTANT_PATTERNS = [
    (re.compile(r"\[OK\]"), "OK"),
    (re.compile(r"\[FAIL\]"), "FAIL"),
    (re.compile(r"\[ERROR\]|\bERROR\b"), "ERROR"),
    (re.compile(r"CORTO|CORTO_SOSPECHOSO"), "SHORT"),
    (re.compile(r"Revision de lineas conectadas a la Pi"), "PI_LINES"),
]

def safe_name(name):
    name = os.path.basename(name or "archivo")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)

def put(q, text, kind="log"):
    q.put(json.dumps({"type": kind, "text": text}, ensure_ascii=False))

def simplify_line(line):
    s = line.strip()
    if not s:
        return None
    keep = [
        "=== INFO DEL BSDL ===",
        "=== INFO DEL NETLIST ===",
        "=== REVISION DE LINEAS",
        "=== RESUMEN COMO SE DEBE ===",
        "Revision de lineas conectadas a la Pi:",
        "Cortos / conexiones detectadas:",
        "Cortos sospechosos reales:",
        "Reportes guardados",
        "Chip:", "TAP:", "Boundary Length:", "Pines controlables encontrados:",
        "Nets encontradas:", "Conexiones encontradas:", "Referencia del chip JTAG usada:",
        "OK:", "FAIL:", "ERROR:", "OK sin seguidores extra:", "OK porque el netlist permite", "CORTO SOSPECHOSO",
        "-> ERROR", "-> FAIL", "-> OK", "[OK]", "[FAIL]", "[ERROR]", "[CORTO?]", "[OK NETLIST]",
        "Lineas con problema:", "Problemas por NET:",
        "UUT_TO_PI", "PI_TO_UUT", "samples",
    ]
    if any(k in s for k in keep):
        return line
    if re.match(r"^\[\d+/\d+\]", s):
        return line
    if s.startswith(("NET_", "  NET_", "PH", "PK", "PA", "PB", "PC", "PD", "PE", "PF", "PG", "PJ", "PL")) and ("->" in s or "UUT" in s):
        return line
    return None


def emit_external_report(q, out_dir):
    path = os.path.join(out_dir, "external_line_test_report.json")
    if not os.path.exists(path):
        put(q, "\nLíneas externas detectadas: no se generó reporte. Revisa que el netlist tenga PI.GPIOxx y que esté activado --external-line-test.\n", "warn")
        return
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except Exception as e:
        put(q, f"\nNo pude leer external_line_test_report.json: {e}\n", "warn")
        return

    ok = sum(1 for x in data if x.get("status") == "OK")
    fail = sum(1 for x in data if x.get("status") == "FAIL")
    err = sum(1 for x in data if x.get("status") == "ERROR")
    put(q, "\n=== LÍNEAS EXTERNAS TX/RX/SPI/I2C/GPIO ===\n", "log")
    put(q, f"OK: {ok} | FAIL: {fail} | ERROR: {err}\n", "log")
    if not data:
        put(q, "No encontré conexiones UUT <-> PI.GPIOxx en el netlist.\n", "warn")
        return
    for i, r in enumerate(data, start=1):
        status = r.get("status", "UNKNOWN")
        net = r.get("net", "NET")
        pin = r.get("uut_pin", "?")
        gpio = r.get("pi_gpio", "?")
        direction = r.get("direction", "?")
        put(q, f"[{i}/{len(data)}] {net}: UUT {pin} <-> PI.GPIO{gpio} ({direction}) -> {status}\n", "log")
        if r.get("error"):
            put(q, f"   Error: {r.get('error')}\n", "error")
        for result in r.get("results", []):
            samples = result.get("samples", [])
            put(q, f"   {result.get('direction')}: {samples}\n", "log")

def run_jtag_job(job_id, bsdl_path, netlist_path=None, options=None):
    options = options or {}
    q = jobs[job_id]["queue"]
    jobs[job_id]["status"] = "running"
    out_dir = os.path.join(REPORT_BASE_DIR, job_id)
    os.makedirs(out_dir, exist_ok=True)
    jobs[job_id]["out_dir"] = out_dir

    put(q, "Empezó la revisión JTAG.\n", "info")
    put(q, "BSDL cargado correctamente.\n", "info")
    if netlist_path:
        put(q, "Netlist cargado: se validan conexiones esperadas y líneas externas.\n", "info")
    else:
        put(q, "Sin netlist: se hace revisión general de cortos.\n", "info")

    try:
        cmd = ["sudo", "python3", "-u", "mega_jtag_bsdl_netlist_test.py", bsdl_path]
        if netlist_path:
            cmd += [netlist_path, "--uut-ref", options.get("uut_ref") or "U1"]
        cmd += ["--out", out_dir]
        if options.get("netlist_test", True) and netlist_path:
            cmd += ["--netlist-test"]
        if options.get("external_line_test", True) and netlist_path:
            cmd += ["--external-line-test"]
            put(q, "Revisión externa activada: buscaré U1.PIN conectado a PI.GPIOxx.\n", "info")
        if options.get("external_bidir", False):
            cmd += ["--external-bidir"]
        if options.get("no_short_test", False):
            cmd += ["--no-short-test"]
        if options.get("map_only", False):
            cmd += ["--map-only", "--print-board-map"]

        put(q, "Modo: " + ("mapa solamente" if options.get("map_only") else "revisión física") + "\n", "info")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=BASE_DIR,
        )
        jobs[job_id]["proc"] = proc

        raw_log_path = os.path.join(out_dir, "raw_console.log")
        with open(raw_log_path, "w", encoding="utf-8", errors="ignore") as raw_log:
            for line in proc.stdout:
                raw_log.write(line)
                raw_log.flush()
                if options.get("simple_output", True):
                    simple = simplify_line(line)
                    if simple is not None:
                        put(q, simple, "log")
                else:
                    put(q, line, "log")

        proc.wait()
        if netlist_path and options.get("external_line_test", True):
            emit_external_report(q, out_dir)
        jobs[job_id]["status"] = "done" if proc.returncode == 0 else "error"
        put(q, f"\nRevisión terminada. Código: {proc.returncode}\n", "done" if proc.returncode == 0 else "error")
        put(q, "__DONE__", "done")

    except Exception as e:
        jobs[job_id]["status"] = "error"
        put(q, f"ERROR: {e}\n", "error")
        put(q, "__DONE__", "done")

@app.route("/api/start", methods=["POST"])
def start_test():
    if "bsdl" not in request.files:
        return jsonify({"ok": False, "error": "No recibí archivo BSDL"}), 400

    bsdl_file = request.files["bsdl"]
    if not bsdl_file.filename:
        return jsonify({"ok": False, "error": "Archivo BSDL vacío"}), 400

    job_id = str(uuid.uuid4())
    job_upload_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_upload_dir, exist_ok=True)

    bsdl_path = os.path.abspath(os.path.join(job_upload_dir, safe_name(bsdl_file.filename)))
    bsdl_file.save(bsdl_path)

    netlist_path = None
    netlist_file = request.files.get("netlist")
    if netlist_file and netlist_file.filename:
        netlist_path = os.path.abspath(os.path.join(job_upload_dir, safe_name(netlist_file.filename)))
        netlist_file.save(netlist_path)

    options = {
        "uut_ref": request.form.get("uut_ref", "U1"),
        "simple_output": request.form.get("simple_output", "true") == "true",
        "external_line_test": request.form.get("external_line_test", "true") == "true",
        "external_bidir": request.form.get("external_bidir", "false") == "true",
        "netlist_test": request.form.get("netlist_test", "true") == "true",
        "no_short_test": request.form.get("no_short_test", "false") == "true",
        "map_only": request.form.get("map_only", "false") == "true",
    }

    jobs[job_id] = {
        "queue": queue.Queue(),
        "status": "created",
        "created_at": time.time(),
        "proc": None,
        "filename": bsdl_file.filename,
        "netlist_filename": netlist_file.filename if netlist_file and netlist_file.filename else None,
        "options": options,
        "out_dir": None,
    }

    t = threading.Thread(target=run_jtag_job, args=(job_id, bsdl_path, netlist_path, options), daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id})

@app.route("/api/progress/<job_id>")
def progress(job_id):
    if job_id not in jobs:
        return "Job no existe", 404

    def stream():
        q = jobs[job_id]["queue"]
        while True:
            msg = q.get()
            try:
                payload = json.loads(msg)
            except Exception:
                payload = {"type": "log", "text": msg}
            data = json.dumps(payload, ensure_ascii=False).replace("\n", "\\n")
            yield f"data: {data}\n\n"
            if payload.get("text") == "__DONE__":
                break

    return Response(stream(), mimetype="text/event-stream")

@app.route("/api/status/<job_id>")
def status(job_id):
    if job_id not in jobs:
        return jsonify({"ok": False, "error": "Job no existe"}), 404
    j = jobs[job_id]
    return jsonify({"ok": True, "status": j["status"], "files": list_report_files(j.get("out_dir"))})

def list_report_files(out_dir):
    if not out_dir or not os.path.isdir(out_dir):
        return []
    files = []
    for name in sorted(os.listdir(out_dir)):
        path = os.path.join(out_dir, name)
        if os.path.isfile(path):
            files.append({"name": name, "url": f"/api/report/{os.path.basename(out_dir)}/{name}"})
    return files

@app.route("/api/report/<job_id>/<path:filename>")
def report(job_id, filename):
    if job_id not in jobs:
        return "Job no existe", 404
    out_dir = jobs[job_id].get("out_dir")
    path = os.path.abspath(os.path.join(out_dir, filename))
    if not path.startswith(os.path.abspath(out_dir)) or not os.path.exists(path):
        return "Archivo no existe", 404
    return send_file(path, as_attachment=True)

@app.route("/api/stop/<job_id>", methods=["POST"])
def stop(job_id):
    if job_id not in jobs:
        return jsonify({"ok": False, "error": "Job no existe"}), 404
    proc = jobs[job_id].get("proc")
    if proc and proc.poll() is None:
        proc.terminate()
        jobs[job_id]["status"] = "stopped"
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "No hay proceso corriendo"})

@app.route("/api/ping")
def ping():
    return jsonify({"ok": True, "message": "Servidor JTAG activo"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
