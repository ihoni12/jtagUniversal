from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import subprocess, os, uuid, threading, queue, time, sys, json, signal

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
REPORT_DIR = os.path.join(BASE_DIR, "jtag_reports")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

jobs = {}

def as_bool(value, default=False):
    if value is None:
        return default
    return str(value).lower() in ["1", "true", "yes", "on"]

def put(q, text):
    q.put(str(text))

def run_jtag_job(job_id, bsdl_path, netlist_path=None, options=None):
    options = options or {}
    q = jobs[job_id]["queue"]
    jobs[job_id]["status"] = "running"

    test_shorts = options.get("test_shorts", True)
    test_netlist = options.get("test_netlist", False)
    test_external = options.get("test_external", False)
    external_bidir = options.get("external_bidir", False)
    uut_ref = options.get("uut_ref") or "U1"

    put(q, "=================================\n")
    put(q, "JTAG UNIVERSAL TEST STATION\n")
    put(q, "=================================\n")
    put(q, "BSDL cargado OK\n")
    put(q, "Netlist: " + ("SI\n" if netlist_path else "NO\n"))
    put(q, "Prueba cortos: " + ("SI\n" if test_shorts else "NO\n"))
    put(q, "Validar netlist: " + ("SI\n" if test_netlist else "NO\n"))
    put(q, "Lineas externas Pi TX/RX/SPI/I2C/GPIO: " + ("SI\n" if test_external else "NO\n"))
    put(q, "\n")

    if (test_netlist or test_external) and not netlist_path:
        put(q, "ERROR: seleccionaste una prueba que necesita netlist, pero no subiste netlist.\n")
        jobs[job_id]["status"] = "error"
        put(q, "__DONE__")
        return

    try:
        cmd = [sys.executable, "-u", "jtag_tester_core.py", bsdl_path, "--out", REPORT_DIR]
        if netlist_path:
            cmd.insert(4, netlist_path)  # after bsdl path
            cmd += ["--uut-ref", uut_ref]
        if not test_shorts:
            cmd.append("--no-short-test")
        if test_netlist:
            cmd.append("--netlist-test")
        if test_external:
            cmd.append("--external-line-test")
        if external_bidir:
            cmd.append("--external-bidir")

        put(q, "Comando interno:\n")
        put(q, " ".join(cmd).replace(bsdl_path, "BSDL_SUBIDO").replace(netlist_path or "__NO_NET__", "NETLIST_SUBIDO") + "\n\n")

        # Si el backend fue iniciado con sudo, esto queda con permisos para OpenOCD/GPIO.
        # Si no, el usuario debe iniciar start_backend.sh con sudo.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=BASE_DIR,
        )
        jobs[job_id]["proc"] = proc

        for line in proc.stdout:
            put(q, line)
        proc.wait()

        # Intentar agregar resumen JSON simple al final, si existe.
        external_json = os.path.join(REPORT_DIR, "external_line_test_report.json")
        if os.path.exists(external_json):
            try:
                with open(external_json, "r") as f:
                    data = json.load(f)
                put(q, "\n=================================\n")
                put(q, "RESUMEN SIMPLE LINEAS EXTERNAS\n")
                put(q, "=================================\n")
                if not data:
                    put(q, "No se encontraron lineas UUT <-> PI.GPIO en el netlist.\n")
                else:
                    for r in data:
                        put(q, f"{r.get('net')} | UUT {r.get('uut_pin')} <-> PI.GPIO{r.get('pi_gpio')} | {r.get('direction')} | {r.get('status')}\n")
            except Exception:
                pass

        put(q, f"\nProceso terminado con codigo: {proc.returncode}\n")
        jobs[job_id]["status"] = "done" if proc.returncode == 0 else "error"
        put(q, "__DONE__")
    except Exception as e:
        jobs[job_id]["status"] = "error"
        put(q, f"\nERROR: {e}\n")
        put(q, "__DONE__")

@app.route("/api/start", methods=["POST"])
def start_test():
    if "bsdl" not in request.files:
        return jsonify({"ok": False, "error": "No recibi archivo BSDL"}), 400
    bsdl_file = request.files["bsdl"]
    if not bsdl_file.filename:
        return jsonify({"ok": False, "error": "Archivo BSDL vacio"}), 400

    bsdl_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}_{bsdl_file.filename}")
    bsdl_file.save(bsdl_path)

    netlist_path = None
    netlist_file = request.files.get("netlist")
    if netlist_file and netlist_file.filename:
        netlist_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}_{netlist_file.filename}")
        netlist_file.save(netlist_path)

    options = {
        "test_shorts": as_bool(request.form.get("test_shorts"), True),
        "test_netlist": as_bool(request.form.get("test_netlist"), False),
        "test_external": as_bool(request.form.get("test_external"), False),
        "external_bidir": as_bool(request.form.get("external_bidir"), False),
        "uut_ref": request.form.get("uut_ref") or "U1",
    }

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"queue": queue.Queue(), "status": "created", "created_at": time.time(), "proc": None}
    threading.Thread(target=run_jtag_job, args=(job_id, bsdl_path, netlist_path, options), daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})

@app.route("/api/progress/<job_id>")
def progress(job_id):
    if job_id not in jobs:
        return "Job no existe", 404
    def stream():
        q = jobs[job_id]["queue"]
        while True:
            msg = q.get()
            if msg == "__DONE__":
                yield "data: __DONE__\n\n"
                break
            msg = msg.replace("\r", "").replace("\n", "\\n")
            yield f"data: {msg}\n\n"
    return Response(stream(), mimetype="text/event-stream")

@app.route("/api/stop/<job_id>", methods=["POST"])
def stop_job(job_id):
    job = jobs.get(job_id)
    if not job or not job.get("proc"):
        return jsonify({"ok": False, "error": "No hay proceso activo"}), 404
    try:
        job["proc"].terminate()
        job["status"] = "stopped"
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/ping")
def ping():
    return jsonify({"ok": True, "message": "Servidor JTAG activo"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
