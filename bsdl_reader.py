#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
from pathlib import Path


def remove_comments(text: str) -> str:
    return re.sub(r"--.*", "", text)


def clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def find_entity(text: str):
    m = re.search(r"\bentity\s+(\w+)\s+is\b", text, re.IGNORECASE)
    return m.group(1) if m else "NO ENCONTRADO"


def extract_port_block(text: str):
    m = re.search(r"\bport\s*\((.*?)\)\s*;", text, re.IGNORECASE | re.DOTALL)
    return m.group(1) if m else ""


def parse_ports(port_block: str):
    ports = []
    if not port_block:
        return ports
    parts = [p.strip() for p in port_block.split(";") if p.strip()]
    for p in parts:
        m = re.match(r"(.+?)\s*:\s*(inout|in|out|linkage|buffer)\s+(.+)$", p, re.IGNORECASE)
        if not m:
            continue
        names_raw, direction, pin_type = m.groups()
        names = [x.strip() for x in names_raw.split(",") if x.strip()]
        for name in names:
            ports.append({"name": name, "direction": direction.lower(), "type": clean_spaces(pin_type)})
    return ports


def extract_attribute(text: str, attr_name: str):
    pattern = rf"attribute\s+{re.escape(attr_name)}\s+of\s+.*?\s+is\s+(.*?);"
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def bsdl_string_to_text(value: str):
    chunks = re.findall(r'"(.*?)"', value, re.DOTALL)
    if chunks:
        return "".join(chunks)
    return clean_spaces(value)


def parse_instruction_opcode(value: str):
    txt = bsdl_string_to_text(value)
    result = []
    for name, code in re.findall(r"([A-Za-z0-9_/]+)\s*\(([^)]+)\)", txt):
        result.append({"name": name.strip(), "code": code.strip()})
    return result, txt


def parse_boundary_register(value: str):
    txt = bsdl_string_to_text(value)
    rows = []
    for num, inside in re.findall(r"(\d+)\s*\((.*?)\)", txt, re.DOTALL):
        cols = [clean_spaces(c) for c in inside.split(",")]
        rows.append({
            "cell": int(num),
            "cell_type": cols[0] if len(cols) > 0 else "",
            "pin_signal": cols[1] if len(cols) > 1 else "",
            "function": cols[2] if len(cols) > 2 else "",
            "extra": ", ".join(cols[3:]) if len(cols) > 3 else "",
            "columns": cols,
            "raw": inside.strip(),
        })
    rows.sort(key=lambda x: x["cell"])
    return rows, txt


def parse_idcode(value: str):
    txt = bsdl_string_to_text(value)
    bits = re.sub(r"[^01Xx]", "", txt)
    return bits if bits else txt


def parse_bsdl_text(raw: str, filename: str = "archivo.bsdl"):
    text = remove_comments(raw)
    entity = find_entity(text)
    ports = parse_ports(extract_port_block(text))

    instr_len = bsdl_string_to_text(extract_attribute(text, "INSTRUCTION_LENGTH")) or "NO ENCONTRADO"
    instr_capture = bsdl_string_to_text(extract_attribute(text, "INSTRUCTION_CAPTURE"))
    instr_private = bsdl_string_to_text(extract_attribute(text, "INSTRUCTION_PRIVATE"))
    instructions, raw_instr = parse_instruction_opcode(extract_attribute(text, "INSTRUCTION_OPCODE"))

    idcode = parse_idcode(extract_attribute(text, "IDCODE_REGISTER")) or "NO ENCONTRADO"
    boundary_len = bsdl_string_to_text(extract_attribute(text, "BOUNDARY_LENGTH")) or "NO ENCONTRADO"
    boundary_rows, raw_boundary = parse_boundary_register(extract_attribute(text, "BOUNDARY_REGISTER"))

    attrs = [
        "TAP_SCAN_IN", "TAP_SCAN_OUT", "TAP_SCAN_MODE", "TAP_SCAN_CLOCK",
        "TAP_SCAN_RESET", "COMPLIANCE_PATTERNS", "REGISTER_ACCESS",
        "BOUNDARY_CELLS", "DESIGN_WARNING", "COMPONENT_CONFORMANCE"
    ]
    other_attrs = []
    for attr in attrs:
        val = extract_attribute(text, attr)
        if val:
            other_attrs.append({"name": attr, "value": bsdl_string_to_text(val)})

    return {
        "filename": filename,
        "entity": entity,
        "ports": ports,
        "instruction_length": instr_len,
        "instruction_capture": instr_capture,
        "instruction_private": instr_private,
        "instructions": instructions,
        "raw_instructions": raw_instr,
        "idcode": idcode,
        "boundary_length": boundary_len,
        "boundary_rows": boundary_rows,
        "raw_boundary": raw_boundary,
        "other_attrs": other_attrs,
        "summary": {
            "ports_count": len(ports),
            "instructions_count": len(instructions),
            "boundary_cells_count": len(boundary_rows),
        }
    }


def parse_bsdl_file(path: Path):
    raw = path.read_text(encoding="utf-8", errors="ignore")
    return parse_bsdl_text(raw, path.name)


def print_title(title: str):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def print_report(data):
    print_title("1) CHIP / ENTITY")
    print(f"Archivo: {data['filename']}")
    print(f"Entity / Chip: {data['entity']}")

    print_title("2) PUERTOS / PINES DECLARADOS")
    print(f"Total puertos encontrados: {len(data['ports'])}\n")
    print(f"{'#':<5} {'Nombre':<28} {'Direccion':<12} {'Tipo'}")
    print("-" * 70)
    for i, p in enumerate(data['ports'], 1):
        print(f"{i:<5} {p['name']:<28} {p['direction']:<12} {p['type']}")

    print_title("3) INSTRUCCIONES JTAG")
    print("Instruction length:", data['instruction_length'])
    if data['instruction_capture']:
        print("Instruction capture:", data['instruction_capture'])
    if data['instruction_private']:
        print("Instruction private:", data['instruction_private'])
    print(f"\n{'Nombre':<25} {'Codigo binario'}")
    print("-" * 45)
    for ins in data['instructions']:
        print(f"{ins['name']:<25} {ins['code']}")

    print_title("4) IDCODE")
    print(data['idcode'])

    print_title("5) BOUNDARY REGISTER")
    print("Boundary length:", data['boundary_length'])
    print(f"Celdas encontradas: {len(data['boundary_rows'])}\n")
    print(f"{'Cell':<6} {'Tipo celda':<12} {'Pin/Señal':<22} {'Funcion':<14} {'Valor/Extra'}")
    print("-" * 80)
    for r in data['boundary_rows']:
        print(f"{r['cell']:<6} {r['cell_type']:<12} {r['pin_signal']:<22} {r['function']:<14} {r['extra']}")

    print_title("6) OTROS ATRIBUTOS IMPORTANTES")
    if not data['other_attrs']:
        print("No se encontraron otros atributos importantes.")
    for a in data['other_attrs']:
        print(f"\n[{a['name']}]\n{a['value']}")


def main():
    if len(sys.argv) != 2:
        print("Uso: python3 bsdl_reader.py archivo.bsdl")
        sys.exit(1)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ERROR: No existe el archivo: {path}")
        sys.exit(1)
    print_report(parse_bsdl_file(path))


if __name__ == "__main__":
    main()
