import { useMemo, useRef, useState } from "react";
import "./App.css";

function getDefaultApiUrl() {
  return `http://${window.location.hostname}:5000`;
}

function parseSimpleSummary(output) {
  const external = [];
  const lines = output.split("\n");
  for (const line of lines) {
    const m = line.match(/^(NET[^|]+) \| UUT ([^ ]+) <-> PI\.GPIO(\d+) \| ([^|]+) \| (OK|FAIL|ERROR)/);
    if (m) external.push({ net: m[1].trim(), pin: m[2], gpio: m[3], dir: m[4].trim(), status: m[5] });
  }
  const cortos = (output.match(/CORTO_SOSPECHOSO|CORTO:/g) || []).length;
  const opens = (output.match(/OPEN_POSIBLE|OPEN posible/g) || []).length;
  return { external, cortos, opens };
}

function App() {
  const [apiUrl, setApiUrl] = useState(getDefaultApiUrl());
  const [file, setFile] = useState(null);
  const [netlistFile, setNetlistFile] = useState(null);
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [testShorts, setTestShorts] = useState(false);
  const [testNetlist, setTestNetlist] = useState(false);
  const [testExternal, setTestExternal] = useState(true);
  const [externalBidir, setExternalBidir] = useState(false);
  const [uutRef, setUutRef] = useState("U1");
  const outputRef = useRef(null);

  const summary = useMemo(() => parseSimpleSummary(output), [output]);

  const progress = useMemo(() => {
    const matches = output.match(/\[(\d+)\/(\d+)\]/g);
    if (!matches || matches.length === 0) return null;
    const last = matches[matches.length - 1].match(/\[(\d+)\/(\d+)\]/);
    if (!last) return null;
    const current = Number(last[1]);
    const total = Number(last[2]);
    return { current, total, percent: Math.round((current / total) * 100) };
  }, [output]);

  function appendOutput(text) {
    setOutput((prev) => prev + text);
    setTimeout(() => {
      if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }, 20);
  }

  async function startTest() {
    if (!file) return setError("Selecciona un archivo BSDL primero.");
    if ((testNetlist || testExternal) && !netlistFile) return setError("Para validar netlist o revisar TX/RX necesitas subir un netlist.");

    setRunning(true);
    setDone(false);
    setOutput("");
    setError("");

    const formData = new FormData();
    formData.append("bsdl", file);
    if (netlistFile) formData.append("netlist", netlistFile);
    formData.append("test_shorts", String(testShorts));
    formData.append("test_netlist", String(testNetlist));
    formData.append("test_external", String(testExternal));
    formData.append("external_bidir", String(externalBidir));
    formData.append("uut_ref", uutRef || "U1");

    try {
      appendOutput("Subiendo archivos a la Raspberry...\n");
      const res = await fetch(`${apiUrl}/api/start`, { method: "POST", body: formData });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "No se pudo iniciar la revision.");
      setJobId(data.job_id);
      appendOutput(`Revision iniciada.\n\n`);
      const source = new EventSource(`${apiUrl}/api/progress/${data.job_id}`);
      source.onmessage = (event) => {
        if (event.data === "__DONE__") {
          source.close(); setRunning(false); setDone(true); return;
        }
        appendOutput(event.data.replaceAll("\\n", "\n"));
      };
      source.onerror = () => { source.close(); setRunning(false); setError("Se corto la conexion con el servidor de progreso."); };
    } catch (err) {
      setRunning(false); setError(err.message || "No pude conectar con la Raspberry Pi.");
    }
  }

  async function pingServer() {
    setError("");
    try {
      const res = await fetch(`${apiUrl}/api/ping`);
      const data = await res.json();
      if (data.ok) appendOutput("Servidor conectado correctamente.\n");
      else setError("El servidor respondio, pero no esta OK.");
    } catch { setError("No pude conectar con el backend. Revisa la IP y que app.py este corriendo."); }
  }

  return (
    <div className="page">
      <div className="hero">
        <div>
          <p className="eyebrow">Raspberry Pi + OpenOCD</p>
          <h1>JTAG Universal Test Station</h1>
          <p className="subtitle">Unimos tu lector BSDL/Netlist con la prueba TX/RX/SPI/I2C/GPIO hacia Raspberry. Primero prueba lo externo; después cortos y netlist.</p>
        </div>
        <div className={running ? "status running" : done ? "status done" : "status idle"}>{running ? "Revisando" : done ? "Terminado" : "Listo"}</div>
      </div>

      <div className="grid">
        <section className="card controls">
          <label>URL del backend</label>
          <div className="row"><input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} /><button className="secondary" onClick={pingServer} disabled={running}>Probar</button></div>

          <label>Archivo BSDL</label>
          <div className="fileBox"><input type="file" accept=".bsdl,.bsd,.txt" onChange={(e) => setFile(e.target.files?.[0] || null)} disabled={running}/><span>{file ? file.name : "Ningun archivo seleccionado"}</span></div>

          <label>Archivo Netlist</label>
          <div className="fileBox"><input type="file" accept=".net,.cir,.csv,.txt,.xml" onChange={(e) => setNetlistFile(e.target.files?.[0] || null)} disabled={running}/><span>{netlistFile ? netlistFile.name : "Necesario para TX/RX y netlist"}</span></div>

          <label>Referencia del chip en netlist</label>
          <input value={uutRef} onChange={(e) => setUutRef(e.target.value.toUpperCase())} placeholder="U1" disabled={running}/>

          <div className="checks">
            <label className="check"><input type="checkbox" checked={testExternal} onChange={(e) => setTestExternal(e.target.checked)} disabled={running}/> Revisar TX/RX/SPI/I2C/GPIO hacia Pi</label>
            <label className="check"><input type="checkbox" checked={externalBidir} onChange={(e) => setExternalBidir(e.target.checked)} disabled={running}/> Probar ambas direcciones</label>
            <label className="check"><input type="checkbox" checked={testNetlist} onChange={(e) => setTestNetlist(e.target.checked)} disabled={running}/> Validar conexiones del netlist</label>
            <label className="check"><input type="checkbox" checked={testShorts} onChange={(e) => setTestShorts(e.target.checked)} disabled={running}/> Revisar cortos generales</label>
          </div>

          <button className="primary" onClick={startTest} disabled={running || !file}>{running ? "Revision en curso..." : "Iniciar revision"}</button>

          {progress && <div className="progressWrap"><div className="progressInfo"><span>Progreso</span><b>{progress.current}/{progress.total} · {progress.percent}%</b></div><div className="bar"><div style={{ width: `${progress.percent}%` }} /></div></div>}
          {error && <div className="error">{error}</div>}
        </section>

        <section className="rightCol">
          <div className="summaryGrid">
            <div className="miniCard"><b>{summary.external.length}</b><span>Lineas externas</span></div>
            <div className="miniCard"><b>{summary.external.filter(x => x.status === "OK").length}</b><span>Externas OK</span></div>
            <div className="miniCard"><b>{summary.cortos}</b><span>Cortos/avisos</span></div>
          </div>

          {summary.external.length > 0 && <section className="card externalCard"><h2>Lineas externas detectadas</h2><div className="externalList">{summary.external.map((x, i) => <div className={`externalRow ${x.status.toLowerCase()}`} key={i}><strong>{x.net}</strong><span>UUT {x.pin} ↔ GPIO{x.gpio}</span><em>{x.dir}</em><b>{x.status}</b></div>)}</div></section>}

          <section className="card terminalCard"><div className="terminalTop"><span></span><span></span><span></span><b>Salida completa</b></div><pre className="terminal" ref={outputRef}>{output || "Esperando revision..."}</pre></section>
        </section>
      </div>
    </div>
  );
}

export default App;
