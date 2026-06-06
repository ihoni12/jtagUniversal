#!/usr/bin/env python3
import os, re, socket, time, html
from flask import Flask, request, render_template_string

app = Flask(__name__)
UPLOAD_DIR = 'uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)

DEFAULT_TAP = 'atmega2560.cpu'
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 4444
EXPECTED_IDCODE = '0x4980103f'

HTML_PAGE = r'''
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>JTAG ATmega2560 Tester</title>
<style>
body{font-family:Arial,sans-serif;background:#0f172a;color:#e5e7eb;margin:0;padding:22px}
.card{background:#111827;border:1px solid #334155;border-radius:14px;padding:18px;margin-bottom:18px;box-shadow:0 6px 20px rgba(0,0,0,.25)}
h1{margin:0 0 16px}.row{display:flex;gap:12px;flex-wrap:wrap;align-items:end}label{display:block;font-size:13px;color:#cbd5e1;margin-bottom:5px}
input,button{border-radius:10px;border:1px solid #475569;background:#020617;color:#fff;padding:10px}button{cursor:pointer;background:#2563eb;border:0;font-weight:bold}.btn2{background:#475569}.msg{white-space:pre-wrap;background:#020617;border-radius:10px;padding:12px;max-height:380px;overflow:auto;font-family:Consolas,monospace;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(92px,1fr));gap:8px;margin-top:14px}.pin{border-radius:10px;padding:9px;text-align:center;font-size:13px;border:1px solid #334155}.wait{background:#334155}.pass{background:#166534}.fail{background:#991b1b}.skip{background:#854d0e}.pin small{display:block;color:#e2e8f0;font-size:11px;margin-top:4px}.legend span{display:inline-block;padding:6px 10px;border-radius:9px;margin-right:7px}.green{background:#166534}.red{background:#991b1b}.gray{background:#334155}.orange{background:#854d0e}
</style>
</head>
<body>
<h1>Revisión JTAG ATmega2560</h1>
<div class="card">
<form method="post" enctype="multipart/form-data">
<div class="row">
<div><label>BSDL</label><input type="file" name="bsdl" accept=".bsdl,.bsd,.txt" required></div>
<div><label>TAP OpenOCD</label><input name="tap" value="{{tap}}"></div>
<div><label>Host</label><input name="host" value="127.0.0.1"></div>
<div><label>Puerto</label><input name="port" value="4444"></div>
<div><label>Delay entre pines</label><input name="delay" value="0.02"></div>
<div><button type="submit">Empezar revisión</button></div>
</div>
</form>
</div>
{% if summary %}
<div class="card">
<h2>Resultado</h2>
<div class="legend"><span class="green">Verde = pasó</span><span class="red">Rojo = posible corto/fallo</span><span class="orange">Naranja = no probado</span><span class="gray">Gris = pendiente</span></div>
<p>{{summary}}</p>
<div class="grid">
{% for p in pins %}<div class="pin {{p.status}}"><b>{{p.name}}</b><small>{{p.note}}</small></div>{% endfor %}
</div>
</div>
<div class="card"><h2>Log</h2><div class="msg">{{log}}</div></div>
{% endif %}
</body>
</html>
'''

class OpenOCD:
    def __init__(self, host, port):
        self.host=host; self.port=int(port)
    def cmd(self, command, timeout=3.0):
        with socket.create_connection((self.host,self.port), timeout=timeout) as s:
            s.settimeout(timeout)
            try: s.recv(4096)  # prompt/banner
            except Exception: pass
            s.sendall((command+'\n').encode())
            time.sleep(0.05)
            chunks=[]
            end=time.time()+timeout
            while time.time()<end:
                try:
                    data=s.recv(8192)
                    if not data: break
                    chunks.append(data.decode(errors='ignore'))
                    if '> ' in chunks[-1] or command in ''.join(chunks):
                        break
                except socket.timeout:
                    break
            return ''.join(chunks)

def parse_bsdl(text):
    ent = re.search(r'entity\s+(\w+)\s+is', text, re.I)
    entity = ent.group(1) if ent else 'UNKNOWN'

    ilen = None
    m = re.search(r'INSTRUCTION_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)', text, re.I)
    if m:
        ilen = int(m.group(1))

    blen = None
    m = re.search(r'BOUNDARY_LENGTH\s+of\s+\w+\s*:\s*entity\s+is\s+(\d+)', text, re.I)
    if m:
        blen = int(m.group(1))

    # Opcodes: EXTEST (0000), SAMPLE/PRELOAD (0010), etc.
    ops = {}
    opcode_block = re.search(r'INSTRUCTION_OPCODE.*?is(.*?);', text, re.I | re.S)
    if opcode_block:
        for name, bits in re.findall(r'([A-Z0-9_\/]+)\s*\(\s*([01]+)\s*\)', opcode_block.group(1), re.I):
            ops[name.upper()] = bits

    # Parse real BSDL boundary lines, including output3 control reference:
    # 164 (BC_1, PG5, output3, X, 163, 0, Z)
    # 163 (BC_1, *, control, 1)
    cells = []
    bit_to_control_owner = {}  # control_bit -> pin name, learned from output3/bidir line

    for raw in text.splitlines():
        line = raw.strip().strip('"').rstrip('&').rstrip(',').strip()
        m = re.match(r'^(\d+)\s*\((.*)\)\s*$', line)
        if not m:
            continue
        bit = int(m.group(1))
        parts = [x.strip() for x in m.group(2).split(',')]
        if len(parts) < 3:
            continue
        cell = parts[0]
        port = parts[1].upper()
        typ = parts[2].lower()
        control_ref = None
        if typ in ('output3', 'bidir', 'output2') and len(parts) >= 5:
            try:
                control_ref = int(parts[4])
            except Exception:
                control_ref = None
        cells.append({'bit': bit, 'cell': cell, 'port': port, 'type': typ, 'control_ref': control_ref, 'raw': line})
        if port not in ('*', 'INTERNAL') and control_ref is not None:
            bit_to_control_owner[control_ref] = port

    # Fallback for table-style text: 164 PG5.Data / 163 PG5.Control
    if not cells:
        for bit, sig in re.findall(r'(\d+)\s+([A-Z]{1,2}\d\.(?:Data|Control)|RSTT)', text, re.I):
            bit = int(bit)
            sig = sig.upper()
            if sig.endswith('.DATA'):
                port = sig.split('.')[0]
                typ = 'output3'
                control_ref = None
            elif sig.endswith('.CONTROL'):
                port = sig.split('.')[0]
                typ = 'control'
                control_ref = None
            else:
                port = sig
                typ = 'observe_only'
                control_ref = None
            cells.append({'bit': bit, 'cell': 'BC_1', 'port': port, 'type': typ, 'control_ref': control_ref, 'raw': sig})

    pins = {}
    for c in cells:
        p = c['port'].upper()

        # In valid BSDL control cells often have port='*'. Attach them to the pin that referenced this control bit.
        if p == '*' and 'control' in c['type']:
            p = bit_to_control_owner.get(c['bit'], '*')

        if p in ('*', 'INTERNAL'):
            continue

        pins.setdefault(p, {'name': p, 'data_bits': [], 'control_bits': [], 'observe_bits': []})

        if 'control' in c['type']:
            pins[p]['control_bits'].append(c['bit'])
        elif c['type'] in ('input', 'observe_only') or p == 'RSTT':
            pins[p]['observe_bits'].append(c['bit'])
        else:
            # output3/bidir/data cells are both drive data and readable during scan.
            pins[p]['data_bits'].append(c['bit'])
            if c.get('control_ref') is not None:
                if c['control_ref'] not in pins[p]['control_bits']:
                    pins[p]['control_bits'].append(c['control_ref'])

    # Clean invalid/duplicate bits and sort high-to-low for human readability.
    for p in pins.values():
        p['data_bits'] = sorted(set(p['data_bits']), reverse=True)
        p['control_bits'] = sorted(set(p['control_bits']), reverse=True)
        p['observe_bits'] = sorted(set(p['observe_bits']), reverse=True)

    return entity, ilen, blen, ops, pins

def bitstring_to_list(hexstr, blen):
    val=int(hexstr,16)
    # list index = bit number
    return [(val>>i)&1 for i in range(blen)]

def list_to_hex(bits):
    val=0
    for i,b in enumerate(bits):
        if b: val |= (1<<i)
    nibbles=(len(bits)+3)//4
    return format(val, '0{}x'.format(nibbles))

def extract_hex(resp):
    # prefer long hex from drscan result
    vals=re.findall(r'\b(?:0x)?[0-9a-fA-F]{8,}\b', resp)
    if not vals: return None
    return vals[-1].lower().replace('0x','')

def run_review(bsdl_text, tap, host, port, delay):
    log=[]
    entity, ilen, blen, ops, pins = parse_bsdl(bsdl_text)
    log.append('Empezó la revisión')
    log.append(f'Chip/Entity: {entity}')
    log.append(f'Instruction length: {ilen}')
    log.append(f'Boundary length: {blen}')
    log.append('Instrucciones encontradas: ' + ', '.join(sorted(ops.keys())))
    if not ilen or not blen:
        raise RuntimeError('El BSDL no tiene INSTRUCTION_LENGTH o BOUNDARY_LENGTH')
    sample = ops.get('SAMPLE') or ops.get('SAMPLE/PRELOAD') or ops.get('SAMPLE_PRELOAD')
    extest = ops.get('EXTEST')
    idcode = ops.get('IDCODE')
    if not sample: raise RuntimeError('El BSDL no contiene SAMPLE o SAMPLE/PRELOAD')
    if not extest: raise RuntimeError('El BSDL no contiene EXTEST')
    if not idcode: raise RuntimeError('El BSDL no contiene IDCODE')
    oo=OpenOCD(host, port)
    log.append('Verificando conexión real JTAG con IDCODE...')
    r=oo.cmd(f'irscan {tap} 0x{int(idcode,2):x}')
    r=oo.cmd(f'drscan {tap} 32 0')
    hx=extract_hex(r)
    if not hx or int(hx,16)==0:
        raise RuntimeError(f'JTAG no responde bien. IDCODE leído: 0x00000000. Revisa cables/alimentación/fuse.')
    log.append(f'IDCODE OK: 0x{int(hx,16):08x}')
    # baseline sample
    oo.cmd(f'irscan {tap} 0x{int(sample,2):x}')
    base_resp=oo.cmd(f'drscan {tap} {blen} 0')
    base_hex=extract_hex(base_resp) or '0'
    base=bitstring_to_list(base_hex, blen)
    log.append('SAMPLE leído correctamente')
    # EXTEST preload vector all safe: controls=1 (Z/input), data=0
    all_bits=[0]*blen
    for p in pins.values():
        for cb in p['control_bits']:
            if cb < blen: all_bits[cb]=1
    oo.cmd(f'irscan {tap} 0x{int(extest,2):x}')
    pin_results=[]; tested=0; failcount=0
    log.append('Ejecutando EXTEST: cada pin sale 0 y luego 1; se lee si otros pines cambian igual.')
    for name in sorted(pins.keys()):
        p=pins[name]
        if name == 'RSTT' or not p['data_bits']:
            pin_results.append({'name':name,'status':'skip','note':'sin data'})
            continue
        data_bit=p['data_bits'][0]
        control_bits=p['control_bits']
        if data_bit >= blen:
            pin_results.append({'name':name,'status':'skip','note':'bit inválido'})
            continue
        # drive enable: control=0 often enables output on AVR BSDL style. If wrong, still safe-ish but use only JTAG boundary.
        v0=all_bits[:]
        for cb in control_bits:
            if cb < blen: v0[cb]=0
        v0[data_bit]=0
        v1=v0[:]; v1[data_bit]=1
        log.append(f'Probando {name}: bit data {data_bit}, control {control_bits}')
        oo.cmd(f'drscan {tap} {blen} 0x{list_to_hex(v0)}')
        time.sleep(delay)
        r0=oo.cmd(f'drscan {tap} {blen} 0x{list_to_hex(v0)}')
        h0=extract_hex(r0) or '0'
        oo.cmd(f'drscan {tap} {blen} 0x{list_to_hex(v1)}')
        time.sleep(delay)
        r1=oo.cmd(f'drscan {tap} {blen} 0x{list_to_hex(v1)}')
        h1=extract_hex(r1) or '0'
        b0=bitstring_to_list(h0, blen); b1=bitstring_to_list(h1, blen)
        changed=[]
        for other,on in pins.items():
            if other==name or other=='RSTT': continue
            for ob in (on['data_bits']+on['observe_bits']):
                if ob < blen and b0[ob] != b1[ob]:
                    changed.append(other); break
        tested += 1
        if changed:
            failcount += 1
            note='cambia: '+','.join(changed[:3]) + ('...' if len(changed)>3 else '')
            pin_results.append({'name':name,'status':'fail','note':note})
            log.append(f'  FALLA/SOSPECHA: {name} cambia junto con {changed}')
        else:
            pin_results.append({'name':name,'status':'pass','note':'OK'})
            log.append(f'  OK: {name}')
    # return to SAMPLE/BYPASS to reduce driving
    try: oo.cmd(f'irscan {tap} 0x{int(sample,2):x}')
    except Exception: pass
    log.append(f'Pines manejables probados: {tested}')
    log.append(f'Pines con posible fallo/corto: {failcount}')
    log.append('Terminó la revisión')
    summary=f'Probados {tested} pines. Fallos/sospechas: {failcount}. Verde=pasó, rojo=posible corto/fallo, naranja=no probado.'
    return summary, pin_results, '\n'.join(log)

@app.route('/', methods=['GET','POST'])
def index():
    summary=None; pins=[]; log=''
    tap=DEFAULT_TAP
    if request.method=='POST':
        try:
            tap=request.form.get('tap',DEFAULT_TAP).strip() or DEFAULT_TAP
            host=request.form.get('host',DEFAULT_HOST).strip() or DEFAULT_HOST
            port=int(request.form.get('port',DEFAULT_PORT))
            delay=float(request.form.get('delay','0.02'))
            f=request.files['bsdl']
            text=f.read().decode(errors='ignore')
            summary,pins,log=run_review(text,tap,host,port,delay)
        except Exception as e:
            summary='ERROR'
            pins=[]
            log='ERROR: '+str(e)
    return render_template_string(HTML_PAGE, summary=summary, pins=pins, log=log, tap=tap)

if __name__=='__main__':
    app.run(host='0.0.0.0', port=8088, debug=False)
