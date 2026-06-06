import re
from pathlib import Path
from flask import Flask, request, render_template_string

app = Flask(__name__)

HTML = r'''
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lector BSDL</title>
  <style>
    body{font-family:Arial, sans-serif;background:#f3f6fb;margin:0;color:#1f2937}
    header{background:#16243a;color:white;padding:18px 24px}
    main{max-width:1200px;margin:20px auto;padding:0 14px}
    .card{background:white;border-radius:14px;padding:18px;margin:14px 0;box-shadow:0 2px 12px #0001}
    h1{margin:0;font-size:26px} h2{margin-top:0;color:#16243a}
    input[type=file]{padding:10px;background:#eef2f7;border-radius:8px;width:100%}
    button{background:#2563eb;color:white;border:0;padding:11px 18px;border-radius:9px;font-size:16px;margin-top:12px;cursor:pointer}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
    .info{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:10px}
    .label{font-size:13px;color:#64748b}.value{font-weight:bold;margin-top:4px;word-break:break-word}
    table{width:100%;border-collapse:collapse;font-size:14px} th,td{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left;vertical-align:top}
    th{background:#eef2f7;position:sticky;top:0}.ok{color:#15803d;font-weight:bold}.warn{color:#b45309;font-weight:bold}.bad{color:#b91c1c;font-weight:bold}
    .pill{display:inline-block;background:#e0f2fe;border-radius:999px;padding:4px 8px;margin:2px;font-size:13px}.muted{color:#64748b}.small{font-size:13px}
  </style>
</head>
<body>
<header><h1>Lector de información BSDL</h1><div class="small">Solo analiza el archivo BSDL. No ejecuta JTAG todavía.</div></header>
<main>
  <div class="card">
    <form method="post" enctype="multipart/form-data">
      <label>Sube archivo .bsdl</label><br><br>
      <input type="file" name="bsdl" accept=".bsdl,.bsd,.txt" required>
      <button type="submit">Leer BSDL</button>
    </form>
  </div>

  {% if result %}
  <div class="card">
    <h2>1. Información general</h2>
    <div class="grid">
      {% for k,v in result.general.items() %}
      <div class="info"><div class="label">{{k}}</div><div class="value">{{v}}</div></div>
      {% endfor %}
    </div>
  </div>

  <div class="card">
    <h2>2. Instrucciones JTAG</h2>
    {% if result.instructions %}
    <table><thead><tr><th>Nombre</th><th>Opcode binario</th><th>Opcode hex</th></tr></thead><tbody>
    {% for ins in result.instructions %}
      <tr><td>{{ins.name}}</td><td>{{ins.binary}}</td><td>{{ins.hex}}</td></tr>
    {% endfor %}
    </tbody></table>
    {% else %}<p class="warn">No se encontraron instrucciones.</p>{% endif %}
  </div>

  <div class="card">
    <h2>3. Pines y celdas Boundary Scan</h2>
    <p class="muted">Separado por pin. Cada pin puede tener celda Data, Control, Input/Output, etc.</p>
    {% if result.pins %}
    <table><thead><tr><th>Pin / Señal</th><th>Celdas</th><th>Data bits</th><th>Control bits</th><th>Tipos encontrados</th></tr></thead><tbody>
    {% for pin in result.pins %}
      <tr>
        <td><b>{{pin.name}}</b></td>
        <td>
          {% for c in pin.cells %}
            <span class="pill">bit {{c.bit}}: {{c.cell_type}}</span>
          {% endfor %}
        </td>
        <td>{{pin.data_bits|join(', ')}}</td>
        <td>{{pin.control_bits|join(', ')}}</td>
        <td>{{pin.types|join(', ')}}</td>
      </tr>
    {% endfor %}
    </tbody></table>
    {% else %}<p class="warn">No se encontraron pines/celdas.</p>{% endif %}
  </div>

  <div class="card">
    <h2>4. Registro boundary completo</h2>
    {% if result.boundary_cells %}
    <table><thead><tr><th>Bit</th><th>Celda</th><th>Puerto/Pin</th><th>Tipo</th><th>Control</th><th>Texto original</th></tr></thead><tbody>
    {% for c in result.boundary_cells %}
      <tr><td>{{c.bit}}</td><td>{{c.cell}}</td><td>{{c.port}}</td><td>{{c.cell_type}}</td><td>{{c.control}}</td><td class="small">{{c.raw}}</td></tr>
    {% endfor %}
    </tbody></table>
    {% else %}<p class="warn">No se encontraron celdas boundary.</p>{% endif %}
  </div>

  <div class="card">
    <h2>5. Avisos</h2>
    {% if result.warnings %}
      {% for w in result.warnings %}<p class="warn">⚠ {{w}}</p>{% endfor %}
    {% else %}<p class="ok">No hay avisos importantes.</p>{% endif %}
  </div>
  {% endif %}
</main>
</body></html>
'''

class Obj(dict):
    __getattr__ = dict.get

def strip_comments(text):
    return re.sub(r'--.*', '', text)

def find_entity(text):
    m = re.search(r'\bentity\s+(\w+)\s+is\b', text, re.I)
    return m.group(1) if m else 'No encontrado'

def attr_value(text, attr):
    m = re.search(r'attribute\s+'+re.escape(attr)+r'\s+of\s+[^:]+:\s*[^\s]+\s+is\s*(.*?);', text, re.I|re.S)
    return m.group(1).strip() if m else ''

def collect_quoted(attr_text):
    return ''.join(re.findall(r'"(.*?)"', attr_text, re.S))

def parse_instruction_len(text):
    val = collect_quoted(attr_value(text, 'INSTRUCTION_LENGTH')) or attr_value(text, 'INSTRUCTION_LENGTH')
    m = re.search(r'\d+', val)
    return int(m.group(0)) if m else None

def parse_boundary_len(text):
    val = collect_quoted(attr_value(text, 'BOUNDARY_LENGTH')) or attr_value(text, 'BOUNDARY_LENGTH')
    m = re.search(r'\d+', val)
    return int(m.group(0)) if m else None

def parse_opcodes(text):
    data = collect_quoted(attr_value(text, 'INSTRUCTION_OPCODE'))
    out = []
    for name, bits in re.findall(r'([A-Za-z0-9_/]+)\s*\(\s*([01Xx]+)\s*\)', data):
        b = bits.upper()
        hx = '-'
        if set(b) <= {'0','1'}:
            hx = '0x%X' % int(b, 2)
        out.append(Obj(name=name, binary=b, hex=hx))
    return out

def split_boundary_entries(s):
    entries = []
    current = ''
    depth = 0
    for ch in s:
        current += ch
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                entries.append(current.strip(' ,&\n\t'))
                current = ''
    return entries

def parse_boundary(text):
    data = collect_quoted(attr_value(text, 'BOUNDARY_REGISTER'))
    cells = []
    for entry in split_boundary_entries(data):
        m = re.match(r'\s*(\d+)\s*\((.*)\)\s*', entry, re.S)
        if not m:
            continue
        bit = int(m.group(1))
        parts = [p.strip() for p in m.group(2).split(',')]
        cell = parts[0] if len(parts)>0 else ''
        port = parts[1] if len(parts)>1 else ''
        cell_type = parts[2] if len(parts)>2 else ''
        control = parts[4] if len(parts)>4 else ''
        cells.append(Obj(bit=bit, cell=cell, port=port, cell_type=cell_type, control=control, raw=f'{bit} ({m.group(2)})'))
    cells.sort(key=lambda x: x.bit, reverse=True)
    return cells

def pin_base(port):
    if not port or port == '*':
        return None
    # PG5.Data -> PG5, PG5 -> PG5
    return port.split('.')[0].strip()

def group_pins(cells):
    pins = {}
    for c in cells:
        base = pin_base(c.port)
        # If control cell has port *, attach by its bit only impossible. Keep separate internal cell.
        if not base:
            continue
        p = pins.setdefault(base, {'name': base, 'cells': [], 'data_bits': [], 'control_bits': [], 'types': set()})
        p['cells'].append(c)
        typ = (c.cell_type or '').lower()
        p['types'].add(c.cell_type or '-')
        rawport = (c.port or '').lower()
        if 'data' in rawport or 'output' in typ or 'bidir' in typ or 'input' in typ:
            p['data_bits'].append(str(c.bit))
        if 'control' in rawport or 'control' in typ:
            p['control_bits'].append(str(c.bit))
    out=[]
    for p in pins.values():
        p['cells'].sort(key=lambda x: x.bit, reverse=True)
        p['types'] = sorted(p['types'])
        out.append(Obj(**p))
    out.sort(key=lambda x: x.name)
    return out

def parse_bsdl(text, filename):
    clean = strip_comments(text)
    entity = find_entity(clean)
    instr_len = parse_instruction_len(clean)
    bound_len = parse_boundary_len(clean)
    instructions = parse_opcodes(clean)
    cells = parse_boundary(clean)
    pins = group_pins(cells)
    warnings=[]
    if not instructions: warnings.append('No encontré INSTRUCTION_OPCODE.')
    if bound_len is None: warnings.append('No encontré BOUNDARY_LENGTH.')
    if not cells: warnings.append('No encontré BOUNDARY_REGISTER o no pude leer sus celdas.')
    if bound_len is not None and cells and len(cells) != bound_len:
        warnings.append(f'BOUNDARY_LENGTH dice {bound_len}, pero leí {len(cells)} celdas.')
    return Obj(
        general=Obj({
            'Archivo': filename,
            'Entity / Chip': entity,
            'Instruction length': instr_len if instr_len is not None else 'No encontrado',
            'Boundary length': bound_len if bound_len is not None else 'No encontrado',
            'Cantidad de instrucciones': len(instructions),
            'Cantidad de celdas boundary': len(cells),
            'Cantidad de pines/señales': len(pins),
        }),
        instructions=instructions,
        boundary_cells=cells,
        pins=pins,
        warnings=warnings
    )

@app.route('/', methods=['GET','POST'])
def index():
    result = None
    if request.method == 'POST':
        f = request.files.get('bsdl')
        if f:
            text = f.read().decode('utf-8', errors='ignore')
            result = parse_bsdl(text, f.filename)
    return render_template_string(HTML, result=result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8088, debug=False)
