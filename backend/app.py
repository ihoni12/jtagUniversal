from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import subprocess
import os
import uuid
import threading
import queue
import time

app = Flask(__name__)
CORS(app)

UPLOAD_DIR = os.path.abspath("uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

jobs = {}


def run_jtag_job(job_id, bsdl_path, netlist_path=None):
    q = jobs[job_id]["queue"]
    jobs[job_id]["status"] = "running"
    q.put("Iniciando revision JTAG...\n")
    if netlist_path:
        q.put("Archivo netlist recibido. La revision validara cortos contra el netlist.\n")
    else:
        q.put("Sin netlist: se ejecuta revision general de cortos.\n")

    try:
        cmd = ["sudo", "python3", "-u", "mega_jtag_bsdl_netlist_test.py", bsdl_path]
        if netlist_path:
            cmd += [netlist_path, "--uut-ref", "U1", "--netlist-test"]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

        jobs[job_id]["proc"] = proc

        for line in proc.stdout:
            q.put(line)

        proc.wait()
        q.put(f"\nProceso terminado con codigo: {proc.returncode}\n")
        jobs[job_id]["status"] = "done" if proc.returncode == 0 else "error"
        q.put("__DONE__")

    except Exception as e:
        jobs[job_id]["status"] = "error"
        q.put(f"\nERROR: {e}\n")
        q.put("__DONE__")


@app.route("/api/start", methods=["POST"])
def start_test():
    if "bsdl" not in request.files:
        return jsonify({"ok": False, "error": "No recibi archivo BSDL"}), 400

    file = request.files["bsdl"]
    if not file.filename:
        return jsonify({"ok": False, "error": "Archivo BSDL vacio"}), 400

    filename = f"{uuid.uuid4()}.bsdl"
    bsdl_path = os.path.abspath(os.path.join(UPLOAD_DIR, filename))
    file.save(bsdl_path)

    netlist_path = None
    netlist_file = request.files.get("netlist")
    if netlist_file and netlist_file.filename:
        net_filename = f"{uuid.uuid4()}.net"
        netlist_path = os.path.abspath(os.path.join(UPLOAD_DIR, net_filename))
        netlist_file.save(netlist_path)

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "queue": queue.Queue(),
        "status": "created",
        "created_at": time.time(),
        "proc": None,
        "filename": file.filename,
        "netlist_filename": netlist_file.filename if netlist_file and netlist_file.filename else None,
    }

    t = threading.Thread(target=run_jtag_job, args=(job_id, bsdl_path, netlist_path), daemon=True)
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
            if msg == "__DONE__":
                yield "data: __DONE__\n\n"
                break
            msg = msg.replace("\r", "").replace("\n", "\\n")
            yield f"data: {msg}\n\n"

    return Response(stream(), mimetype="text/event-stream")


@app.route("/api/ping")
def ping():
    return jsonify({"ok": True, "message": "Servidor JTAG activo"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
