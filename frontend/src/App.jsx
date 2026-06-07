import { useMemo, useRef, useState } from "react";
import "./App.css";

function getDefaultApiUrl() {
  const host = window.location.hostname;
  return `http://${host}:5000`;
}

function App() {
  const [apiUrl, setApiUrl] = useState(getDefaultApiUrl());
  const [file, setFile] = useState(null);
  const [netlistFile, setNetlistFile] = useState(null);
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const outputRef = useRef(null);

  const progress = useMemo(() => {
    const matches = output.match(/\[(\d+)\/(\d+)\] Probando/g);
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
    if (!file) {
      setError("Selecciona un archivo BSDL primero.");
      return;
    }

    setRunning(true);
    setDone(false);
    setOutput("");
    setError("");

    const formData = new FormData();
    formData.append("bsdl", file);
    if (netlistFile) formData.append("netlist", netlistFile);

    try {
      appendOutput(netlistFile ? "Subiendo archivo BSDL y Netlist a la Raspberry...\n" : "Subiendo archivo BSDL a la Raspberry...\n");

      const res = await fetch(`${apiUrl}/api/start`, {
        method: "POST",
        body: formData,
      });

      const data = await res.json();

      if (!data.ok) {
        throw new Error(data.error || "No se pudo iniciar la revision.");
      }

      appendOutput(`Revision iniciada. Job: ${data.job_id}\n\n`);

      const source = new EventSource(`${apiUrl}/api/progress/${data.job_id}`);

      source.onmessage = (event) => {
        if (event.data === "__DONE__") {
          source.close();
          setRunning(false);
          setDone(true);
          return;
        }

        appendOutput(event.data.replaceAll("\\n", "\n"));
      };

      source.onerror = () => {
        source.close();
        setRunning(false);
        setError("Se corto la conexion con el servidor de progreso.");
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
      if (data.ok) appendOutput("Servidor conectado correctamente.\n");
      else setError("El servidor respondio, pero no esta OK.");
    } catch {
      setError("No pude conectar con el backend. Revisa la IP y que app.py este corriendo.");
    }
  }

  return (
    <div className="page">
      <div className="hero">
        <div>
          <p className="eyebrow">Raspberry Pi + OpenOCD</p>
          <h1>Estacion JTAG Boundary Scan</h1>
          <p className="subtitle">
            Sube un archivo BSDL desde tu computadora, ejecuta la revision en la Raspberry Pi y mira el progreso en vivo.
          </p>
        </div>
        <div className={running ? "status running" : done ? "status done" : "status idle"}>
          {running ? "Revisando" : done ? "Terminado" : "Listo"}
        </div>
      </div>

      <div className="grid">
        <section className="card controls">
          <label>URL del backend</label>
          <div className="row">
            <input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} />
            <button className="secondary" onClick={pingServer} disabled={running}>Probar</button>
          </div>

          <label>Archivo BSDL</label>
          <div className="fileBox">
            <input
              type="file"
              accept=".bsdl,.bsd,.txt"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              disabled={running}
            />
            <span>{file ? file.name : "Ningun archivo seleccionado"}</span>
          </div>

          <label>Archivo Netlist opcional</label>
          <div className="fileBox">
            <input
              type="file"
              accept=".net,.cir,.csv,.txt,.xml"
              onChange={(e) => setNetlistFile(e.target.files?.[0] || null)}
              disabled={running}
            />
            <span>{netlistFile ? netlistFile.name : "Sin netlist: revision general"}</span>
          </div>

          <button className="primary" onClick={startTest} disabled={running || !file}>
            {running ? "Revision en curso..." : "Iniciar revision"}
          </button>

          {progress && (
            <div className="progressWrap">
              <div className="progressInfo">
                <span>Progreso</span>
                <b>{progress.current}/{progress.total} · {progress.percent}%</b>
              </div>
              <div className="bar">
                <div style={{ width: `${progress.percent}%` }} />
              </div>
            </div>
          )}

          {error && <div className="error">{error}</div>}
        </section>

        <section className="card terminalCard">
          <div className="terminalTop">
            <span></span><span></span><span></span>
            <b>Salida de revision</b>
          </div>
          <pre className="terminal" ref={outputRef}>{output || "Esperando revision..."}</pre>
        </section>
      </div>
    </div>
  );
}

export default App;
