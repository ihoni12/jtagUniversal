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

from jtag_tester_core import (
    parse_bsdl, parse_netlist, normalize_ref, find_uut_ref_in_netlist,
    build_board_map, DEFAULT_UUT_REFS, create_openocd_cfg, start_openocd, recv_all,
    cmd, sample, extest_write
)
from revisiones.pin_review import review_pin
from revisiones.tx_rx_review import review_tx_rx

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



def save_uploaded_files(job_id, bsdl_file, netlist_file=None):
    job_upload_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_upload_dir, exist_ok=True)
    bsdl_path = os.path.abspath(os.path.join(job_upload_dir, safe_name(bsdl_file.filename)))
    bsdl_file.save(bsdl_path)
    netlist_path = None
    if netlist_file and netlist_file.filename:
        netlist_path = os.path.abspath(os.path.join(job_upload_dir, safe_name(netlist_file.filename)))
        netlist_file.save(netlist_path)
    return bsdl_path, netlist_path



def uart_id_from_net(net_name):
    """Devuelve UART0/UART1/etc si el net se llama NET_UART0_TX/RX."""
    text = str(net_name or "").upper()
    m = re.search(r"UART\s*([0-9]+)", text)
    if m:
        return f"UART{m.group(1)}"
    if "UART" in text:
        return "UART"
    return None


def uart_role_from_net_or_pin(net_name, pin_name=""):
    text = f"{net_name or ''} {pin_name or ''}".upper()
    if re.search(r"(^|[_\-])TX([_\-]|$)", text) or text.endswith("TX"):
        return "TX"
    if re.search(r"(^|[_\-])RX([_\-]|$)", text) or text.endswith("RX"):
        return "RX"
    return None


def build_uart_pairs_from_board_map(board_map):
    """Agrupa TX/RX por número de UART usando el netlist.

    Ejemplo:
      NET_UART0_TX -> U1.PE1 + PI.GPIO15
      NET_UART0_RX -> U1.PE0 + PI.GPIO14
    crea una pareja UART0: TX PE1, RX PE0.
    """
    groups = {}
    for item in board_map or []:
        net = item.get("net", "")
        uart_id = uart_id_from_net(net)
        role = uart_role_from_net_or_pin(net, item.get("driver", ""))
        if not uart_id or role not in ["TX", "RX"]:
            continue
        ext = None
        for c in item.get("all_connections", []):
            gpio = connection_pi_gpio_local(c)
            if gpio is not None:
                ext = gpio
                break
        g = groups.setdefault(uart_id, {"id": uart_id, "tx": None, "rx": None})
        g[role.lower()] = {"pin": item.get("driver"), "net": net, "pi_gpio": ext}

    pairs = []
    for uart_id, g in sorted(groups.items()):
        tx = g.get("tx")
        rx = g.get("rx")
        complete = bool(tx and rx)
        pairs.append({
            "id": uart_id,
            "tx": tx,
            "rx": rx,
            "complete": complete,
            "label": f"{uart_id}: TX {tx.get('pin') if tx else '?'} / RX {rx.get('pin') if rx else '?'}",
            "note": "Completa" if complete else "Falta TX o RX en el netlist",
        })
    return pairs


def annotate_uart_pairs_on_pins(pin_rows, uart_pairs):
    by_pin = {p["name"]: p for p in pin_rows}
    for pair in uart_pairs or []:
        for role in ["tx", "rx"]:
            side = pair.get(role)
            if not side:
                continue
            pin = by_pin.get(side.get("pin"))
            if not pin:
                continue
            pin["uart_pair"] = {
                "id": pair["id"],
                "role": role.upper(),
                "other_pin": pair.get("rx" if role == "tx" else "tx", {}).get("pin") if pair.get("rx" if role == "tx" else "tx") else None,
                "complete": pair.get("complete", False),
                "tx_pin": pair.get("tx", {}).get("pin") if pair.get("tx") else None,
                "rx_pin": pair.get("rx", {}).get("pin") if pair.get("rx") else None,
            }
    return pin_rows

def analyze_files(bsdl_path, netlist_path=None, uut_ref="U1"):
    info = parse_bsdl(bsdl_path)
    pins = info["pins"]
    nets = None
    board_map = []
    unknown_uut_pins = []
    used_uut_ref = normalize_ref(uut_ref or "U1")
    parser_name = None

    if netlist_path:
        parser_name, nets = parse_netlist(netlist_path)
        refs = list(dict.fromkeys([used_uut_ref] + DEFAULT_UUT_REFS))
        used_uut_ref = normalize_ref(uut_ref) if uut_ref else find_uut_ref_in_netlist(nets, refs)
        board_map, unknown_uut_pins = build_board_map(nets, pins, used_uut_ref)

    pin_nets = {}
    pin_connections = {}
    for item in board_map or []:
        driver = item.get("driver")
        if not driver:
            continue
        pin_nets.setdefault(driver, []).append(item.get("net"))
        pin_connections.setdefault(driver, []).extend(item.get("all_connections", []))

    pin_rows = []
    for name in sorted(pins.keys()):
        data = pins[name]
        nets_for_pin = sorted(set(pin_nets.get(name, [])))
        funcs = guess_pin_functions(name, nets_for_pin)
        special = guess_pin_special(name, nets_for_pin, funcs)
        external = find_pin_external_connection(name, board_map or [])
        pin_rows.append({
            "name": name,
            "input_bit": data.get("input_bit"),
            "output_bit": data.get("output_bit"),
            "control_bit": data.get("control_bit"),
            "nets": nets_for_pin,
            "connections": pin_connections.get(name, []),
            "functions": funcs,
            "special": special,
            "external": external,
        })

    uart_pairs = build_uart_pairs_from_board_map(board_map or [])
    pin_rows = annotate_uart_pairs_on_pins(pin_rows, uart_pairs)

    return {
        "chipname": info["chipname"],
        "tap": f"{info['chipname']}.cpu",
        "bits": info["bits"],
        "irlen": info["irlen"],
        "extest": info["extest"],
        "sample": info["sample"],
        "idcode": info["idcode"],
        "pin_count": len(pin_rows),
        "pins": pin_rows,
        "net_count": len(nets or {}),
        "board_map_count": len(board_map or []),
        "netlist_parser": parser_name,
        "uut_ref": used_uut_ref,
        "unknown_uut_pins": unknown_uut_pins,
        "uart_pairs": uart_pairs,
    }


def guess_pin_functions(pin, nets):
    hay = " ".join([pin] + list(nets)).upper()
    funcs = []
    checks = [
        ("TX", "UART TX"), ("RX", "UART RX"), ("UART", "UART"),
        ("MOSI", "SPI MOSI"), ("MISO", "SPI MISO"), ("SCK", "SPI SCK"), ("SPI", "SPI"),
        ("SDA", "I2C SDA"), ("SCL", "I2C SCL"), ("I2C", "I2C"),
        ("PWM", "PWM"), ("ADC", "ADC"), ("GPIO", "GPIO"),
    ]
    for key, label in checks:
        if key in hay and label not in funcs:
            funcs.append(label)
    return funcs or ["BSDL pin"]


def guess_pin_special(pin, nets, funcs):
    hay = " ".join([pin] + list(nets) + list(funcs)).upper()
    if "UART TX" in hay or re.search(r"(^|[_\-])TX([_\-]|$)", hay):
        return {"kind": "TX", "label": "TX", "test": "tx_rx"}
    if "UART RX" in hay or re.search(r"(^|[_\-])RX([_\-]|$)", hay):
        return {"kind": "RX", "label": "RX", "test": "tx_rx"}
    if "SDA" in hay:
        return {"kind": "SDA", "label": "I2C", "test": "external"}
    if "SCL" in hay:
        return {"kind": "SCL", "label": "I2C", "test": "external"}
    for k in ["MOSI", "MISO", "SCK"]:
        if k in hay:
            return {"kind": k, "label": "SPI", "test": "external"}
    return None

def find_pin_external_connection(pin, board_map):
    for item in board_map or []:
        if item.get("driver") != pin:
            continue
        for c in item.get("all_connections", []):
            try:
                gpio = connection_pi_gpio_local(c)
            except Exception:
                gpio = None
            if gpio is not None:
                return {"net": item.get("net"), "pi_gpio": gpio, "direction_hint": classify_external_direction_local(item.get("net", ""), pin)}
    return None

def connection_pi_gpio_local(conn):
    ref = str(conn.get("ref", "")).upper()
    pin = str(conn.get("pin", "")).upper()
    raw = str(conn.get("raw", "")).upper()
    text = f"{ref}.{pin} {raw}"
    m = re.search(r"GPIO\s*([0-9]+)", text)
    if m:
        return int(m.group(1))
    if ref in ["PI", "RPI", "RASPBERRY", "RASPBERRYPI"] and pin.isdigit():
        return int(pin)
    return None

def classify_external_direction_local(net_name, pin):
    n = f"{net_name} {pin}".upper()
    if any(k in n for k in ["RX", "MISO"]):
        return "PI_TO_UUT"
    return "UUT_TO_PI"

def emit_external_report(q, out_dir):
    path = os.path.join(out_dir, "external_line_test_report.json")
    if not os.path.exists(path):
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


@app.route("/api/analyze", methods=["POST"])
def analyze_upload():
    if "bsdl" not in request.files:
        return jsonify({"ok": False, "error": "No recibí archivo BSDL"}), 400
    bsdl_file = request.files["bsdl"]
    if not bsdl_file.filename:
        return jsonify({"ok": False, "error": "Archivo BSDL vacío"}), 400
    job_id = str(uuid.uuid4())
    netlist_file = request.files.get("netlist")
    bsdl_path, netlist_path = save_uploaded_files(job_id, bsdl_file, netlist_file)
    try:
        data = analyze_files(bsdl_path, netlist_path, request.form.get("uut_ref", "U1"))
        data["session_id"] = job_id
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


def run_pin_job(job_id, bsdl_path, netlist_path, pin, options=None):
    options = options or {}
    q = jobs[job_id]["queue"]
    jobs[job_id]["status"] = "running"
    out_dir = os.path.join(REPORT_BASE_DIR, job_id)
    os.makedirs(out_dir, exist_ok=True)
    jobs[job_id]["out_dir"] = out_dir
    proc = None
    sock = None
    try:
        info = parse_bsdl(bsdl_path)
        chipname = info["chipname"]
        tap = f"{chipname}.cpu"
        bits = info["bits"]
        extest = info["extest"]
        sample_opcode = info["sample"]
        idcode = info["idcode"]
        board_map = None
        if netlist_path:
            _, nets = parse_netlist(netlist_path)
            refs = [options.get("uut_ref") or "U1"] + DEFAULT_UUT_REFS
            uut_ref = normalize_ref(options.get("uut_ref")) if options.get("uut_ref") else find_uut_ref_in_netlist(nets, refs)
            board_map, _ = build_board_map(nets, info["pins"], uut_ref)

        put(q, f"Empezó revisión del pin {pin}.\n", "info")
        cfg_path = create_openocd_cfg(chipname, info["irlen"], work_dir=out_dir)
        proc, sock = start_openocd(cfg_path)
        jobs[job_id]["proc"] = proc
        recv_all(sock)
        put(q, "SCAN CHAIN:\n" + cmd(sock, "scan_chain") + "\n", "log")
        cmd(sock, f"irscan {tap} {idcode}")
        put(q, "IDCODE:\n" + cmd(sock, f"drscan {tap} 32 0") + "\n", "log")
        result = review_pin(sock, tap, extest, sample_opcode, bits, info["pins"], pin, board_map=board_map)
        extest_write(sock, tap, extest, bits, 0)
        with open(os.path.join(out_dir, "single_pin_report.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        status = result.get("status")
        put(q, f"Pin {pin}: {status}\n", "done" if result.get("passed") else "error")
        if result.get("unexpected_followers"):
            put(q, "Corto sospechoso con: " + ", ".join(result["unexpected_followers"]) + "\n", "error")
        jobs[job_id]["status"] = "done"
        put(q, "__DONE__", "done")
    except Exception as e:
        jobs[job_id]["status"] = "error"
        put(q, f"ERROR: {e}\n", "error")
        put(q, "__DONE__", "done")
    finally:
        if sock:
            try: sock.close()
            except Exception: pass
        if proc:
            try: proc.terminate(); proc.wait(timeout=5)
            except Exception:
                try: proc.kill()
                except Exception: pass


def run_external_pin_job(job_id, bsdl_path, netlist_path, pin, options=None):
    options = options or {}
    q = jobs[job_id]["queue"]
    jobs[job_id]["status"] = "running"
    out_dir = os.path.join(REPORT_BASE_DIR, job_id)
    os.makedirs(out_dir, exist_ok=True)
    jobs[job_id]["out_dir"] = out_dir
    proc = None
    sock = None
    try:
        if not netlist_path:
            raise RuntimeError("Para probar TX/RX o conexión externa necesito netlist con UUT.PIN y PI.GPIOxx")
        info = parse_bsdl(bsdl_path)
        chipname = info["chipname"]
        tap = f"{chipname}.cpu"
        _, nets = parse_netlist(netlist_path)
        refs = [options.get("uut_ref") or "U1"] + DEFAULT_UUT_REFS
        uut_ref = normalize_ref(options.get("uut_ref")) if options.get("uut_ref") else find_uut_ref_in_netlist(nets, refs)

        # Reutilizamos el generador de pruebas externas, pero filtrado al pin elegido.
        from jtag_tester_core import build_external_line_tests, run_external_line_tests
        all_tests = build_external_line_tests(nets, info["pins"], uut_ref, external_bidir=options.get("external_bidir", False))
        tests = [t for t in all_tests if str(t.get("uut_pin", "")).upper() == pin.upper()]
        if not tests:
            raise RuntimeError(f"El pin {pin} no tiene conexión PI.GPIOxx en el netlist. Para TX/RX conecta algo como U1.{pin} + PI.GPIO15")

        put(q, f"Empezó revisión de conexión especial del pin {pin}.\n", "info")
        for t in tests:
            put(q, f"Conexión: {t['net']} · UUT {t['uut_pin']} <-> PI.GPIO{t['pi_gpio']} · {t['direction']}\n", "info")

        cfg_path = create_openocd_cfg(chipname, info["irlen"], work_dir=out_dir)
        proc, sock = start_openocd(cfg_path)
        jobs[job_id]["proc"] = proc
        recv_all(sock)
        put(q, "SCAN CHAIN:\n" + cmd(sock, "scan_chain") + "\n", "log")
        cmd(sock, f"irscan {tap} {info['idcode']}")
        put(q, "IDCODE:\n" + cmd(sock, f"drscan {tap} 32 0") + "\n", "log")

        report = run_external_line_tests(sock, tap, info["extest"], info["sample"], info["bits"], info["pins"], tests)
        extest_write(sock, tap, info["extest"], info["bits"], 0)
        with open(os.path.join(out_dir, "special_pin_connection_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        ok = sum(1 for r in report if r.get("status") == "OK")
        fail = sum(1 for r in report if r.get("status") == "FAIL")
        err = sum(1 for r in report if r.get("status") == "ERROR")
        put(q, f"Resultado conexión {pin}: OK {ok} | FAIL {fail} | ERROR {err}\n", "done" if fail == 0 and err == 0 else "error")
        for r in report:
            if r.get("error"):
                put(q, f"ERROR en {r.get('net')}: {r.get('error')}\n", "error")
        jobs[job_id]["status"] = "done" if fail == 0 and err == 0 else "error"
        put(q, "__DONE__", "done")
    except Exception as e:
        jobs[job_id]["status"] = "error"
        put(q, f"ERROR: {e}\n", "error")
        put(q, "__DONE__", "done")
    finally:
        if sock:
            try: sock.close()
            except Exception: pass
        if proc:
            try: proc.terminate(); proc.wait(timeout=5)
            except Exception:
                try: proc.kill()
                except Exception: pass




def run_uart_pair_job(job_id, bsdl_path, netlist_path, uart_id, tx_pin=None, rx_pin=None, options=None):
    """Revisión conjunta de pareja UART según netlist.

    Es una prueba eléctrica conjunta, no una transmisión UART con baud rate.
    Revisa las dos líneas que forman la pareja:
      TX del UUT -> RX/GPIO de Raspberry
      RX del UUT <- TX/GPIO de Raspberry
    """
    options = options or {}
    q = jobs[job_id]["queue"]
    jobs[job_id]["status"] = "running"
    out_dir = os.path.join(REPORT_BASE_DIR, job_id)
    os.makedirs(out_dir, exist_ok=True)
    jobs[job_id]["out_dir"] = out_dir
    proc = None
    sock = None
    try:
        if not netlist_path:
            raise RuntimeError("Para revisar UART completo necesito netlist con NET_UARTx_TX y NET_UARTx_RX.")
        info = parse_bsdl(bsdl_path)
        chipname = info["chipname"]
        tap = f"{chipname}.cpu"
        _, nets = parse_netlist(netlist_path)
        refs = [options.get("uut_ref") or "U1"] + DEFAULT_UUT_REFS
        uut_ref = normalize_ref(options.get("uut_ref")) if options.get("uut_ref") else find_uut_ref_in_netlist(nets, refs)
        board_map, _ = build_board_map(nets, info["pins"], uut_ref)
        pairs = build_uart_pairs_from_board_map(board_map)

        chosen = None
        if uart_id:
            uid = str(uart_id).upper()
            chosen = next((p for p in pairs if str(p.get("id", "")).upper() == uid), None)
        if not chosen and tx_pin and rx_pin:
            tx_pin = tx_pin.upper(); rx_pin = rx_pin.upper()
            chosen = {"id": "MANUAL", "tx": {"pin": tx_pin}, "rx": {"pin": rx_pin}, "complete": True, "label": f"MANUAL: TX {tx_pin} / RX {rx_pin}"}
        if not chosen:
            raise RuntimeError("No encontré pareja UART. Usa nombres como NET_UART0_TX y NET_UART0_RX en el netlist.")
        if not chosen.get("tx") or not chosen.get("rx"):
            raise RuntimeError(f"{chosen.get('id')} no está completo: falta TX o RX en el netlist.")

        selected_pins = {chosen["tx"]["pin"], chosen["rx"]["pin"]}
        from jtag_tester_core import build_external_line_tests, run_external_line_tests
        all_tests = build_external_line_tests(nets, info["pins"], uut_ref, external_bidir=False)
        tests = [t for t in all_tests if str(t.get("uut_pin", "")).upper() in selected_pins]
        # Mantener sólo las líneas de la misma pareja UART si el net tiene ID.
        if chosen.get("id") != "MANUAL":
            tests = [t for t in tests if uart_id_from_net(t.get("net")) == chosen.get("id")]
        if len(tests) < 2:
            raise RuntimeError("Encontré la pareja, pero no encontré las dos conexiones PI.GPIOxx. Ejemplo necesario: NET_UART0_TX U1.PE1 PI.GPIO15 y NET_UART0_RX U1.PE0 PI.GPIO14.")

        put(q, f"Empezó revisión UART completa: {chosen.get('label')}\n", "info")
        put(q, "Esta revisión prueba las dos líneas juntas como pareja TX/RX del mismo UART.\n", "info")
        put(q, "Nota: no se mezcla UART0_TX con UART1_RX salvo que elijas pareja manual. Lo correcto es TX0 con RX0.\n\n", "info")
        for t in tests:
            put(q, f"Línea: {t['net']} · UUT {t['uut_pin']} <-> PI.GPIO{t['pi_gpio']} · {t['direction']}\n", "info")

        cfg_path = create_openocd_cfg(chipname, info["irlen"], work_dir=out_dir)
        proc, sock = start_openocd(cfg_path)
        jobs[job_id]["proc"] = proc
        recv_all(sock)
        put(q, "SCAN CHAIN:\n" + cmd(sock, "scan_chain") + "\n", "log")
        cmd(sock, f"irscan {tap} {info['idcode']}")
        put(q, "IDCODE:\n" + cmd(sock, f"drscan {tap} 32 0") + "\n", "log")

        report = run_external_line_tests(sock, tap, info["extest"], info["sample"], info["bits"], info["pins"], tests)
        extest_write(sock, tap, info["extest"], info["bits"], 0)
        full_report = {"uart": chosen, "tests": report}
        with open(os.path.join(out_dir, "uart_pair_report.json"), "w", encoding="utf-8") as f:
            json.dump(full_report, f, indent=2, ensure_ascii=False)
        ok = sum(1 for r in report if r.get("status") == "OK")
        fail = sum(1 for r in report if r.get("status") == "FAIL")
        err = sum(1 for r in report if r.get("status") == "ERROR")
        passed = (ok >= 2 and fail == 0 and err == 0)
        put(q, f"Resultado UART {chosen.get('id')}: OK {ok} | FAIL {fail} | ERROR {err}\n", "done" if passed else "error")
        if passed:
            put(q, "UART eléctrico: PASS. Las dos líneas TX/RX responden como pareja.\n", "done")
        else:
            put(q, "UART eléctrico: FAIL/ERROR. Revisa cruce TX/RX, GND común, GPIO usado o netlist.\n", "error")
        jobs[job_id]["status"] = "done" if passed else "error"
        put(q, "__DONE__", "done")
    except Exception as e:
        jobs[job_id]["status"] = "error"
        put(q, f"ERROR: {e}\n", "error")
        put(q, "__DONE__", "done")
    finally:
        if sock:
            try: sock.close()
            except Exception: pass
        if proc:
            try: proc.terminate(); proc.wait(timeout=5)
            except Exception:
                try: proc.kill()
                except Exception: pass


@app.route("/api/start-uart-pair", methods=["POST"])
def start_uart_pair_test():
    if "bsdl" not in request.files:
        return jsonify({"ok": False, "error": "No recibí archivo BSDL"}), 400
    uart_id = (request.form.get("uart_id") or "").strip().upper()
    tx_pin = (request.form.get("tx_pin") or "").strip().upper() or None
    rx_pin = (request.form.get("rx_pin") or "").strip().upper() or None
    job_id = str(uuid.uuid4())
    bsdl_file = request.files["bsdl"]
    netlist_file = request.files.get("netlist")
    bsdl_path, netlist_path = save_uploaded_files(job_id, bsdl_file, netlist_file)
    options = {"uut_ref": request.form.get("uut_ref", "U1")}
    jobs[job_id] = {"queue": queue.Queue(), "status": "created", "created_at": time.time(), "proc": None, "filename": bsdl_file.filename, "netlist_filename": netlist_file.filename if netlist_file and netlist_file.filename else None, "options": options, "out_dir": None}
    t = threading.Thread(target=run_uart_pair_job, args=(job_id, bsdl_path, netlist_path, uart_id, tx_pin, rx_pin, options), daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id})

@app.route("/api/start-special-pin", methods=["POST"])
def start_special_pin_test():
    if "bsdl" not in request.files:
        return jsonify({"ok": False, "error": "No recibí archivo BSDL"}), 400
    pin = (request.form.get("pin") or "").strip().upper()
    if not pin:
        return jsonify({"ok": False, "error": "No recibí pin"}), 400
    job_id = str(uuid.uuid4())
    bsdl_file = request.files["bsdl"]
    netlist_file = request.files.get("netlist")
    bsdl_path, netlist_path = save_uploaded_files(job_id, bsdl_file, netlist_file)
    options = {"uut_ref": request.form.get("uut_ref", "U1"), "external_bidir": request.form.get("external_bidir", "false") == "true"}
    jobs[job_id] = {"queue": queue.Queue(), "status": "created", "created_at": time.time(), "proc": None, "filename": bsdl_file.filename, "netlist_filename": netlist_file.filename if netlist_file and netlist_file.filename else None, "options": options, "out_dir": None}
    t = threading.Thread(target=run_external_pin_job, args=(job_id, bsdl_path, netlist_path, pin, options), daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/start-pin", methods=["POST"])
def start_pin_test():
    if "bsdl" not in request.files:
        return jsonify({"ok": False, "error": "No recibí archivo BSDL"}), 400
    pin = (request.form.get("pin") or "").strip().upper()
    if not pin:
        return jsonify({"ok": False, "error": "No recibí pin"}), 400
    job_id = str(uuid.uuid4())
    bsdl_file = request.files["bsdl"]
    netlist_file = request.files.get("netlist")
    bsdl_path, netlist_path = save_uploaded_files(job_id, bsdl_file, netlist_file)
    options = {"uut_ref": request.form.get("uut_ref", "U1")}
    jobs[job_id] = {"queue": queue.Queue(), "status": "created", "created_at": time.time(), "proc": None, "filename": bsdl_file.filename, "netlist_filename": netlist_file.filename if netlist_file and netlist_file.filename else None, "options": options, "out_dir": None}
    t = threading.Thread(target=run_pin_job, args=(job_id, bsdl_path, netlist_path, pin, options), daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id})

@app.route("/api/start", methods=["POST"])
def start_test():
    if "bsdl" not in request.files:
        return jsonify({"ok": False, "error": "No recibí archivo BSDL"}), 400

    bsdl_file = request.files["bsdl"]
    if not bsdl_file.filename:
        return jsonify({"ok": False, "error": "Archivo BSDL vacío"}), 400

    job_id = str(uuid.uuid4())
    netlist_file = request.files.get("netlist")
    bsdl_path, netlist_path = save_uploaded_files(job_id, bsdl_file, netlist_file)

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
