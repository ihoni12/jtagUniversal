#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Lector simple de archivos BSDL.
Uso:
    python bsdl_reader.py archivo.bsdl

Imprime la informacion separada:
- Chip / Entity
- Puertos y pines
- Instrucciones JTAG
- IDCODE
- Boundary register
- Otros atributos BSDL
"""

import re
import sys
from pathlib import Path


def remove_comments(text: str) -> str:
    # En BSDL/VHDL los comentarios empiezan con --
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

    # Separar por ;, cada linea suele ser: PINES : in bit;
    parts = [p.strip() for p in port_block.split(";") if p.strip()]
    for p in parts:
        m = re.match(r"(.+?)\s*:\s*(inout|in|out|linkage|buffer)\s+(.+)$", p, re.IGNORECASE)
        if not m:
            continue
        names_raw, direction, pin_type = m.groups()
        names = [x.strip() for x in names_raw.split(",") if x.strip()]
        for name in names:
            ports.append({
                "name": name,
                "direction": direction.lower(),
                "type": clean_spaces(pin_type),
            })
    return ports


def extract_attribute(text: str, attr_name: str):
    # Captura: attribute X of Y : entity is "...";
    # Tambien funciona con strings largos concatenados.
    pattern = rf"attribute\s+{re.escape(attr_name)}\s+of\s+.*?\s+is\s+(.*?);"
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    value = m.group(1).strip()
    return value


def bsdl_string_to_text(value: str):
    # Junta todos los textos entre comillas de atributos BSDL
    chunks = re.findall(r'"(.*?)"', value, re.DOTALL)
    if chunks:
        return "".join(chunks)
    return value.strip()


def parse_instruction_opcode(value: str):
    txt = bsdl_string_to_text(value)
    result = []
    # Ejemplo: "EXTEST (0000), SAMPLE (0010), BYPASS (1111)"
    for name, code in re.findall(r"([A-Za-z0-9_/]+)\s*\(([^)]+)\)", txt):
        result.append((name.strip(), code.strip()))
    return result, txt


def parse_boundary_register(value: str):
    txt = bsdl_string_to_text(value)
    rows = []

    # Formato comun:
    # 0 (BC_1, PORT, input, X),
    # 1 (BC_1, *, control, 1)
    pattern = r"(\d+)\s*\((.*?)\)"
    for num, inside in re.findall(pattern, txt, re.DOTALL):
        cols = [clean_spaces(c) for c in inside.split(",")]
        rows.append({
            "cell": int(num),
            "raw": inside.strip(),
            "columns": cols,
        })
    rows.sort(key=lambda x: x["cell"])
    return rows, txt


def parse_idcode(value: str):
    txt = bsdl_string_to_text(value)
    bits = re.sub(r"[^01Xx]", "", txt)
    return bits if bits else txt


def print_title(title: str):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    if len(sys.argv) != 2:
        print("Uso: python bsdl_reader.py archivo.bsdl")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ERROR: No existe el archivo: {path}")
        sys.exit(1)

    raw = path.read_text(encoding="utf-8", errors="ignore")
    text = remove_comments(raw)
    entity = find_entity(text)

    print_title("1) CHIP / ENTITY")
    print(f"Archivo: {path.name}")
    print(f"Entity / Chip: {entity}")

    print_title("2) PUERTOS / PINES DECLARADOS")
    ports = parse_ports(extract_port_block(text))
    if ports:
        print(f"Total puertos encontrados: {len(ports)}\n")
        print(f"{'#':<5} {'Nombre':<28} {'Direccion':<12} {'Tipo'}")
        print("-" * 70)
        for i, p in enumerate(ports, 1):
            print(f"{i:<5} {p['name']:<28} {p['direction']:<12} {p['type']}")
    else:
        print("No se encontraron puertos.")

    print_title("3) INSTRUCCIONES JTAG")
    instr_len = extract_attribute(text, "INSTRUCTION_LENGTH")
    instr_opcode = extract_attribute(text, "INSTRUCTION_OPCODE")
    instr_capture = extract_attribute(text, "INSTRUCTION_CAPTURE")
    instr_private = extract_attribute(text, "INSTRUCTION_PRIVATE")

    print("Instruction length:", bsdl_string_to_text(instr_len) if instr_len else "NO ENCONTRADO")
    if instr_capture:
        print("Instruction capture:", bsdl_string_to_text(instr_capture))
    if instr_private:
        print("Instruction private:", bsdl_string_to_text(instr_private))

    parsed_instr, raw_instr = parse_instruction_opcode(instr_opcode)
    if parsed_instr:
        print("\nInstrucciones encontradas:")
        print(f"{'Nombre':<25} {'Codigo binario'}")
        print("-" * 45)
        for name, code in parsed_instr:
            print(f"{name:<25} {code}")
    else:
        print("\nNo se encontraron instrucciones en INSTRUCTION_OPCODE.")
        if raw_instr:
            print(raw_instr)

    print_title("4) IDCODE")
    idcode = extract_attribute(text, "IDCODE_REGISTER")
    if idcode:
        print(parse_idcode(idcode))
    else:
        print("No se encontro IDCODE_REGISTER.")

    print_title("5) BOUNDARY REGISTER")
    boundary_len = extract_attribute(text, "BOUNDARY_LENGTH")
    boundary_reg = extract_attribute(text, "BOUNDARY_REGISTER")
    print("Boundary length:", bsdl_string_to_text(boundary_len) if boundary_len else "NO ENCONTRADO")

    rows, raw_boundary = parse_boundary_register(boundary_reg)
    if rows:
        print(f"Celdas encontradas: {len(rows)}\n")
        print(f"{'Cell':<6} {'Tipo celda':<12} {'Pin/Señal':<22} {'Funcion':<14} {'Valor/Extra'}")
        print("-" * 80)
        for r in rows:
            cols = r["columns"]
            cell_type = cols[0] if len(cols) > 0 else ""
            pin = cols[1] if len(cols) > 1 else ""
            function = cols[2] if len(cols) > 2 else ""
            extra = ", ".join(cols[3:]) if len(cols) > 3 else ""
            print(f"{r['cell']:<6} {cell_type:<12} {pin:<22} {function:<14} {extra}")
    else:
        print("No se pudo separar BOUNDARY_REGISTER.")
        if raw_boundary:
            print(raw_boundary[:2000])

    print_title("6) OTROS ATRIBUTOS IMPORTANTES")
    attrs = [
        "TAP_SCAN_IN", "TAP_SCAN_OUT", "TAP_SCAN_MODE", "TAP_SCAN_CLOCK",
        "TAP_SCAN_RESET", "COMPLIANCE_PATTERNS", "REGISTER_ACCESS",
        "BOUNDARY_CELLS", "DESIGN_WARNING", "COMPONENT_CONFORMANCE"
    ]
    found = False
    for attr in attrs:
        val = extract_attribute(text, attr)
        if val:
            found = True
            print(f"\n[{attr}]")
            print(bsdl_string_to_text(val))
    if not found:
        print("No se encontraron otros atributos de la lista.")


if __name__ == "__main__":
    main()
