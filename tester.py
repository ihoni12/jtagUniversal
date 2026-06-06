from typing import Dict, List
from bsdl_parser import BsdlInfo, pin_cells
from jtag_openocd import OpenOcdTelnet, parse_hex_from_output, int_to_bits_lsb


def build_safe_vector(info: BsdlInfo) -> int:
    value = 0
    for c in info.boundary:
        bit = 0 if str(c.safe).upper() in {"X", "0", "Z"} else 1
        value |= (bit & 1) << c.index
    return value


def sample_once(info: BsdlInfo, tap: str, host: str, port: int):
    sample = info.instructions.get("SAMPLE") or info.instructions.get("SAMPLE/PRELOAD")
    if not sample:
        raise RuntimeError("El BSDL no contiene opcode SAMPLE o SAMPLE/PRELOAD")
    with OpenOcdTelnet(host, port) as ocd:
        ocd.irscan(tap, sample)
        out = ocd.drscan(tap, info.boundary_length, 0)
    raw = parse_hex_from_output(out)
    bits = int_to_bits_lsb(raw, info.boundary_length)
    return raw, bits


def basic_short_test(info: BsdlInfo, tap: str, host: str, port: int, max_pins: int = 250) -> Dict:
    """
    Best-effort EXTEST short test.
    Drives one output-capable pin at a time and watches input cells of other pins.
    This is safe only for boundary-scan capable pins on an unpowered/no-external-conflict board.
    """
    extest = info.instructions.get("EXTEST")
    preload = info.instructions.get("SAMPLE/PRELOAD") or info.instructions.get("SAMPLE")
    if not extest:
        raise RuntimeError("El BSDL no contiene opcode EXTEST")
    if not preload:
        raise RuntimeError("El BSDL no contiene opcode SAMPLE/PRELOAD")

    cells_by_pin = pin_cells(info)
    testable = [p for p, cs in cells_by_pin.items() if cs["output"] and cs["input"]]
    testable = testable[:max_pins]
    safe = build_safe_vector(info)
    errors: List[Dict] = []

    with OpenOcdTelnet(host, port) as ocd:
        for pin in testable:
            out_cell = cells_by_pin[pin]["output"][0]
            ctrl_cells = cells_by_pin[pin]["control"]
            for drive in (0, 1):
                vec = safe
                # Set output value.
                if drive:
                    vec |= (1 << out_cell.index)
                else:
                    vec &= ~(1 << out_cell.index)
                # Try to enable output when the BSDL gives control cell info.
                for ctrl in ctrl_cells:
                    if ctrl.disval in {"0", "1"}:
                        enable = 1 - int(ctrl.disval)
                        if enable:
                            vec |= (1 << ctrl.index)
                        else:
                            vec &= ~(1 << ctrl.index)
                ocd.irscan(tap, preload)
                ocd.drscan(tap, info.boundary_length, vec)
                ocd.irscan(tap, extest)
                read_out = ocd.drscan(tap, info.boundary_length, vec)
                bits = int_to_bits_lsb(parse_hex_from_output(read_out), info.boundary_length)
                for other, cs in cells_by_pin.items():
                    if other == pin or not cs["input"]:
                        continue
                    ib = cs["input"][0].index
                    if bits[ib] == drive:
                        # One observation is not proof; report as suspect only.
                        errors.append({"driven_pin": pin, "possible_short_to": other, "level": drive})
    return {"tested_pins": len(testable), "suspects": errors[:500], "suspect_count": len(errors)}
