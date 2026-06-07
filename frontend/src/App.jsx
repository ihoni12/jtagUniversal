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
  const [jobId, setJobId] = useState(null);
  const [reports, setReports] = useState([]);
  const [options, setOptions] = useState({
    simple_output: true,
    external_line_test: true,
    external_bidir: false,
    netlist_test: true,
    no_short_test: false,
    map_only: false,
  });
  const outputRef = useRef(null);

  const summary = useMemo(() => {
    const ok = (output.match(/\[OK\]|\bOK: [1-9]/g) || []).length;
    const fail = (output.match(/\[FAIL\]|\bFAIL: [1-9]/g) || []).length;
    const err = (output.match(/\[ERROR\]|\bERROR: [1-9]|ERROR:/g) || []).length;
    const shorts = (output.match(/CORTO_SOSPECHOSO|\[CORTO\?\]/g) || []).length;
    return { ok, fail, err, shorts };
  }, [output]);

  const progress = useMemo(() => {
    const matches = output.match(/\[(\d+)\/(\d+)\]/g);
    if (!matches || matches.length === 0) return null;
    const last = matches[matches.length - 1].match(/\[(\d+)\/(\d+)\]/);
    if (!last) return null;
    const current = Number(last[1]);
    const total = Number(last[2]);
    return { current, total, percent: Math.round((current / total) * 100) };
  }, [output]);

  function updateOption(key, value) {
    setOptions((prev) => ({ ...prev, [key]: value }));
  }

  function appendOutput(text, type = "log") {
    if (text === "__DONE__") return;
    setOutput((prev) => prev + text);
    const clean = text.trim();

    const lineMatch = clean.match(/^\[(\d+)\/(\d+)\]\s+([^:]+):\s+UUT\s+([A-Z0-9]+)\s+<->\s+PI\.GPIO(\d+)\s+\(([^)]+)\)/);
    if (lineMatch) {
      setLineChecks((prev) => [{
        net: lineMatch[3],
        pin: lineMatch[4],
        gpio: lineMatch[5],
        direction: lineMatch[6],
        status: "RUNNING"
      }, ...prev.filter((x) => x.net !== lineMatch[3])]);
    }

    const finalLineMatch = clean.match(/^\[(\d+)\/(\d+)\]\s+([^:]+):\s+UUT\s+([A-Z0-9]+)\s+<->\s+PI\.GPIO(\d+)\s+\(([^)]+)\)\s+->\s+(OK|FAIL|ERROR)/);
    if (finalLineMatch) {
      setLineChecks((prev) => [{
        net: finalLineMatch[3],
        pin: finalLineMatch[4],
        gpio: finalLineMatch[5],
        direction: finalLineMatch[6],
        status: finalLineMatch[7]
      }, ...prev.filter((x) => x.net !== finalLineMatch[3])]);
    }

    if (clean === "[OK]" || clean === "[FAIL]" || clean.startsWith("[ERROR]")) {
      setLineChecks((prev) => {
        if (!prev.length) return prev;
        const copy = [...prev];
        copy[0] = { ...copy[0], status: clean.includes("OK") ? "OK" : clean.includes("FAIL") ? "FAIL" : "ERROR" };
        return copy;
      });
    }

    if (clean) setEvents((prev) => [{ text: clean, type, ts: Date.now() }, ...prev].slice(0, 10));
    setTimeout(() => {
      if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }, 20);
  }

  async function loadReports(id) {
    try {
      const res = await fetch(`${apiUrl}/api/status/${id}`);
      const data = await res.json();
      if (data.ok) setReports(data.files || []);
    } catch {}
  }

  async function startTest() {
    if (!file) {
      setError("Selecciona un archivo BSDL primero.");
      return;
    }
    setRunning(true);
    setDone(false);
    setOutput("");
    setEvents([]);
    setLineChecks([]);
    setReports([]);
    setError("");

    const formData = new FormData();
    formData.append("bsdl", file);
    if (netlistFile) formData.append("netlist", netlistFile);
    Object.entries({ ...options, uut_ref: uutRef || "U1" }).forEach(([k, v]) => formData.append(k, String(v)));

    try {
      appendOutput("Subiendo archivos a la Raspberry...\n", "info");
      const res = await fetch(`${apiUrl}/api/start`, { method: "POST", body: formData });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "No se pudo iniciar la revisión.");
      setJobId(data.job_id);
      appendOutput("Revisión iniciada.\n\n", "info");

      const source = new EventSource(`${apiUrl}/api/progress/${data.job_id}`);
      source.onmessage = (event) => {
        const payload = parseEvent(event.data);
        if (payload.text === "__DONE__") {
          source.close();
          setRunning(false);
          setDone(true);
          loadReports(data.job_id);
          return;
        }
        appendOutput((payload.text || "").replaceAll("\\n", "\n"), payload.type || "log");
      };
      source.onerror = () => {
        source.close();
        setRunning(false);
        setError("Se cortó la conexión con el servidor de progreso.");
        loadReports(data.job_id);
      };
    } catch (err) {
      setRunning(false);
      setError(err.message || "No pude conectar con la Raspberry Pi.");
    }
  }

  async function pingServer() {
    setError("");
    try {
      const res = await fetch(`${apiUrl}/api/ping`);
      const data = await res.json();
      if (data.ok) appendOutput("Servidor conectado correctamente.\n", "info");
      else setError("El servidor respondió, pero no está OK.");
    } catch {
      setError("No pude conectar con el backend. Revisa la IP y ejecuta backend/app.py.");
    }
  }

  return (
    <div className="page">
      <div className="hero">
        <div>
          <p className="eyebrow">Raspberry Pi · OpenOCD · Boundary Scan</p>
          <h1>Estación JTAG Universal</h1>
          <p className="subtitle">Carga BSDL + netlist, revisa cortos y muestra claramente líneas externas: TX/RX UART, SPI, I2C y GPIO hacia la Raspberry.</p>
        </div>
        <div className={`status ${running ? "running" : done ? "done" : "idle"}`}>{running ? "Revisando" : done ? "Terminado" : "Listo"}</div>
      </div>

      <div className="stats">
        <div><b>{summary.ok}</b><span>OK</span></div>
        <div><b>{summary.fail}</b><span>Fallos</span></div>
        <div><b>{summary.err}</b><span>Errores</span></div>
        <div><b>{summary.shorts}</b><span>Cortos?</span></div>
      </div>

      <div className="grid">
        <section className="card controls">
          <h2>Configuración</h2>
          <label>URL del backend</label>
          <div className="row">
            <input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} />
            <button className="secondary" onClick={pingServer} disabled={running}>Probar</button>
          </div>

          <label>Archivo BSDL</label>
          <div className="fileBox">
            <input type="file" accept=".bsdl,.bsd,.txt" onChange={(e) => setFile(e.target.files?.[0] || null)} disabled={running} />
            <span>{file ? file.name : "Selecciona el BSDL del chip"}</span>
          </div>

          <label>Archivo Netlist</label>
          <div className="fileBox">
            <input type="file" accept=".net,.cir,.csv,.txt,.xml" onChange={(e) => setNetlistFile(e.target.files?.[0] || null)} disabled={running} />
            <span>{netlistFile ? netlistFile.name : "Opcional, pero recomendado"}</span>
          </div>

          <label>Nombre del chip en netlist</label>
          <input value={uutRef} onChange={(e) => setUutRef(e.target.value.toUpperCase())} placeholder="U1" disabled={running} />

          <div className="checks">
            <label><input type="checkbox" checked={options.simple_output} onChange={(e) => updateOption("simple_output", e.target.checked)} /> Salida simple</label>
            <label><input type="checkbox" checked={options.external_line_test} onChange={(e) => updateOption("external_line_test", e.target.checked)} /> Revisar TX/RX/SPI/I2C/GPIO hacia Pi</label>
            <label><input type="checkbox" checked={options.netlist_test} onChange={(e) => updateOption("netlist_test", e.target.checked)} /> Validar netlist</label>
            <label><input type="checkbox" checked={options.external_bidir} onChange={(e) => updateOption("external_bidir", e.target.checked)} /> Probar ambas direcciones</label>
            <label><input type="checkbox" checked={options.no_short_test} onChange={(e) => updateOption("no_short_test", e.target.checked)} /> Saltar cortos generales</label>
            <label><input type="checkbox" checked={options.map_only} onChange={(e) => updateOption("map_only", e.target.checked)} /> Solo mapa, sin JTAG</label>
          </div>

          
          <div className="lineHelp">
            <b>Qué busca automáticamente en el netlist</b>
            <span>UART: TX/RX · SPI: MOSI/MISO/SCK/CS · I2C: SDA/SCL · GPIO: PI.GPIOxx</span>
          </div>

          <button className="primary" onClick={startTest} disabled={running || !file}>{running ? "Revisión en curso..." : "Iniciar revisión"}</button>

          {progress && <div className="progressWrap"><div className="progressInfo"><span>Progreso</span><b>{progress.current}/{progress.total} · {progress.percent}%</b></div><div className="bar"><div style={{ width: `${progress.percent}%` }} /></div></div>}
          {error && <div className="error">{error}</div>}

          {reports.length > 0 && <div className="reports"><b>Reportes</b>{reports.map((r) => <a key={r.name} href={`${apiUrl}${r.url}`} target="_blank">{r.name}</a>)}</div>}
        </section>

        <section className="card dashboard">
          <div className="panelTitle">Últimos eventos</div>
          <div className="events">
            {events.length === 0 ? <p>Esperando revisión...</p> : events.map((e) => <div key={e.ts + e.text} className={`event ${e.type}`}>{e.text}</div>)}
          </div>
          
          <div className="linePanel">
            <div className="panelTitle">Líneas externas detectadas</div>
            {lineChecks.length === 0 ? (
              <p className="muted">Todavía no se detectaron líneas. Necesitas netlist con algo como U1.PE1 + PI.GPIO15.</p>
            ) : (
              <div className="lineList">
                {lineChecks.map((l) => (
                  <div className={`lineItem ${l.status.toLowerCase()}`} key={l.net}>
                    <b>{l.net}</b>
                    <span>UUT {l.pin} ↔ PI.GPIO{l.gpio}</span>
                    <small>{l.direction}</small>
                    <em>{l.status}</em>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="terminalTop"><span></span><span></span><span></span><b>Salida simplificada</b></div>
          <pre className="terminal" ref={outputRef}>{output || "Esperando revisión..."}</pre>
        </section>
      </div>
    </div>
  );
}

export default App;
