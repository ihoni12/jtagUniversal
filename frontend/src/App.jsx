import { useMemo, useRef, useState } from "react";
import "./App.css";

function getDefaultApiUrl() {
  const host = window.location.hostname;
  return `http://${host}:5000`;
}

function parseEvent(data) {
  try { return JSON.parse(data); } catch { return { type: "log", text: data }; }
}

function App() {
  const [apiUrl, setApiUrl] = useState(getDefaultApiUrl());
  const [file, setFile] = useState(null);
  const [netlistFile, setNetlistFile] = useState(null);
  const [uutRef, setUutRef] = useState("U1");
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState("");
  const [events, setEvents] = useState([]);
  const [lineChecks, setLineChecks] = useState([]);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [reports, setReports] = useState([]);
  const [options, setOptions] = useState({ simple_output: true, external_line_test: true, external_bidir: false, netlist_test: true, no_short_test: false, map_only: false });
  const [board, setBoard] = useState(null);
  const [selectedPin, setSelectedPin] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [pinFilter, setPinFilter] = useState("");
  const outputRef = useRef(null);

  const summary = useMemo(() => {
    const ok = (output.match(/\[OK\]|\bOK: [1-9]|OK_SIN_CORTO|OK_SEGUN_NETLIST/g) || []).length;
    const fail = (output.match(/\[FAIL\]|\bFAIL: [1-9]/g) || []).length;
    const err = (output.match(/\[ERROR\]|\bERROR: [1-9]|ERROR:/g) || []).length;
    const shorts = (output.match(/CORTO_SOSPECHOSO|\[CORTO\?\]/g) || []).length;
    return { ok, fail, err, shorts };
  }, [output]);

  const filteredPins = useMemo(() => {
    const pins = board?.pins || [];
    const f = pinFilter.trim().toUpperCase();
    if (!f) return pins;
    return pins.filter((p) => `${p.name} ${(p.nets || []).join(" ")} ${(p.functions || []).join(" ")}`.toUpperCase().includes(f));
  }, [board, pinFilter]);

  const currentPin = useMemo(() => {
    if (!selectedPin) return null;
    return (board?.pins || []).find((p) => p.name === selectedPin) || null;
  }, [board, selectedPin]);

  const progress = useMemo(() => {
    const matches = output.match(/\[(\d+)\/(\d+)\]/g);
    if (!matches || matches.length === 0) return null;
    const last = matches[matches.length - 1].match(/\[(\d+)\/(\d+)\]/);
    if (!last) return null;
    const current = Number(last[1]);
    const total = Number(last[2]);
    return { current, total, percent: Math.round((current / total) * 100) };
  }, [output]);

  function updateOption(key, value) { setOptions((prev) => ({ ...prev, [key]: value })); }

  function appendOutput(text, type = "log") {
    if (text === "__DONE__") return;
    setOutput((prev) => prev + text);
    const clean = text.trim();
    const finalLineMatch = clean.match(/^\[(\d+)\/(\d+)\]\s+([^:]+):\s+UUT\s+([A-Z0-9]+)\s+<->\s+PI\.GPIO(\d+)\s+\(([^)]+)\)\s+->\s+(OK|FAIL|ERROR)/);
    if (finalLineMatch) {
      setLineChecks((prev) => [{ net: finalLineMatch[3], pin: finalLineMatch[4], gpio: finalLineMatch[5], direction: finalLineMatch[6], status: finalLineMatch[7] }, ...prev.filter((x) => x.net !== finalLineMatch[3])]);
    }
    if (clean) setEvents((prev) => [{ text: clean, type, ts: Date.now() }, ...prev].slice(0, 10));
    setTimeout(() => { if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight; }, 20);
  }

  async function loadReports(id) {
    try {
      const res = await fetch(`${apiUrl}/api/status/${id}`);
      const data = await res.json();
      if (data.ok) setReports(data.files || []);
    } catch {}
  }

  function makeForm(extra = {}) {
    const formData = new FormData();
    if (file) formData.append("bsdl", file);
    if (netlistFile) formData.append("netlist", netlistFile);
    Object.entries({ ...options, uut_ref: uutRef || "U1", ...extra }).forEach(([k, v]) => formData.append(k, String(v)));
    return formData;
  }

  async function analyzeBoard() {
    if (!file) { setError("Selecciona un archivo BSDL primero."); return; }
    setError("");
    setBoard(null);
    setSelectedPin(null);
    try {
      appendOutput("Leyendo BSDL/netlist para crear mapa de placa...\n", "info");
      const res = await fetch(`${apiUrl}/api/analyze`, { method: "POST", body: makeForm() });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "No pude analizar archivos.");
      setBoard(data.data);
      setSelectedPin(data.data.pins?.[0]?.name || null);
      appendOutput(`Mapa listo: ${data.data.chipname}, ${data.data.pin_count} pines, ${data.data.net_count} nets.\n`, "done");
    } catch (err) { setError(err.message || "No pude analizar los archivos."); }
  }

  function consumeJob(jobId) {
    const source = new EventSource(`${apiUrl}/api/progress/${jobId}`);
    source.onmessage = (event) => {
      const payload = parseEvent(event.data);
      if (payload.text === "__DONE__") { source.close(); setRunning(false); setDone(true); loadReports(jobId); return; }
      appendOutput((payload.text || "").replaceAll("\\n", "\n"), payload.type || "log");
    };
    source.onerror = () => { source.close(); setRunning(false); setError("Se cortó la conexión con el servidor de progreso."); loadReports(jobId); };
  }

  async function startTest() {
    if (!file) { setError("Selecciona un archivo BSDL primero."); return; }
    setRunning(true); setDone(false); setOutput(""); setEvents([]); setLineChecks([]); setReports([]); setError("");
    try {
      appendOutput("Subiendo archivos a la Raspberry...\n", "info");
      const res = await fetch(`${apiUrl}/api/start`, { method: "POST", body: makeForm() });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "No se pudo iniciar la revisión.");
      appendOutput("Revisión completa iniciada.\n\n", "info");
      consumeJob(data.job_id);
    } catch (err) { setRunning(false); setError(err.message || "No pude conectar con la Raspberry Pi."); }
  }

  async function startPinTest(pinName = selectedPin) {
    if (!file || !pinName) { setError("Selecciona BSDL y un pin."); return; }
    setRunning(true); setDone(false); setOutput(""); setEvents([]); setReports([]); setError("");
    try {
      appendOutput(`Preparando prueba individual del pin ${pinName}...\n`, "info");
      const res = await fetch(`${apiUrl}/api/start-pin`, { method: "POST", body: makeForm({ pin: pinName }) });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "No se pudo iniciar la prueba del pin.");
      consumeJob(data.job_id);
    } catch (err) { setRunning(false); setError(err.message || "No pude conectar con la Raspberry Pi."); }
  }

  async function pingServer() {
    setError("");
    try {
      const res = await fetch(`${apiUrl}/api/ping`);
      const data = await res.json();
      if (data.ok) appendOutput("Servidor conectado correctamente.\n", "info");
      else setError("El servidor respondió, pero no está OK.");
    } catch { setError("No pude conectar con el backend. Revisa la IP y ejecuta backend/app.py."); }
  }

  return (
    <div className={`appShell ${sidebarOpen ? "withSidebar" : "closedSidebar"}`}>
      <aside className="sideBar">
        <button className="collapse" onClick={() => setSidebarOpen(!sidebarOpen)}>{sidebarOpen ? "‹" : "›"}</button>
        {sidebarOpen && <>
          <div className="boardName">
            <small>Placa / chip</small>
            <b>{board?.chipname || "Sin mapa"}</b>
            <span>{board ? `${board.pin_count} pines · ${board.net_count} nets` : "Carga BSDL y toca Analizar"}</span>
          </div>
          <input className="pinSearch" placeholder="Buscar pin / función" value={pinFilter} onChange={(e) => setPinFilter(e.target.value)} />
          <div className="pinList">
            {filteredPins.map((p) => (
              <button key={p.name} className={`pinBtn ${selectedPin === p.name ? "active" : ""}`} onClick={() => setSelectedPin(p.name)}>
                <b>{p.name}</b><small>{(p.functions || []).join(" · ")}</small>
              </button>
            ))}
            {!board && <p className="mutedSide">Todavía no hay pines cargados.</p>}
          </div>
        </>}
      </aside>

      <main className="page">
        <div className="hero"><div><p className="eyebrow">Raspberry Pi · OpenOCD · Boundary Scan</p><h1>Estación JTAG Universal</h1><p className="subtitle">Carga BSDL + netlist, abre el mapa lateral de pines, revisa un pin por separado o ejecuta toda la revisión.</p></div><div className={`status ${running ? "running" : done ? "done" : "idle"}`}>{running ? "Revisando" : done ? "Terminado" : "Listo"}</div></div>
        <div className="stats"><div><b>{summary.ok}</b><span>OK</span></div><div><b>{summary.fail}</b><span>Fallos</span></div><div><b>{summary.err}</b><span>Errores</span></div><div><b>{summary.shorts}</b><span>Cortos?</span></div></div>
        <div className="grid">
          <section className="card controls">
            <h2>Configuración</h2>
            <label>URL del backend</label><div className="row"><input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} /><button className="secondary" onClick={pingServer} disabled={running}>Probar</button></div>
            <label>Archivo BSDL</label><div className="fileBox"><input type="file" accept=".bsdl,.bsd,.txt" onChange={(e) => setFile(e.target.files?.[0] || null)} disabled={running} /><span>{file ? file.name : "Selecciona el BSDL del chip"}</span></div>
            <label>Archivo Netlist</label><div className="fileBox"><input type="file" accept=".net,.cir,.csv,.txt,.xml" onChange={(e) => setNetlistFile(e.target.files?.[0] || null)} disabled={running} /><span>{netlistFile ? netlistFile.name : "Opcional, pero recomendado"}</span></div>
            <label>Nombre del chip en netlist</label><input value={uutRef} onChange={(e) => setUutRef(e.target.value.toUpperCase())} placeholder="U1" disabled={running} />
            <div className="checks"><label><input type="checkbox" checked={options.simple_output} onChange={(e) => updateOption("simple_output", e.target.checked)} /> Salida simple</label><label><input type="checkbox" checked={options.external_line_test} onChange={(e) => updateOption("external_line_test", e.target.checked)} /> Revisar TX/RX/SPI/I2C/GPIO hacia Pi</label><label><input type="checkbox" checked={options.netlist_test} onChange={(e) => updateOption("netlist_test", e.target.checked)} /> Validar netlist</label><label><input type="checkbox" checked={options.external_bidir} onChange={(e) => updateOption("external_bidir", e.target.checked)} /> Probar ambas direcciones</label><label><input type="checkbox" checked={options.no_short_test} onChange={(e) => updateOption("no_short_test", e.target.checked)} /> Saltar cortos generales</label><label><input type="checkbox" checked={options.map_only} onChange={(e) => updateOption("map_only", e.target.checked)} /> Solo mapa, sin JTAG</label></div>
            <div className="buttonStack"><button className="secondary" onClick={analyzeBoard} disabled={running || !file}>Analizar archivos / cargar pines</button><button className="primary" onClick={startTest} disabled={running || !file}>{running ? "Revisión en curso..." : "Iniciar revisión completa"}</button></div>
            {progress && <div className="progressWrap"><div className="progressInfo"><span>Progreso</span><b>{progress.current}/{progress.total} · {progress.percent}%</b></div><div className="bar"><div style={{ width: `${progress.percent}%` }} /></div></div>}
            {error && <div className="error">{error}</div>}
            {reports.length > 0 && <div className="reports"><b>Reportes</b>{reports.map((r) => <a key={r.name} href={`${apiUrl}${r.url}`} target="_blank" rel="noreferrer">{r.name}</a>)}</div>}
          </section>

          <section className="card dashboard">
            <div className="pinInspector">
              <div><div className="panelTitle noPad">Pin seleccionado</div>{currentPin ? <><h2>{currentPin.name}</h2><p className="muted">Input bit {currentPin.input_bit} · Output bit {currentPin.output_bit} · Control bit {currentPin.control_bit ?? "sin control"}</p><div className="chips">{(currentPin.functions || []).map((f) => <span key={f}>{f}</span>)}</div><p><b>Nets:</b> {(currentPin.nets || []).length ? currentPin.nets.join(", ") : "sin netlist"}</p></> : <p className="muted">Selecciona un pin de la barra lateral.</p>}</div>
              <button className="primary" onClick={() => startPinTest()} disabled={running || !currentPin}>Probar este pin</button>
            </div>
            <div className="panelTitle">Últimos eventos</div><div className="events">{events.length === 0 ? <p>Esperando revisión...</p> : events.map((e) => <div key={e.ts + e.text} className={`event ${e.type}`}>{e.text}</div>)}</div>
            <div className="linePanel"><div className="panelTitle">Líneas externas detectadas</div>{lineChecks.length === 0 ? <p className="muted">Todavía no se detectaron líneas. Necesitas netlist con U1.PIN + PI.GPIOxx.</p> : <div className="lineList">{lineChecks.map((l) => <div className={`lineItem ${l.status.toLowerCase()}`} key={l.net}><b>{l.net}</b><span>UUT {l.pin} ↔ PI.GPIO{l.gpio}</span><small>{l.direction}</small><em>{l.status}</em></div>)}</div>}</div>
            <div className="terminalTop"><span></span><span></span><span></span><b>Salida simplificada</b></div><pre className="terminal" ref={outputRef}>{output || "Esperando revisión..."}</pre>
          </section>
        </div>
      </main>
    </div>
  );
}

export default App;
