import socket
import time
import subprocess
import re
import sys
import os

HOST = "127.0.0.1"
PORT = 4444

# Configuracion fija de tu Raspberry Pi
JTAG_TCK = 11
JTAG_TMS = 25
JTAG_TDI = 10
JTAG_TDO = 9
JTAG_SPEED = 10


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
    m = re.search(
        r"BOUNDARY_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)",
        text,
        re.IGNORECASE,
    )
    if not m:
        raise RuntimeError("No encontre BOUNDARY_LENGTH en el BSDL")
    return int(m.group(1))


def get_instruction_length(text):
    m = re.search(
        r"INSTRUCTION_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)",
        text,
        re.IGNORECASE,
    )
    if not m:
        raise RuntimeError("No encontre INSTRUCTION_LENGTH en el BSDL")
    return int(m.group(1))


def get_instruction_opcode(text, name, default=None):
    # Busca: EXTEST (0000)
    pattern = rf"{re.escape(name)}\s*\(\s*([01]+)\s*\)"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return "0x" + format(int(m.group(1), 2), "x")
    return default


def parse_boundary_cells(text):
    cells = {}
    matches = re.findall(r"(\d+)\s*\((.*?)\)", text)

    for bit_str, body in matches:
        bit = int(bit_str)
        parts = [p.strip() for p in body.split(",")]

        if len(parts) < 3:
            continue

        cells[bit] = {
            "bit": bit,
            "cell_type": parts[0],
            "port": parts[1],
            "function": parts[2].lower(),
            "parts": parts,
        }

    return cells


def build_pins_from_cells(cells):
    pins = {}

    for bit, cell in cells.items():
        port = cell["port"]
        function = cell["function"]
        parts = cell["parts"]

        if port == "*" or port == "":
            continue

        control_bit = None

        if function in ["output3", "bidir", "output2", "output"]:
            if len(parts) >= 5:
                try:
                    control_bit = int(parts[4])
                except Exception:
                    control_bit = None

            if control_bit is not None:
                pins[port] = (bit, control_bit)

    return pins


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
    }


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
    print("Iniciando OpenOCD...", flush=True)

    proc = subprocess.Popen(["openocd", "-f", cfg_path])

    end = time.time() + 10

    while time.time() < end:
        try:
            sock = socket.create_connection((HOST, PORT), timeout=1)
            print("OpenOCD abierto y conectado.", flush=True)
            return proc, sock
        except OSError:
            time.sleep(0.3)

    raise RuntimeError("No pude conectarme a OpenOCD en el puerto 4444")


def sample(sock, tap, sample_opcode, bits):
    cmd(sock, f"irscan {tap} {sample_opcode}")
    out = cmd(sock, f"drscan {tap} {bits} 0")

    value = extract_hex(out)

    if value is None:
        print("\nRESPUESTA SAMPLE RAW:", flush=True)
        print(out, flush=True)
        raise RuntimeError("No pude leer SAMPLE")

    return value


def extest_write(sock, tap, extest_opcode, bits, value):
    cmd(sock, f"irscan {tap} {extest_opcode}")
    cmd(sock, f"drscan {tap} {bits} 0x{value:x}")
    time.sleep(0.05)


def make_pattern(pins, selected_pin, level):
    value = 0

    for name, (data_bit, control_bit) in pins.items():
        value &= ~(1 << control_bit)

    data_bit, control_bit = pins[selected_pin]

    if level:
        value |= (1 << data_bit)
    else:
        value &= ~(1 << data_bit)

    value |= (1 << control_bit)
    return value


def read_pin_states(pins, value):
    states = {}

    for name, (data_bit, control_bit) in pins.items():
        states[name] = 1 if (value >> data_bit) & 1 else 0

    return states


def main():
    if len(sys.argv) < 2:
        print("Uso:", flush=True)
        print("sudo python3 mega_jtag_bsdl_test.py archivo.bsdl", flush=True)
        return

    bsdl_path = sys.argv[1]

    if not os.path.exists(bsdl_path):
        print("No existe el archivo: " + bsdl_path, flush=True)
        return

    proc = None
    sock = None
    tap = None
    extest = None
    bits = None

    try:
        info = parse_bsdl(bsdl_path)

        chipname = info["chipname"]
        bits = info["bits"]
        irlen = info["irlen"]
        extest = info["extest"]
        sample_opcode = info["sample"]
        idcode = info["idcode"]
        pins = info["pins"]

        tap = f"{chipname}.cpu"

        print("\n=== INFO DEL BSDL ===", flush=True)
        print("Chip: " + chipname, flush=True)
        print("TAP: " + tap, flush=True)
        print("IR Length: " + str(irlen), flush=True)
        print("Boundary Length: " + str(bits), flush=True)
        print("EXTEST: " + extest, flush=True)
        print("SAMPLE: " + sample_opcode, flush=True)
        print("IDCODE: " + idcode, flush=True)
        print("Pines controlables encontrados: " + str(len(pins)), flush=True)

        cfg_path = create_openocd_cfg(chipname, irlen)
        proc, sock = start_openocd(cfg_path)

        recv_all(sock)

        print("\n=== SCAN CHAIN ===", flush=True)
        print(cmd(sock, "scan_chain"), flush=True)

        print("Leyendo IDCODE...", flush=True)
        cmd(sock, f"irscan {tap} {idcode}")
        print(cmd(sock, f"drscan {tap} 32 0"), flush=True)

        print("Lectura base SAMPLE...", flush=True)
        base = sample(sock, tap, sample_opcode, bits)
        print(f"BASE = 0x{base:x}", flush=True)

        print("\n=== PRUEBA DE POSIBLES CORTOS ===", flush=True)
        print("OJO: si hay pines flotando puede haber falsos positivos.\n", flush=True)

        results = []
        total = len(pins)

        for index, pin in enumerate(pins, start=1):
            print(f"[{index}/{total}] Probando {pin}...", flush=True)

            high_pattern = make_pattern(pins, pin, 1)
            extest_write(sock, tap, extest, bits, high_pattern)
            high_read = sample(sock, tap, sample_opcode, bits)
            high_states = read_pin_states(pins, high_read)

            low_pattern = make_pattern(pins, pin, 0)
            extest_write(sock, tap, extest, bits, low_pattern)
            low_read = sample(sock, tap, sample_opcode, bits)
            low_states = read_pin_states(pins, low_read)

            suspects = []

            for other in pins:
                if other == pin:
                    continue

                if high_states[other] == 1 and low_states[other] == 0:
                    suspects.append(other)

            if suspects:
                print(f"  [SOSPECHA] {pin} puede estar conectado/corto con: {suspects}", flush=True)
                results.append((pin, suspects))
            else:
                print(f"  [OK] {pin}", flush=True)

        extest_write(sock, tap, extest, bits, 0)

        print("\n=== RESUMEN ===", flush=True)

        if not results:
            print("No se detectaron cortos fuertes entre los pines probados.", flush=True)
        else:
            for pin, suspects in results:
                print(f"{pin} sospechoso con {', '.join(suspects)}", flush=True)

    except Exception as e:
        print("\n[ERROR]", flush=True)
        print(e, flush=True)

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
            print("\nCerrando OpenOCD...", flush=True)
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
