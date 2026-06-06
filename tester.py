from typing import Dict, List, Tuple
from bsdl_parser import BsdlInfo, pin_cells
from jtag_openocd import OpenOcdTelnet, parse_hex_from_output, int_to_bits_lsb


def get_instruction(info: BsdlInfo, *names: str) -> str:
    for n in names:
        k = n.upper().replace("-", "_")
        if k in info.instructions:
            return info.instructions[k]
    raise RuntimeError("El BSDL no contiene opcode: " + " / ".join(names))


def build_safe_vector(info: BsdlInfo) -> int:
    value = 0
    for c in info.boundary:
        s = str(c.safe).strip().upper()
        bit = 1 if s == "1" else 0
        value |= bit << c.index
    return value


def set_pin_drive_vector(base: int, out_cell, ctrl_cells, drive: int) -> int:
    vec = base
    if drive:
        vec |= (1 << out_cell.index)
    else:
        vec &= ~(1 << out_cell.index)
    for ctrl in ctrl_cells:
        # disval means value that DISABLES output. To enable, use opposite.
        if ctrl.disval in {"0", "1"}:
            enable = 1 - int(ctrl.disval)
            if enable:
                vec |= (1 << ctrl.index)
            else:
                vec &= ~(1 << ctrl.index)
    return vec


def verify_idcode(info: BsdlInfo, tap: str, host: str, port: int) -> Dict:
    idcode = get_instruction(info, "IDCODE")
    with OpenOcdTelnet(host, port) as ocd:
        chain = ocd.cmd("scan_chain")
        ocd.irscan(tap, idcode)
        out = ocd.drscan(tap, 32, 0)
    raw = parse_hex_from_output(out)
    if raw == 0 or raw == 0xFFFFFFFF:
        raise RuntimeError("JTAG no responde bien. IDCODE leído: 0x%08x. Revisa cables/alimentación/fuse." % raw)
    return {"idcode": raw, "scan_chain": chain, "raw_output": out}


def sample_once(info: BsdlInfo, tap: str, host: str, port: int):
    sample = get_instruction(info, "SAMPLE", "SAMPLE/PRELOAD", "SAMPLE_PRELOAD")
    with OpenOcdTelnet(host, port) as ocd:
        ocd.irscan(tap, sample)
        out = ocd.drscan(tap, info.boundary_length, 0)
    raw = parse_hex_from_output(out)
    bits = int_to_bits_lsb(raw, info.boundary_length)
    return raw, bits


def extest_short_test(info: BsdlInfo, tap: str, host: str, port: int, max_pins: int = 250) -> Dict:
    """
    Real EXTEST-style differential test:
    1) preload safe vector
    2) for each output-capable pin, drive 0 and read inputs
    3) drive 1 and read inputs
    4) a suspected short is reported only if another input follows the driven change.

    This still reports SUSPECTS, not guaranteed shorts, because without a board netlist the
    software does not know which pins are intentionally connected.
    """
    extest = get_instruction(info, "EXTEST")
    preload = get_instruction(info, "SAMPLE_PRELOAD", "SAMPLE/PRELOAD", "SAMPLE")

    cells_by_pin = pin_cells(info)
    testable = [p for p, cs in cells_by_pin.items() if cs["output"]]
    observable = {p: cs["input"][0] for p, cs in cells_by_pin.items() if cs["input"]}
    testable = testable[:max_pins]
    safe = build_safe_vector(info)
    suspects: List[Dict] = []
    tested_detail: List[str] = []

    with OpenOcdTelnet(host, port) as ocd:
        ocd.irscan(tap, preload)
        ocd.drscan(tap, info.boundary_length, safe)
        ocd.irscan(tap, extest)

        for pin in testable:
            cs = cells_by_pin[pin]
            out_cell = cs["output"][0]
            ctrl_cells = cs["control"]

            vec0 = set_pin_drive_vector(safe, out_cell, ctrl_cells, 0)
            ocd.irscan(tap, preload)
            ocd.drscan(tap, info.boundary_length, vec0)
            ocd.irscan(tap, extest)
            read0 = ocd.drscan(tap, info.boundary_length, vec0)
            bits0 = int_to_bits_lsb(parse_hex_from_output(read0), info.boundary_length)

            vec1 = set_pin_drive_vector(safe, out_cell, ctrl_cells, 1)
            ocd.irscan(tap, preload)
            ocd.drscan(tap, info.boundary_length, vec1)
            ocd.irscan(tap, extest)
            read1 = ocd.drscan(tap, info.boundary_length, vec1)
            bits1 = int_to_bits_lsb(parse_hex_from_output(read1), info.boundary_length)

            followers = []
            for other, ib in observable.items():
                if other == pin:
                    continue
                if bits0[ib.index] == 0 and bits1[ib.index] == 1:
                    followers.append(other)
                    suspects.append({"driven_pin": pin, "possible_short_to": other})
            tested_detail.append(f"{pin}: {len(followers)} sospechas")

        # Return to SAMPLE/PRELOAD safe state.
        ocd.irscan(tap, preload)
        ocd.drscan(tap, info.boundary_length, safe)

    return {
        "tested_pins": len(testable),
        "observable_pins": len(observable),
        "suspects": suspects[:1000],
        "suspect_count": len(suspects),
        "tested_detail": tested_detail[:200],
    }
