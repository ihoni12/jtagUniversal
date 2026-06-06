import { useState } from "react";

const API = `http://${window.location.hostname}:8000`;

export default function App() {
  const [file, setFile] = useState(null);
  const [allowExtest, setAllowExtest] = useState(false);
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState("Esperando archivo BSDL");
  const [result, setResult] = useState(null);

  async function startTest() {
    if (!file) {
      alert("Primero sube un archivo BSDL");
      return;
    }

    setRunning(true);
    setResult(null);
    setPhase("Empezó la revisión...");

    const form = new FormData();
    form.append("file", file);
    form.append("allow_extest", allowExtest ? "true" : "false");

    try {
      const response = await fetch(`${API}/jtag/run`, {
        method: "POST",
        body: form,
      });

      const data = await response.json();
      setResult(data);
      setPhase("Terminó la revisión");
    } catch (err) {
      setResult({
        report: {
          ok: false,
          status: "FALLÓ",
          errors: ["No se pudo conectar con el backend: " + err.message],
          warnings: [],
          ok_items: [],
        },
      });
      setPhase("Terminó con error");
    }

    setRunning(false);
  }

  const report = result?.report;

  return (
    <div className="page">
      <div className="hero">
        <h1>JTAG Chip Tester</h1>
        <p>Sube un BSDL y ejecuta una revisión JTAG básica del chip.</p>
      </div>

      <div className="card">
        <h2>1. Subir BSDL</h2>
        <input
          type="file"
          accept=".bsdl,.bsd,.txt"
          onChange={(e) => setFile(e.target.files[0])}
        />

        {file && <div className="file">Archivo: {file.name}</div>}

        <label className="check">
          <input
            type="checkbox"
            checked={allowExtest}
            onChange={(e) => setAllowExtest(e.target.checked)}
          />
          Usar EXTEST para buscar posibles cortos
        </label>

        <div className="note">
          Sin EXTEST la prueba es segura: IDCODE + SAMPLE. Con EXTEST el sistema puede manejar pines.
        </div>

        <button disabled={running || !file} onClick={startTest}>
          {running ? "Revisando..." : "Empezar revisión"}
        </button>
      </div>

      <div className="card status">
        <h2>Estado</h2>
        <div className={running ? "running" : "done"}>{phase}</div>
      </div>

      {report && (
        <div className={`card result ${report.ok ? "pass" : "fail"}`}>
          <h2>Resultado: {report.status}</h2>

          <div className="summary">
            <div><b>Chip:</b> {report.chip || "No detectado"}</div>
            <div><b>IDCODE:</b> {report.idcode || "No detectado"}</div>
            <div><b>IR:</b> {report.ir_length || "No detectado"}</div>
            <div><b>Boundary:</b> {report.boundary_length || 0}</div>
            <div><b>Pines:</b> {report.pin_count || 0}</div>
          </div>

          {report.errors?.length > 0 && (
            <div className="box bad">
              <h3>Errores</h3>
              {report.errors.map((e, i) => <div key={i}>❌ {e}</div>)}
            </div>
          )}

          {report.warnings?.length > 0 && (
            <div className="box warn">
              <h3>Avisos</h3>
              {report.warnings.map((w, i) => <div key={i}>⚠️ {w}</div>)}
            </div>
          )}

          {report.ok_items?.length > 0 && (
            <div className="box good">
              <h3>OK</h3>
              {report.ok_items.map((o, i) => <div key={i}>✅ {o}</div>)}
            </div>
          )}

          <details>
            <summary>Ver detalles técnicos</summary>
            <pre>{JSON.stringify(result.details, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  );
}
