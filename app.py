import os
import tempfile
from flask import Flask, request, render_template_string
from bsdl_parser import parse_bsdl, pin_cells
from tester import sample_once, extest_short_test, verify_idcode
from jtag_openocd import OpenOcdProcess

app = Flask(__name__)

HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>JTAG BSDL Tester</title>
<style>
body{font-family:Arial,sans-serif;background:#f6f8fb;margin:0;padding:30px;color:#172033}.card{max-width:960px;margin:auto;background:white;border-radius:18px;padding:24px;box-shadow:0 8px 28px #0001}button{background:#1b5cff;color:white;border:0;border-radius:12px;padding:12px 18px;font-weight:bold}input,select{padding:10px;border:1px solid #ccd3df;border-radius:10px;margin:6px 0;width:100%;box-sizing:border-box}.ok{color:#087a3d}.bad{color:#b00020}.log{white-space:pre-wrap;background:#0d1117;color:#d6e2ff;padding:14px;border-radius:12px;max-height:520px;overflow:auto}.small{color:#667085;font-size:14px}.warn{background:#fff7df;padding:10px;border-radius:10px}</style>
</head>
<body><div class="card">
<h1>JTAG BSDL Tester</h1>
<p class="small">Primero verifica IDCODE. Después puede hacer SAMPLE o EXTEST diferencial para detectar cortos sospechosos.</p>
<div class="warn">Importante: sin netlist, el resultado dice <b>sospecha</b>. Puede haber pines conectados de forma normal en la placa.</div>
<form method="post" enctype="multipart/form-data">
<label>Archivo BSDL</label><input type="file" name="bsdl" required>
<label>Nombre TAP de OpenOCD</label><input name="tap" value="avr.cpu">
<label>OpenOCD host</label><input name="host" value="127.0.0.1">
<label>OpenOCD telnet port</label><input name="port" value="4444">
<label>Acción</label><select name="action"><option value="idcode">1. Verificar conexión IDCODE</option><option value="sample">2. Leer SAMPLE</option><option value="shorts">3. Revisar cortos sospechosos EXTEST</option><option value="parse">Solo leer BSDL</option></select>
<label>Máximo de pines a probar en EXTEST</label><input name="max_pins" value="250">
<button>Empezar revisión</button>
</form>
{% if result %}<h2>Resultado</h2><div class="log">{{ result }}</div>{% endif %}
</div></body></html>
"""

@app.route('/', methods=['GET', 'POST'])
def index():
    result = ""
    if request.method == 'POST':
        try:
            f = request.files['bsdl']
            text = f.read().decode(errors='ignore')
            info = parse_bsdl(text)
            cells = pin_cells(info)
            lines = []
            lines.append("Empezó la revisión")
            lines.append(f"Chip/Entity: {info.entity}")
            lines.append(f"Instruction length: {info.instruction_length}")
            lines.append(f"Boundary length: {info.boundary_length}")
            lines.append(f"Pines con celdas encontrados: {len(cells)}")
            lines.append("Instrucciones encontradas: " + ", ".join(sorted(info.instructions.keys())))
            action = request.form.get('action')
            tap = request.form.get('tap', 'avr.cpu')
            host = request.form.get('host', '127.0.0.1')
            port = int(request.form.get('port', '4444'))
            max_pins = int(request.form.get('max_pins', '250'))

            if action in {'idcode', 'sample', 'shorts'}:
                lines.append("Verificando conexión real JTAG con IDCODE...")
                idres = verify_idcode(info, tap, host, port)
                lines.append(f"IDCODE OK: 0x{idres['idcode']:08x}")

            if action == 'sample':
                raw, bits = sample_once(info, tap, host, port)
                lines.append(f"SAMPLE OK. DR raw: 0x{raw:x}")
                lines.append("Primeros 64 bits LSB: " + "".join(str(b) for b in bits[:64]))
            elif action == 'shorts':
                lines.append("Ejecutando EXTEST diferencial: cada pin sale 0 y luego 1, leyendo los demás...")
                res = extest_short_test(info, tap, host, port, max_pins=max_pins)
                lines.append(f"Pines manejables probados: {res['tested_pins']}")
                lines.append(f"Pines observables: {res['observable_pins']}")
                lines.append(f"Sospechas de corto: {res['suspect_count']}")
                for s in res['suspects'][:120]:
                    lines.append(f"POSIBLE CORTO: {s['driven_pin']} <-> {s['possible_short_to']}")
                if res['suspect_count'] == 0:
                    lines.append("No se encontraron cortos sospechosos en esta prueba.")
            lines.append("Terminó la revisión")
            result = "\n".join(lines)
        except Exception as e:
            result = "ERROR: " + str(e)
    return render_template_string(HTML, result=result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8088, debug=False)
