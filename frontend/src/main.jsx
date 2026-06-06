import React, { useState, useEffect } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

const defaultApi = `http://${window.location.hostname}:8000`;

function App() {
  const [api, setApi] = useState(defaultApi);
  const [uploads, setUploads] = useState({ bsdl: [], firmware: [] });
  const [bsdlFile, setBsdlFile] = useState(null);
  const [firmwareFile, setFirmwareFile] = useState(null);
  const [selectedBsdl, setSelectedBsdl] = useState("");
  const [selectedFirmware, setSelectedFirmware] = useState("");
  const [allowDrive, setAllowDrive] = useState(false);
  const [flashCommand, setFlashCommand] = useState(
    'openocd -f interface/raspberrypi-native.cfg -f target/TU_TARGET.cfg -c "program {firmware} verify reset exit"'
  );
  const [listenPort, setListenPort] = useState(9000);
  const [expectedText, setExpectedText] = useState("OK");
  const [timeoutSeconds, setTimeoutSeconds] = useState(30);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  useEffect(() => {
    refreshUploads();
  }, []);

  async function refreshUploads() {
    try {
      const r = await fetch(`${api}/uploads`);
      setUploads(await r.json());
    } catch (e) {
      console.warn(e);
    }
  }

  async function uploadBsdl() {
    if (!bsdlFile) return alert("Selecciona un BSDL");
    setBusy(true);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", bsdlFile);
      const r = await fetch(`${api}/upload/bsdl`, { method: "POST", body: fd });
      const data = await r.json();
      setResult(data);
      if (data.ok) {
        setSelectedBsdl(data.bsdl_id);
        await refreshUploads();
      }
    } catch (e) {
      alert("Error subiendo BSDL: " + e.message);
    } finally {
      setBusy(false);
    }
  }

  async function analyzeBsdlFile() {
    if (!bsdlFile) return alert("Selecciona un BSDL");
    setBusy(true);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", bsdlFile);
      const r = await fetch(`${api}/jtag/analyze-bsdl`, { method: "POST", body: fd });
      setResult(await r.json());
    } catch (e) {
      alert("Error analizando BSDL: " + e.message);
    } finally {
      setBusy(false);
    }
  }

  async function runJtag() {
    if (!selectedBsdl) return alert("Sube o selecciona un BSDL");
    setBusy(true);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("bsdl_id", selectedBsdl);
      fd.append("allow_drive", allowDrive ? "true" : "false");
      const r = await fetch(`${api}/run/jtag`, { method: "POST", body: fd });
      setResult(await r.json());
    } catch (e) {
      alert("Error ejecutando JTAG: " + e.message);
    } finally {
      setBusy(false);
    }
  }

  async function uploadFirmware() {
    if (!firmwareFile) return alert("Selecciona firmware");
    setBusy(true);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", firmwareFile);
      const r = await fetch(`${api}/upload/firmware`, { method: "POST", body: fd });
      const data = await r.json();
      setResult(data);
      if (data.ok) {
        setSelectedFirmware(data.firmware_id);
        await refreshUploads();
      }
    } catch (e) {
      alert("Error subiendo firmware: " + e.message);
    } finally {
      setBusy(false);
    }
  }

  async function runFunctional() {
    if (!selectedFirmware) return alert("Sube o selecciona firmware");
    setBusy(true);
    setResult(null);
    try {
      const body = {
        name: "Prueba funcional",
        firmware_id: selectedFirmware,
        flash_command: flashCommand,
        listen_host: "0.0.0.0",
        listen_port: Number(listenPort),
        expected_text: expectedText,
        timeout_seconds: Number(timeoutSeconds),
      };

      const r = await fetch(`${api}/run/functional`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      setResult(await r.json());
    } catch (e) {
      alert("Error ejecutando funcional: " + e.message);
    } finally {
      setBusy(false);
    }
  }

  const info = result?.info || result?.bsdl_info || result?.info;
  const plan = result?.auto_test_plan;

  return (
    <div className="page">
      <h1>JTAG Universal Test Station</h1>
      <p className="sub">
        Sistema universal: JTAG desde BSDL + pruebas funcionales con firmware.
      </p>

      <div className="card">
        <h2>Conexión backend</h2>
        <label>
          API
          <input value={api} onChange={(e) => setApi(e.target.value)} />
        </label>
        <button onClick={refreshUploads}>Probar / refrescar</button>
      </div>

      <div className="card">
        <h2>1. Revisión JTAG desde BSDL</h2>
        <p>
          El usuario solo sube el BSDL. El programa saca IDCODE, IR length,
          Boundary Register, pines y genera pruebas automáticas.
        </p>

        <label>
          Subir BSDL
          <input
            type="file"
            accept=".bsdl,.bsd,.txt"
            onChange={(e) => setBsdlFile(e.target.files[0])}
          />
        </label>

        <div className="row">
          <button disabled={busy} onClick={analyzeBsdlFile}>Analizar archivo</button>
          <button disabled={busy} onClick={uploadBsdl}>Subir BSDL</button>
        </div>

        <label>
          BSDL guardado
          <select value={selectedBsdl} onChange={(e) => setSelectedBsdl(e.target.value)}>
            <option value="">-- seleccionar --</option>
            {uploads.bsdl?.map((b) => (
              <option key={b.id} value={b.id}>
                {b.filename} {b.info?.entity ? `(${b.info.entity})` : ""}
              </option>
            ))}
          </select>
        </label>

        <label className="check">
          <input
            type="checkbox"
            checked={allowDrive}
            onChange={(e) => setAllowDrive(e.target.checked)}
          />
          Permitir EXTEST para buscar posibles cortos manejando pines
        </label>

        <div className="warn">
          Sin EXTEST la prueba es más segura: scan_chain, IDCODE y SAMPLE.
          Con EXTEST el programa puede manejar pines físicos. Úsalo solo si la placa lo permite.
        </div>

        <button disabled={busy || !selectedBsdl} onClick={runJtag}>
          Ejecutar revisión JTAG
        </button>
      </div>

      <div className="card">
        <h2>2. Prueba funcional con firmware</h2>
        <p>
          Para Ethernet, Wi-Fi, Bluetooth u otra interfaz: sube un firmware de prueba.
          La Pi lo carga y espera que la placa mande un mensaje TCP.
        </p>

        <label>
          Subir firmware
          <input type="file" onChange={(e) => setFirmwareFile(e.target.files[0])} />
        </label>

        <button disabled={busy} onClick={uploadFirmware}>Subir firmware</button>

        <label>
          Firmware guardado
          <select
            value={selectedFirmware}
            onChange={(e) => setSelectedFirmware(e.target.value)}
          >
            <option value="">-- seleccionar --</option>
            {uploads.firmware?.map((f) => (
              <option key={f.id} value={f.id}>{f.filename}</option>
            ))}
          </select>
        </label>

        <label>
          Comando para cargar firmware
          <textarea value={flashCommand} onChange={(e) => setFlashCommand(e.target.value)} />
        </label>
        <small>
          Usa <code>{"{firmware}"}</code> donde debe ir la ruta del archivo.
        </small>

        <div className="grid">
          <label>
            Puerto de escucha
            <input value={listenPort} onChange={(e) => setListenPort(e.target.value)} />
          </label>
          <label>
            Timeout segundos
            <input value={timeoutSeconds} onChange={(e) => setTimeoutSeconds(e.target.value)} />
          </label>
        </div>

        <label>
          Mensaje esperado
          <input value={expectedText} onChange={(e) => setExpectedText(e.target.value)} />
        </label>

        <button disabled={busy || !selectedFirmware} onClick={runFunctional}>
          Ejecutar prueba funcional
        </button>
      </div>

      {busy && <div className="card">Ejecutando...</div>}

      {info && (
        <div className="card">
          <h2>Información BSDL detectada</h2>
          <div className="grid">
            <div><b>Entity:</b> {info.entity || "No detectado"}</div>
            <div><b>IDCODE:</b> {info.idcode_hex || "No detectado"}</div>
            <div><b>IR length:</b> {info.ir_length || "No detectado"}</div>
            <div><b>Boundary length:</b> {info.boundary_length || 0}</div>
            <div><b>Pines:</b> {info.pin_count || 0}</div>
          </div>

          {info.warnings?.length > 0 && (
            <div className="badBox">
              <b>Advertencias:</b>
              {info.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
            </div>
          )}

          <h3>Conteo de pines</h3>
          <pre>{JSON.stringify(info.counts, null, 2)}</pre>
        </div>
      )}

      {plan && (
        <div className="card">
          <h2>Plan automático generado</h2>
          <div>Total pruebas: {plan.total_tests}</div>
          <pre>{JSON.stringify((plan.tests || []).slice(0, 40), null, 2)}</pre>
          <p>Mostrando primeras 40 pruebas.</p>
          <div className="warn">
            {plan.limitations?.map((x, i) => <div key={i}>⚠ {x}</div>)}
          </div>
        </div>
      )}

      {result && !info && (
        <Result r={result} />
      )}

      {result?.steps && (
        <Result r={result} />
      )}
    </div>
  );
}

function Result({ r }) {
  return (
    <div className={r.ok ? "card result ok" : "card result bad"}>
      <h2>{r.ok ? "✅" : "❌"} {r.message || (r.ok ? "OK" : "Falló")}</h2>

      {r.summary && (
        <>
          <h3>Resumen</h3>
          <pre>{JSON.stringify(r.summary, null, 2)}</pre>
        </>
      )}

      {r.steps?.map((s, i) => (
        <div className="step" key={i}>
          <h3>{s.ok === false ? "❌" : "✅"} {s.name}</h3>

          {s.analysis?.findings?.map((f, idx) => (
            <div key={idx} className={f.level === "error" ? "badText" : "okText"}>
              {f.level === "error" ? "❌" : "✅"} {f.message}
            </div>
          ))}

          <pre>{JSON.stringify(s.details || s.raw || {}, null, 2)}</pre>
        </div>
      ))}

      {r.important_note && <div className="warn">{r.important_note}</div>}
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
