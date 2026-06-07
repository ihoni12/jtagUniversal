import socket
import time
import subprocess
import re
import sys
import os
import json
import argparse
from collections import defaultdict

HOST = "127.0.0.1"
PORT = 4444

# Configuración fija de tu Raspberry Pi
JTAG_TCK = 11
JTAG_TMS = 25
JTAG_TDI = 10
JTAG_TDO = 9
JTAG_SPEED = 10

# Cambia esto si tu netlist usa otro nombre para el micro.
# Ejemplos: U1, IC1, ATMEGA2560, MEGA2560
DEFAULT_UUT_REFS = ["U1", "IC1", "ATMEGA2560", "MEGA2560", "ATMEGA", "ARDUINO"]


def read_file(path):
    with open(path, "r", errors="ignore") as f:
        return f.read()


def clean_bsdl(text):
    text = re.sub(r"--.*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def get_entity_name(text):
    m = re.search(r"entity\s+(\w+)\s+is", text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return "chip"


def get_boundary_length(text):
    m = re.search(r"BOUNDARY_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)", text, re.IGNORECASE)
    if not m:
        raise RuntimeError("No encontré BOUNDARY_LENGTH en el BSDL")
    return int(m.group(1))


def get_instruction_length(text):
    m = re.search(r"INSTRUCTION_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)", text, re.IGNORECASE)
    if not m:
        raise RuntimeError("No encontré INSTRUCTION_LENGTH en el BSDL")
    return int(m.group(1))


def get_instruction_opcode(text, name, default=None):
    # Funciona con: EXTEST (0000), SAMPLE (0010), SAMPLE/PRELOAD (0010)
    pattern = rf"{re.escape(name)}\s*\(\s*([01]+)\s*\)"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return "0x" + format(int(m.group(1), 2), "x")
    return default


def parse_boundary_cells(text):
    """
    Busca celdas tipo:
    27 (BC_1, PA0, output3, X, 26, 1, Z)
    26 (BC_1, *, control, 1)
    12 (BC_1, PB0, input, X)
    """
    cells = {}
    matches = re.findall(r"(\d+)\s*\((.*?)\)", text)

    for bit_str, body in matches:
        bit = int(bit_str)
        parts = [p.strip().strip('"') for p in body.split(",")]
        if len(parts) < 3:
            continue

        cell_type = parts[0]
        port = parts[1]
        function = parts[2].lower()

        cells[bit] = {
            "bit": bit,
            "cell_type": cell_type,
            "port": normalize_pin_name(port),
            "function": function,
            "parts": parts,
        }

    return cells


def normalize_pin_name(name):
    if name is None:
        return ""
    s = str(name).strip().strip('"').strip("'")
    s = s.replace("\\", "")
    return s.upper()


def build_pins_from_cells(cells):
    """
    Devuelve:
    pins[PORT] = {
      output_bit: bit usado para manejar salida,
      input_bit: bit usado para leer,
      control_bit: bit usado para habilitar salida,
    }
    """
    pins = defaultdict(lambda: {"input_bit": None, "output_bit": None, "control_bit": None})

    for bit, cell in cells.items():
        port = normalize_pin_name(cell["port"])
        function = cell["function"]
        parts = cell["parts"]

        if port in ["*", ""]:
            continue

        if function in ["input", "observe_only"]:
            pins[port]["input_bit"] = bit

        if function in ["output3", "bidir", "output2", "output"]:
            pins[port]["output_bit"] = bit

            # En BSDL normal, output3/bidir suele tener control bit en parts[4]
            if len(parts) >= 5:
                try:
                    pins[port]["control_bit"] = int(parts[4])
                except Exception:
                    pass

            # Si no hay input separado, muchos AVR leen el mismo bit de datos.
            if pins[port]["input_bit"] is None:
                pins[port]["input_bit"] = bit

    # Deja sólo pines que se puedan manejar y leer.
    clean = {}
    for port, data in pins.items():
        if data["output_bit"] is not None and data["input_bit"] is not None:
            clean[port] = dict(data)
    return clean


def parse_bsdl(bsdl_path):
    raw = read_file(bsdl_path)
    text = clean_bsdl(raw)

    chipname = get_entity_name(text)
    bits = get_boundary_length(text)
    irlen = get_instruction_length(text)

    extest = get_instruction_opcode(text, "EXTEST", "0x0")
    sample = (
        get_instruction_opcode(text, "SAMPLE", None)
        or get_instruction_opcode(text, "SAMPLE_PRELOAD", None)
        or get_instruction_opcode(text, "SAMPLE/PRELOAD", None)
        or "0x2"
    )
    idcode = get_instruction_opcode(text, "IDCODE", "0x1")

    cells = parse_boundary_cells(text)
    pins = build_pins_from_cells(cells)

    if not pins:
        raise RuntimeError("No pude sacar pines controlables del BSDL")

    return {
        "chipname": chipname,
        "bits": bits,
        "irlen": irlen,
        "extest": extest,
        "sample": sample,
        "idcode": idcode,
        "pins": pins,
        "cells": cells,
    }


# ---------------- NETLIST ----------------

def clean_net_name(name):
    return str(name).strip().strip('"').strip("'")


def normalize_ref(ref):
    return str(ref).strip().strip('"').strip("'").upper()


def normalize_pin_token(pin):
    return normalize_pin_name(pin)


def add_net(nets, net_name, ref, pin, raw=None):
    net = clean_net_name(net_name)
    ref = normalize_ref(ref)
    pin = normalize_pin_token(pin)
    if not net or not ref or not pin:
        return
    nets[net].append({"ref": ref, "pin": pin, "raw": raw or f"{ref}.{pin}"})


def parse_kicad_netlist(text):
    """Soporta netlist s-expression de KiCad exportado con nodos: (node (ref U1) (pin 5))"""
    nets = defaultdict(list)

    # Agarra cada bloque (net ...). Es suficiente para netlists normales.
    for block in re.findall(r"\(net\s+\(code\s+\d+\)\s+\(name\s+([^\)]+)\)(.*?)\n\s*\)", text, re.S | re.I):
        net_name_raw, body = block
        net_name = clean_net_name(net_name_raw)
        for ref, pin in re.findall(r"\(node\s+\(ref\s+([^\)]+)\)\s+\(pin\s+([^\)]+)\)\)", body, re.I):
            add_net(nets, net_name, ref, pin)

    # Variante más laxa si el regex anterior no agarró bien.
    if not nets:
        for m in re.finditer(r"\(net\b", text, re.I):
            start = m.start()
            depth = 0
            end = None
            for i in range(start, len(text)):
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end:
                block = text[start:end]
                mn = re.search(r"\(name\s+([^\)]+)\)", block, re.I)
                if not mn:
                    continue
                net_name = clean_net_name(mn.group(1))
                for ref, pin in re.findall(r"\(node\s+\(ref\s+([^\)]+)\)\s+\(pin\s+([^\)]+)\)\)", block, re.I):
                    add_net(nets, net_name, ref, pin)

    return dict(nets)


def parse_simple_netlist(text):
    """
    Soporta formatos simples como:

    NET_D22
      U1.PA0
      J1.1

    NET SPI_MOSI (
      U1-42
      U5-3
    )

    NET_D23: U1.PA1, J1.2
    """
    nets = defaultdict(list)
    current = None

    for original in text.splitlines():
        line = original.strip()
        if not line or line.startswith("#") or line.startswith(";") or line.startswith("//"):
            continue

        line = re.sub(r"//.*", "", line).strip()

        # Inicio de net: NET_NAME, NET NET_NAME, NET_NAME:, NET_NAME (
        m = re.match(r"^(?:NET\s+)?([A-Za-z0-9_./+\-:$]+)\s*[:(]?\s*(.*)$", line, re.I)
        if m and (
            line.upper().startswith("NET")
            or line.endswith(":")
            or line.endswith("(")
            or (":" in line and not re.match(r"^[A-Za-z0-9_]+[.\-:]", line))
        ):
            name = m.group(1)
            rest = m.group(2).replace("(", "").replace(")", "").strip()
            # Evita tratar una conexión U1.PA0 como nombre de net.
            if not re.match(r"^[A-Za-z]+\d+[.\-:]", name):
                current = clean_net_name(name)
                if rest:
                    for token in re.split(r"[,\s]+", rest):
                        parse_connection_token_into_net(nets, current, token)
                continue

        if line in [")", "("]:
            continue

        if current:
            for token in re.split(r"[,\s]+", line.replace(")", "")):
                parse_connection_token_into_net(nets, current, token)

    return dict(nets)


def parse_connection_token_into_net(nets, net_name, token):
    token = token.strip().strip(",").strip()
    if not token:
        return
    # U1.PA0 / U1-42 / U1:PA0
    m = re.match(r"^([A-Za-z]+[A-Za-z0-9_]*)[.\-:](.+)$", token)
    if m:
        add_net(nets, net_name, m.group(1), m.group(2), raw=token)


def parse_csv_netlist(text):
    """Soporta CSV/TSV con columnas net, ref, pin."""
    import csv
    from io import StringIO

    sample = text[:2000]
    delimiter = "\t" if "\t" in sample and sample.count("\t") > sample.count(",") else ","
    reader = csv.DictReader(StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        return {}

    fields = {f.lower().strip(): f for f in reader.fieldnames}
    net_key = next((fields[k] for k in fields if k in ["net", "net_name", "netname", "signal"]), None)
    ref_key = next((fields[k] for k in fields if k in ["ref", "reference", "component", "designator"]), None)
    pin_key = next((fields[k] for k in fields if k in ["pin", "pad", "terminal"]), None)

    if not (net_key and ref_key and pin_key):
        return {}

    nets = defaultdict(list)
    for row in reader:
        add_net(nets, row.get(net_key, ""), row.get(ref_key, ""), row.get(pin_key, ""))
    return dict(nets)


def parse_netlist(path):
    text = read_file(path)

    parsers = [
        ("kicad", parse_kicad_netlist),
        ("csv", parse_csv_netlist),
        ("simple", parse_simple_netlist),
    ]

    best_name = None
    best = {}
    for name, parser in parsers:
        try:
            nets = parser(text)
            if len(nets) > len(best):
                best = nets
                best_name = name
        except Exception:
            pass

    if not best:
        raise RuntimeError("No pude leer el netlist. Usa formato KiCad, CSV net/ref/pin, o formato simple NET_NAME con U1.PIN")

    return best_name, best


def find_uut_ref_in_netlist(nets, allowed_refs):
    allowed = {normalize_ref(r) for r in allowed_refs}
    refs = defaultdict(int)
    for conns in nets.values():
        for c in conns:
            refs[c["ref"]] += 1

    for ref in allowed:
        if ref in refs:
            return ref

    # Si no coincide, elige el componente con más pines conectados.
    if refs:
        return max(refs.items(), key=lambda x: x[1])[0]
    return None


def build_board_map(nets, bsdl_pins, uut_ref):
    """
    Relaciona el netlist con pines BSDL.
    Devuelve sólo nets donde aparece el chip JTAG y el pin existe en el BSDL.
    """
    mapped = []
    unknown_uut_pins = []

    bsdl_names = set(bsdl_pins.keys())

    for net_name, conns in nets.items():
        uut_conns = [c for c in conns if c["ref"] == uut_ref]
        if not uut_conns:
            continue

        for uc in uut_conns:
            pin = normalize_pin_token(uc["pin"])
            if pin not in bsdl_names:
                unknown_uut_pins.append({"net": net_name, "pin": pin, "raw": uc["raw"]})
                continue

            expected = []
            for c in conns:
                if c is uc:
                    continue
                # Sólo podemos leer/controlar otros pines si también son del mismo chip JTAG.
                if c["ref"] == uut_ref and normalize_pin_token(c["pin"]) in bsdl_names:
                    expected.append(normalize_pin_token(c["pin"]))

            mapped.append({
                "net": net_name,
                "driver": pin,
                "expected_same_chip_pins": sorted(set(expected)),
                "all_connections": conns,
            })

    return mapped, unknown_uut_pins


def build_pin_net_lookup(board_map):
    """
    Crea búsquedas rápidas para saber qué pines BSDL están en la misma NET.

    pin_to_nets["PA0"] = {"NET_D22"}
    same_net_pins["PA0"] = {"PA1", "PA2"}  # sólo si el netlist dice que están juntos
    """
    pin_to_nets = defaultdict(set)
    same_net_pins = defaultdict(set)

    for item in board_map or []:
        net = item["net"]
        pins_in_net = {item["driver"]} | set(item.get("expected_same_chip_pins", []))

        for pin in pins_in_net:
            pin_to_nets[pin].add(net)
            same_net_pins[pin].update(pins_in_net - {pin})

    return pin_to_nets, same_net_pins


def describe_connections(conns):
    return ", ".join(f"{c['ref']}.{c['pin']}" for c in conns)


def print_test_summary(short_report=None, net_report=None):
    print("\n=== RESUMEN COMO SE DEBE ===")

    if short_report is not None:
        counts = defaultdict(int)
        for row in short_report:
            counts[row["status"]] += 1

        print("\nCortos / conexiones detectadas:")
        print(f"  OK sin seguidores extra: {counts['OK_SIN_CORTO']}")
        print(f"  OK porque el netlist permite la conexión: {counts['OK_SEGUN_NETLIST']}")
        print(f"  CORTO SOSPECHOSO no permitido por netlist: {counts['CORTO_SOSPECHOSO']}")

        bad = [r for r in short_report if r["status"] == "CORTO_SOSPECHOSO"]
        if bad:
            print("\nCortos sospechosos reales:")
            for r in bad:
                print(f"  {r['driver']} -> {', '.join(r['unexpected_followers'])}")
                if r.get("driver_nets"):
                    print(f"     Netlist del pin {r['driver']}: {', '.join(r['driver_nets'])}")
                for p, nets in r.get("unexpected_follower_nets", {}).items():
                    print(f"     {p} aparece en netlist como: {', '.join(nets) if nets else 'SIN_NET'}")
        else:
            print("  No hay cortos sospechosos fuera del netlist.")

    if net_report is not None:
        counts = defaultdict(int)
        for r in net_report:
            counts[r["status"]] += 1

        print("\nRevisión contra netlist:")
        for key in ["OK", "OPEN_POSIBLE", "BRIDGE_POSIBLE", "MIXTO", "NO_MEDIBLE_DIRECTO"]:
            print(f"  {key}: {counts[key]}")

        problems = [r for r in net_report if r["status"] in ["OPEN_POSIBLE", "BRIDGE_POSIBLE", "MIXTO"]]
        if problems:
            print("\nProblemas por NET:")
            for r in problems:
                print(f"  {r['net']} desde {r['driver']} -> {r['status']}")
                if r.get("missing"):
                    print(f"     Faltan: {', '.join(r['missing'])}")
                if r.get("extra"):
                    print(f"     Extras/no permitidos: {', '.join(r['extra'])}")
        else:
            print("  No hay fallos medibles contra el netlist.")


# ---------------- OpenOCD/JTAG ----------------

def create_openocd_cfg(chipname, irlen):
    cfg = f"""
interface bcm2835gpio

transport select jtag

adapter speed {JTAG_SPEED}

reset_config none

bcm2835gpio_jtag_nums {JTAG_TCK} {JTAG_TMS} {JTAG_TDI} {JTAG_TDO}

set CHIPNAME {chipname}

jtag newtap $CHIPNAME cpu -irlen {irlen}

init
scan_chain
"""
    path = "/tmp/jtag_auto.cfg"
    with open(path, "w") as f:
        f.write(cfg)
    return path


def recv_all(sock):
    sock.setblocking(False)
    data = b""
    end = time.time() + 1
    while time.time() < end:
        try:
            chunk = sock.recv(4096)
            if chunk:
                data += chunk
                if b">" in chunk:
                    break
            else:
                break
        except BlockingIOError:
            time.sleep(0.03)
    sock.setblocking(True)
    return data.decode(errors="ignore")


def cmd(sock, text):
    sock.sendall((text + "\n").encode())
    time.sleep(0.12)
    return recv_all(sock)


def extract_hex(output):
    matches = re.findall(r"\b[0-9a-fA-F]{8,}\b", output)
    if not matches:
        return None
    best = max(matches, key=len)
    return int(best, 16)


def start_openocd(cfg_path):
    print("Iniciando OpenOCD...")
    proc = subprocess.Popen(["openocd", "-f", cfg_path])
    end = time.time() + 10
    while time.time() < end:
        try:
            sock = socket.create_connection((HOST, PORT), timeout=1)
            print("OpenOCD abierto y conectado.")
            return proc, sock
        except OSError:
            time.sleep(0.3)
    raise RuntimeError("No pude conectarme a OpenOCD en el puerto 4444")


def sample(sock, tap, sample_opcode, bits):
    cmd(sock, f"irscan {tap} {sample_opcode}")
    out = cmd(sock, f"drscan {tap} {bits} 0")
    value = extract_hex(out)
    if value is None:
        print("\nRESPUESTA SAMPLE RAW:")
        print(out)
        raise RuntimeError("No pude leer SAMPLE")
    return value


def extest_write(sock, tap, extest_opcode, bits, value):
    cmd(sock, f"irscan {tap} {extest_opcode}")
    cmd(sock, f"drscan {tap} {bits} 0x{value:x}")
    time.sleep(0.05)


def make_pattern(pins, selected_pin, level):
    value = 0

    # Todos en entrada / alta impedancia si el control existe.
    # OJO: en algunos BSDL el control puede ser invertido. Si ves todo al revés, hay que ajustar control polarity.
    for name, p in pins.items():
        cb = p.get("control_bit")
        if cb is not None:
            value &= ~(1 << cb)

    p = pins[selected_pin]
    data_bit = p["output_bit"]
    control_bit = p.get("control_bit")

    if level:
        value |= (1 << data_bit)
    else:
        value &= ~(1 << data_bit)

    if control_bit is not None:
        value |= (1 << control_bit)

    return value


def read_pin_states(pins, value):
    states = {}
    for name, p in pins.items():
        ib = p["input_bit"]
        states[name] = 1 if (value >> ib) & 1 else 0
    return states


def test_one_pin(sock, tap, extest, sample_opcode, bits, pins, pin):
    high_pattern = make_pattern(pins, pin, 1)
    extest_write(sock, tap, extest, bits, high_pattern)
    high_read = sample(sock, tap, sample_opcode, bits)
    high_states = read_pin_states(pins, high_read)

    low_pattern = make_pattern(pins, pin, 0)
    extest_write(sock, tap, extest, bits, low_pattern)
    low_read = sample(sock, tap, sample_opcode, bits)
    low_states = read_pin_states(pins, low_read)

    followers = []
    stuck_high = []
    stuck_low = []

    for other in pins:
        if other == pin:
            continue
        if high_states[other] == 1 and low_states[other] == 0:
            followers.append(other)
        elif high_states[other] == 1 and low_states[other] == 1:
            stuck_high.append(other)
        elif high_states[other] == 0 and low_states[other] == 0:
            stuck_low.append(other)

    return {
        "pin": pin,
        "followers": sorted(set(followers)),
        "stuck_high": stuck_high,
        "stuck_low": stuck_low,
        "high_read_hex": f"0x{high_read:x}",
        "low_read_hex": f"0x{low_read:x}",
    }


def run_short_test(sock, tap, extest, sample_opcode, bits, pins, board_map=None):
    print("\n=== PRUEBA DE CORTOS VALIDADA CON NETLIST ===")
    print("Si un pin sigue al otro, primero reviso si el netlist dice que esa conexión es correcta.")
    print("Sólo marco CORTO si la conexión NO aparece permitida en el netlist.\n")

    pin_to_nets, same_net_pins = build_pin_net_lookup(board_map)
    results = []

    for pin in pins:
        print(f"Probando {pin}...")
        r = test_one_pin(sock, tap, extest, sample_opcode, bits, pins, pin)
        followers = set(r["followers"])
        allowed = set(same_net_pins.get(pin, set()))

        expected_followers = sorted(followers & allowed)
        unexpected_followers = sorted(followers - allowed)

        if unexpected_followers:
            status = "CORTO_SOSPECHOSO"
            print(f"  [CORTO?] seguidores NO permitidos por netlist: {unexpected_followers}")
            if expected_followers:
                print(f"  [OK NETLIST] seguidores permitidos: {expected_followers}")
        elif expected_followers:
            status = "OK_SEGUN_NETLIST"
            print(f"  [OK NETLIST] conexión esperada detectada: {expected_followers}")
        else:
            status = "OK_SIN_CORTO"
            print("  [OK] no siguió ningún pin extra")

        results.append({
            "driver": pin,
            "status": status,
            "followers": sorted(followers),
            "expected_followers_by_netlist": sorted(allowed),
            "allowed_detected_followers": expected_followers,
            "unexpected_followers": unexpected_followers,
            "driver_nets": sorted(pin_to_nets.get(pin, [])),
            "unexpected_follower_nets": {
                other: sorted(pin_to_nets.get(other, [])) for other in unexpected_followers
            },
            "raw": r,
        })

    return results


def run_netlist_test(sock, tap, extest, sample_opcode, bits, pins, board_map):
    print("\n=== PRUEBA SEGÚN NETLIST ===")
    print("Sólo puedo verificar conexiones donde ambos extremos son pines Boundary Scan del mismo chip.")
    print("Si el otro extremo es resistencia, conector, memoria sin JTAG, etc., queda en el mapa pero no se puede leer directo.\n")

    report = []

    for item in board_map:
        driver = item["driver"]
        expected = set(item["expected_same_chip_pins"])

        # Si la net no tiene otro pin JTAG del mismo chip, no se puede verificar continuidad directa.
        if not expected:
            report.append({
                "net": item["net"],
                "driver": driver,
                "status": "NO_MEDIBLE_DIRECTO",
                "expected": [],
                "observed": [],
                "missing": [],
                "extra": [],
                "connections": item["all_connections"],
            })
            continue

        print(f"Probando net {item['net']} desde {driver}...")
        r = test_one_pin(sock, tap, extest, sample_opcode, bits, pins, driver)
        observed = set(r["followers"])

        missing = sorted(expected - observed)
        extra = sorted(observed - expected)

        if not missing and not extra:
            status = "OK"
            print("  [OK] conexiones esperadas detectadas")
        elif missing and not extra:
            status = "OPEN_POSIBLE"
            print(f"  [OPEN?] no respondieron: {missing}")
        elif extra and not missing:
            status = "BRIDGE_POSIBLE"
            print(f"  [BRIDGE?] respondieron pines no esperados: {extra}")
        else:
            status = "MIXTO"
            print(f"  [MIXTO] faltan {missing}, extras {extra}")

        report.append({
            "net": item["net"],
            "driver": driver,
            "status": status,
            "expected": sorted(expected),
            "observed": sorted(observed),
            "missing": missing,
            "extra": extra,
            "connections": item["all_connections"],
        })

    return report


def save_reports(out_dir, info, nets=None, board_map=None, net_report=None, short_results=None, unknown_uut_pins=None):
    os.makedirs(out_dir, exist_ok=True)

    pins_path = os.path.join(out_dir, "bsdl_pins.json")
    with open(pins_path, "w") as f:
        json.dump(info["pins"], f, indent=2)

    if nets is not None:
        with open(os.path.join(out_dir, "netlist_parsed.json"), "w") as f:
            json.dump(nets, f, indent=2)

    if board_map is not None:
        with open(os.path.join(out_dir, "board_map.json"), "w") as f:
            json.dump(board_map, f, indent=2)

    if unknown_uut_pins is not None:
        with open(os.path.join(out_dir, "unknown_uut_pins.json"), "w") as f:
            json.dump(unknown_uut_pins, f, indent=2)

    if net_report is not None:
        with open(os.path.join(out_dir, "netlist_test_report.json"), "w") as f:
            json.dump(net_report, f, indent=2)

    if short_results is not None:
        with open(os.path.join(out_dir, "short_test_report.json"), "w") as f:
            json.dump(short_results, f, indent=2)

    print(f"\nReportes guardados en: {out_dir}")


def print_netlist_summary(nets, board_map, uut_ref, unknown_uut_pins):
    total_conns = sum(len(c) for c in nets.values())
    measurable = sum(1 for x in board_map if x["expected_same_chip_pins"])
    not_measurable = len(board_map) - measurable

    print("\n=== INFO DEL NETLIST ===")
    print("Nets encontradas:", len(nets))
    print("Conexiones encontradas:", total_conns)
    print("Referencia del chip JTAG usada:", uut_ref)
    print("Nets con pin del chip JTAG:", len(board_map))
    print("Nets medibles directo Bscan-Bscan en el mismo chip:", measurable)
    print("Nets sólo mapeadas, no medibles directo:", not_measurable)
    print("Pines del netlist que no coincidieron con nombres del BSDL:", len(unknown_uut_pins))


def print_netlist_details(nets, limit=0):
    print("\n=== NETLIST PARSEADO / CONEXIONES ENCONTRADAS ===")
    items = sorted(nets.items(), key=lambda x: x[0].upper())
    shown = 0

    for net_name, conns in items:
        if limit and shown >= limit:
            remaining = len(items) - shown
            print(f"... ({remaining} nets más no impresas. Usa --print-limit 0 para imprimir todo)")
            break

        print(f"\n{net_name}")
        for c in conns:
            print(f"  {c['ref']}.{c['pin']}")
        shown += 1


def print_board_map_details(board_map, unknown_uut_pins, limit=0):
    print("\n=== MAPA NETLIST + BSDL ===")
    print("Aquí muestro sólo nets donde aparece el chip JTAG y el pin existe en el BSDL.")

    shown = 0
    for item in sorted(board_map, key=lambda x: x['net'].upper()):
        if limit and shown >= limit:
            remaining = len(board_map) - shown
            print(f"... ({remaining} nets mapeadas más no impresas. Usa --print-limit 0 para imprimir todo)")
            break

        status = "MEDIBLE_DIRECTO" if item["expected_same_chip_pins"] else "SOLO_MAPEADA"
        print(f"\nNET: {item['net']}  [{status}]")
        print(f"  Pin UUT/BSDL: {item['driver']}")

        if item["expected_same_chip_pins"]:
            print("  Otros pines BSDL esperados en la misma net:", ", ".join(item["expected_same_chip_pins"]))
        else:
            print("  Otros pines BSDL esperados en la misma net: ninguno")

        print("  Todas las conexiones del netlist:")
        for c in item["all_connections"]:
            print(f"    {c['ref']}.{c['pin']}")
        shown += 1

    if unknown_uut_pins:
        print("\n=== PINES DEL UUT QUE NO COINCIDEN CON EL BSDL ===")
        print("Estos nombres aparecen en el netlist, pero no existen igual en el BSDL.")
        for x in unknown_uut_pins[:50]:
            print(f"  NET {x['net']}: {x['raw']} -> pin {x['pin']}")
        if len(unknown_uut_pins) > 50:
            print(f"  ... {len(unknown_uut_pins) - 50} más")


def main():
    parser = argparse.ArgumentParser(description="JTAG BSDL + Netlist tester para Raspberry Pi/OpenOCD")
    parser.add_argument("bsdl", help="Archivo BSDL")
    parser.add_argument("netlist", nargs="?", help="Archivo netlist opcional")
    parser.add_argument("--uut-ref", default=None, help="Referencia del chip en el netlist, ejemplo U1 o IC1")
    parser.add_argument("--refs", default=",".join(DEFAULT_UUT_REFS), help="Lista de referencias posibles separadas por coma")
    parser.add_argument("--out", default="jtag_reports", help="Carpeta para guardar reportes JSON")
    parser.add_argument("--map-only", action="store_true", help="Sólo analiza BSDL/netlist y guarda mapas, no abre OpenOCD")
    parser.add_argument("--no-short-test", action="store_true", help="No ejecuta prueba general de cortos")
    parser.add_argument("--netlist-test", action="store_true", help="Ejecuta prueba comparando contra netlist")
    parser.add_argument("--print-netlist", action="store_true", help="Imprime todas las nets y conexiones leídas del netlist")
    parser.add_argument("--print-board-map", action="store_true", help="Imprime cómo se mapeó el netlist contra los pines del BSDL")
    parser.add_argument("--print-limit", type=int, default=0, help="Límite de nets a imprimir. 0 = imprimir todo")
    args = parser.parse_args()

    if not os.path.exists(args.bsdl):
        print("No existe el archivo BSDL:", args.bsdl)
        return

    proc = None
    sock = None
    tap = None
    extest = None
    bits = None

    try:
        info = parse_bsdl(args.bsdl)
        chipname = info["chipname"]
        bits = info["bits"]
        irlen = info["irlen"]
        extest = info["extest"]
        sample_opcode = info["sample"]
        idcode = info["idcode"]
        pins = info["pins"]
        tap = f"{chipname}.cpu"

        print("\n=== INFO DEL BSDL ===")
        print("Chip:", chipname)
        print("TAP:", tap)
        print("IR Length:", irlen)
        print("Boundary Length:", bits)
        print("EXTEST:", extest)
        print("SAMPLE:", sample_opcode)
        print("IDCODE:", idcode)
        print("Pines controlables encontrados:", len(pins))

        nets = None
        board_map = None
        unknown_uut_pins = []
        uut_ref = None

        if args.netlist:
            if not os.path.exists(args.netlist):
                print("No existe el archivo netlist:", args.netlist)
                return
            parser_name, nets = parse_netlist(args.netlist)
            refs = [r.strip() for r in args.refs.split(",") if r.strip()]
            uut_ref = normalize_ref(args.uut_ref) if args.uut_ref else find_uut_ref_in_netlist(nets, refs)
            if not uut_ref:
                raise RuntimeError("No pude encontrar la referencia del chip en el netlist. Usa --uut-ref U1")
            board_map, unknown_uut_pins = build_board_map(nets, pins, uut_ref)
            print("Parser de netlist usado:", parser_name)
            print_netlist_summary(nets, board_map, uut_ref, unknown_uut_pins)

            if args.print_netlist:
                print_netlist_details(nets, limit=args.print_limit)

            if args.print_board_map:
                print_board_map_details(board_map, unknown_uut_pins, limit=args.print_limit)

        if args.map_only:
            save_reports(args.out, info, nets=nets, board_map=board_map, unknown_uut_pins=unknown_uut_pins)
            return

        cfg_path = create_openocd_cfg(chipname, irlen)
        proc, sock = start_openocd(cfg_path)
        recv_all(sock)

        print("\n=== SCAN CHAIN ===")
        print(cmd(sock, "scan_chain"))

        print("Leyendo IDCODE...")
        cmd(sock, f"irscan {tap} {idcode}")
        print(cmd(sock, f"drscan {tap} 32 0"))

        print("Lectura base SAMPLE...")
        base = sample(sock, tap, sample_opcode, bits)
        print(f"BASE = 0x{base:x}")

        short_results = None
        if not args.no_short_test:
            short_results = run_short_test(sock, tap, extest, sample_opcode, bits, pins, board_map)

        net_report = None
        if args.netlist and args.netlist_test:
            net_report = run_netlist_test(sock, tap, extest, sample_opcode, bits, pins, board_map)

        extest_write(sock, tap, extest, bits, 0)

        print_test_summary(short_results, net_report)

        save_reports(
            args.out,
            info,
            nets=nets,
            board_map=board_map,
            net_report=net_report,
            short_results=short_results,
            unknown_uut_pins=unknown_uut_pins,
        )

    except Exception as e:
        print("\n[ERROR]")
        print(e)

    finally:
        if sock:
            try:
                if tap and extest and bits:
                    extest_write(sock, tap, extest, bits, 0)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

        if proc:
            print("\nCerrando OpenOCD...")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
