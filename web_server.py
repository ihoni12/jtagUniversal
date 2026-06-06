#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
import cgi
import html
import json
import socket
import sys

from bsdl_reader import parse_bsdl_text

HOST = "0.0.0.0"
PORT = 8088
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
LAST_RESULT = None


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "IP_DE_TU_RASPBERRY"


def esc(x):
    return html.escape(str(x or ""))


def page(result=None, error=""):
    body_result = ""
    if result:
        ports_rows = "".join(
            f"<tr><td>{i}</td><td>{esc(p['name'])}</td><td>{esc(p['direction'])}</td><td>{esc(p['type'])}</td></tr>"
            for i, p in enumerate(result['ports'], 1)
        ) or "<tr><td colspan='4'>No se encontraron puertos</td></tr>"

        instr_rows = "".join(
            f"<tr><td>{esc(x['name'])}</td><td><code>{esc(x['code'])}</code></td></tr>"
            for x in result['instructions']
        ) or "<tr><td colspan='2'>No se encontraron instrucciones</td></tr>"

        boundary_rows = "".join(
            f"<tr><td>{r['cell']}</td><td>{esc(r['cell_type'])}</td><td>{esc(r['pin_signal'])}</td><td>{esc(r['function'])}</td><td>{esc(r['extra'])}</td></tr>"
            for r in result['boundary_rows']
        ) or "<tr><td colspan='5'>No se pudo separar BOUNDARY_REGISTER</td></tr>"

        attrs_html = "".join(
            f"<div class='attr'><h3>{esc(a['name'])}</h3><pre>{esc(a['value'])}</pre></div>"
            for a in result['other_attrs']
        ) or "<p>No se encontraron otros atributos importantes.</p>"

        body_result = f"""
        <section class='card'>
          <h2>1) Chip / Entity</h2>
          <div class='grid'>
            <div><b>Archivo:</b> {esc(result['filename'])}</div>
            <div><b>Entity / Chip:</b> {esc(result['entity'])}</div>
            <div><b>Puertos:</b> {result['summary']['ports_count']}</div>
            <div><b>Instrucciones:</b> {result['summary']['instructions_count']}</div>
            <div><b>Celdas boundary:</b> {result['summary']['boundary_cells_count']}</div>
          </div>
        </section>

        <section class='card'>
          <h2>2) Puertos / Pines</h2>
          <table><thead><tr><th>#</th><th>Nombre</th><th>Dirección</th><th>Tipo</th></tr></thead><tbody>{ports_rows}</tbody></table>
        </section>

        <section class='card'>
          <h2>3) Instrucciones JTAG</h2>
          <p><b>Instruction length:</b> {esc(result['instruction_length'])}</p>
          <p><b>Instruction capture:</b> {esc(result['instruction_capture'])}</p>
          <p><b>Instruction private:</b> {esc(result['instruction_private'])}</p>
          <table><thead><tr><th>Nombre</th><th>Código binario</th></tr></thead><tbody>{instr_rows}</tbody></table>
        </section>

        <section class='card'>
          <h2>4) IDCODE</h2>
          <pre>{esc(result['idcode'])}</pre>
        </section>

        <section class='card'>
          <h2>5) Boundary Register</h2>
          <p><b>Boundary length:</b> {esc(result['boundary_length'])}</p>
          <table><thead><tr><th>Cell</th><th>Tipo celda</th><th>Pin/Señal</th><th>Función</th><th>Extra</th></tr></thead><tbody>{boundary_rows}</tbody></table>
        </section>

        <section class='card'>
          <h2>6) Otros atributos</h2>
          {attrs_html}
        </section>
        """

    err_html = f"<div class='error'>{esc(error)}</div>" if error else ""
    return f"""<!doctype html>
<html lang='es'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Lector BSDL</title>
<style>
  body {{ font-family: Arial, sans-serif; background:#f4f6fb; margin:0; color:#1d2733; }}
  header {{ background:#182235; color:white; padding:22px; }}
  main {{ max-width:1200px; margin:20px auto; padding:0 14px; }}
  .card {{ background:white; border-radius:14px; padding:18px; margin:14px 0; box-shadow:0 3px 14px #0001; overflow:auto; }}
  h1,h2 {{ margin-top:0; }}
  input[type=file] {{ padding:10px; background:#eef2f7; border-radius:10px; width:100%; box-sizing:border-box; }}
  button {{ margin-top:12px; padding:12px 18px; border:0; border-radius:10px; background:#2563eb; color:white; font-size:16px; cursor:pointer; }}
  table {{ border-collapse:collapse; width:100%; font-size:14px; }}
  th,td {{ border-bottom:1px solid #e5e7eb; padding:8px; text-align:left; white-space:nowrap; }}
  th {{ background:#f1f5f9; position:sticky; top:0; }}
  pre {{ background:#0f172a; color:#e2e8f0; padding:12px; border-radius:10px; overflow:auto; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px; }}
  .error {{ background:#fee2e2; color:#991b1b; padding:12px; border-radius:10px; margin:12px 0; }}
  .hint {{ color:#64748b; }}
</style>
</head>
<body>
<header>
  <h1>Lector BSDL</h1>
  <div>Sube un archivo .bsdl y mira la información separada.</div>
</header>
<main>
  <section class='card'>
    <h2>Subir archivo</h2>
    {err_html}
    <form action='/upload' method='post' enctype='multipart/form-data'>
      <input type='file' name='bsdl' accept='.bsdl,.bsd,.txt' required>
      <button type='submit'>Analizar BSDL</button>
    </form>
    <p class='hint'>Esto solo lee el archivo BSDL. No conecta todavía al chip.</p>
  </section>
  {body_result}
</main>
</body>
</html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def send_html(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_html(page(LAST_RESULT))

    def do_POST(self):
        global LAST_RESULT
        if self.path != "/upload":
            self.send_error(404)
            return
        try:
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type"),
            })
            item = form["bsdl"] if "bsdl" in form else None
            if item is None or not item.filename:
                self.send_html(page(error="No recibí ningún archivo."))
                return
            filename = Path(item.filename).name
            raw_bytes = item.file.read()
            raw_text = raw_bytes.decode("utf-8", errors="ignore")
            (UPLOAD_DIR / filename).write_bytes(raw_bytes)
            LAST_RESULT = parse_bsdl_text(raw_text, filename)
            self.send_html(page(LAST_RESULT))
        except Exception as e:
            self.send_html(page(error=f"Error leyendo el BSDL: {e}"))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    ip = get_lan_ip()
    print("Servidor BSDL iniciado")
    print(f"Desde la Raspberry: http://127.0.0.1:{port}")
    print(f"Desde tu computadora: http://{ip}:{port}")
    print("Para cerrar: Ctrl + C")
    server = ThreadingHTTPServer((HOST, port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
