import { useEffect, useMemo, useRef, useState } from "react";
import "./App.css";

function getDefaultApiUrl() {
  const host = window.location.hostname;
  return `http://${host}:5000`;
}

function parseEvent(data) {
  try { return JSON.parse(data); } catch { return { type: "log", text: data }; }
}

function calculateResultCounts(text) {
  const lines = text.split("\n").map((x) => x.trim()).filter(Boolean);
  let ok = 0, fail = 0, err = 0, shorts = 0;

  for (const line of lines) {
    // Contar sólo líneas de resultado real, no textos explicativos como "FAIL/ERROR".
    const summary = line.match(/Resultado .*?: OK (\d+) \| FAIL (\d+) \| ERROR (\d+)/i);
    if (summary) {
      ok += Number(summary[1]);
      fail += Number(summary[2]);
      err += Number(summary[3]);
      continue;
    }

    const detail = line.match(/Detalle UART: .* · (OK|FAIL|ERROR)$/i) || line.match(/Detalle conexión: .* · (OK|FAIL|ERROR)$/i);
    if (detail) {
      const status = detail[1].toUpperCase();
      if (status === "OK") ok += 1;
      if (status === "FAIL") fail += 1;
      if (status === "ERROR") err += 1;
      continue;
    }

    if (/CORTO_SOSPECHOSO|\[CORTO\?\]/.test(line)) shorts += 1;
    if (/^\[OK\]/.test(line)) ok += 1;
    if (/^\[FAIL\]/.test(line)) fail += 1;
    if (/^\[ERROR\]/.test(line) || /^ERROR:/.test(line)) err += 1;
  }

  return { ok, fail, err, shorts };
}

function cleanConsoleForUser(text) {
  const lines = text.split("\n").map((x) => x.trim()).filter(Boolean);
  const { ok, fail, err } = calculateResultCounts(text);
  const important = lines.filter((l) =>
    /Revisión terminada|Reportes guardados|Pin .*:|CORTO|Lineas con problema|Problemas por NET|UART eléctrico|Resultado UART|Resultado conexión|Detalle UART|Detalle conexión|Línea:|Conexión:|OK:|FAIL:|ERROR:|Código:/.test(l)
  ).filter((l) => !/^SCAN CHAIN:?$/i.test(l) && !/^IDCODE:?$/i.test(l)).slice(-30);
  const state = err ? "Terminó con errores" : fail ? "Terminó con fallos" : "Terminó correctamente";
  return [
    `Resultado: ${state}`,
    `OK detectados: ${ok}`,
    `Fallos detectados: ${fail}`,
    `Errores detectados: ${err}`,
    "",
    "Detalle importante:",
    ...(important.length ? important : ["No hubo mensajes importantes adicionales."])
  ].join("\n");
}


function statusClass(status) {
  const s = String(status || "").toUpperCase();
  if (s.includes("PASS") || s === "OK") return "pass";
  if (s.includes("AVISO") || s.includes("SKIPPED") || s.includes("NO_COMPROBABLE")) return "warn";
  if (s.includes("FAIL") || s.includes("STUCK") || s.includes("CORTO")) return "fail";
  if (s.includes("ERROR")) return "error";
  return "idle";
}

function worstStatus(a, b) {
  const rank = { idle: 0, pass: 1, warn: 2, fail: 3, error: 4 };
  const ca = statusClass(a);
  const cb = statusClass(b);
  return rank[cb] > rank[ca] ? b : a;
}

function shortDetail(text) {
  if (!text) return "Sin errores detectados.";
  return text
    .replace("El pin quedó pegado a 0: ", "")
    .replace("El pin quedó pegado a 1: ", "")
    .replace("Posible ", "Posible ")
    .trim();
}

function categoryFromText(revision, reason, extra) {
  const t = `${revision || ""} ${reason || ""} ${extra || ""}`.toLowerCase();
  if (t.includes("pegado a 0") || t.includes("stuck-at-0")) return "stuck0";
  if (t.includes("pegado a 1") || t.includes("stuck-at-1")) return "stuck1";
  if (t.includes("corto")) return "short";
  if (t.includes("abierto")) return "open";
  if (t.includes("conect") || t.includes("uart") || t.includes("gpio") || t.includes("net_")) return "connection";
  if (t.includes("cambio") || t.includes("0/1")) return "change";
  return "review";
}

function createEmptyPinRow(pin, extra = "") {
  return {
    pin,
    extra,
    status: "PASS",
    change: "PASS",
    stuck0: "PASS",
    stuck1: "PASS",
    short: "PASS",
    open: "PASS",
    connection: "PASS",
    details: [],
  };
}

function addProblemToRow(row, category, status, revision, reason) {
  const normalized = String(status || "PASS").toUpperCase();
  row.status = worstStatus(row.status, normalized);
  if (category in row) row[category] = worstStatus(row[category], normalized);
  if (normalized !== "PASS" || reason) {
    row.details.push({ status: normalized, revision: revision || "Revisión", reason: shortDetail(reason) });
  }
}

function parseFinalReport(text) {
  const lines = text.split("\n").map((x) => x.trim()).filter(Boolean);
  const getValue = (prefix) => {
    const row = lines.find((l) => l.toLowerCase().startsWith(prefix.toLowerCase()));
    return row ? row.split(":").slice(1).join(":").trim() : "";
  };
  const status = getValue("Estado") || "Sin resultado";
  const reviewType = getValue("Tipo de revisión") || "Revisión";
  const chip = getValue("Placa / chip") || "No detectado";
  const pinsAvailable = getValue("Pines disponibles en el BSDL");
  const pinsUsed = getValue("Pines usados en esta prueba");
  const pass = getValue("PASS") || "0";
  const aviso = getValue("AVISO") || "0";
  const fail = getValue("FAIL") || "0";
  const error = getValue("ERROR") || "0";

  const tests = [];
  const testStart = lines.indexOf("Revisiones realizadas:");
  if (testStart >= 0) {
    for (let i = testStart + 1; i < lines.length; i++) {
      if (!lines[i].startsWith("✓")) break;
      tests.push(lines[i].replace(/^✓\s*/, ""));
    }
  }

  const pinMap = new Map();
  const connectionMap = new Map();
  const rawRows = [];

  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^-\s*([^:]+):\s*(PASS|AVISO|FAIL|ERROR)\s*(?:\((.*)\))?$/i);
    if (!m) continue;
    const pin = m[1].trim();
    const statusLine = m[2].toUpperCase();
    const extra = m[3] || "";
    let revision = "";
    let reason = "";
    for (let j = i + 1; j < Math.min(lines.length, i + 12); j++) {
      if (lines[j].startsWith("- ")) break;
      if (lines[j].startsWith("Revisión:")) revision = lines[j].replace("Revisión:", "").trim();
      if (lines[j].startsWith("Motivo:")) reason = lines[j].replace("Motivo:", "").trim();
    }
    rawRows.push({ pin, status: statusLine, extra, revision, reason });
  }

  for (const r of rawRows) {
    const isConnection = /GPIO|UART|SPI|I2C|NET_/i.test(r.extra) && (/GPIO|NET_/i.test(r.extra) || /UART|SPI|I2C/i.test(r.revision));
    const key = isConnection ? `${r.pin}-${r.extra}` : r.pin;
    const map = isConnection ? connectionMap : pinMap;
    if (!map.has(key)) map.set(key, createEmptyPinRow(r.pin, r.extra));
    const row = map.get(key);
    const category = categoryFromText(r.revision, r.reason, r.extra);
    addProblemToRow(row, category, r.status, r.revision, r.reason);
  }

  const pins = Array.from(pinMap.values()).sort((a, b) => statusClass(b.status).localeCompare(statusClass(a.status)) || a.pin.localeCompare(b.pin));
  const connections = Array.from(connectionMap.values()).sort((a, b) => statusClass(b.status).localeCompare(statusClass(a.status)) || a.pin.localeCompare(b.pin));
  return { status, reviewType, chip, pinsAvailable, pinsUsed, pass, aviso, fail, error, tests, pins, connections };
}

function ProgressView({ output, running }) {
  const lines = output.split("\n").map((x) => x.trim()).filter(Boolean);
  const visible = lines.filter((l) =>
    /Empezó|Inici|Revisión|Revisando|\[\d+\/\d+\]|Stuck|Corto|Abierto|Conectividad|Resultado|PASS|FAIL|ERROR|AVISO|Pin |UART|Conexión|Línea:/i.test(l)
  ).filter((l) => !/^SCAN CHAIN:?$/i.test(l) && !/^IDCODE:?$/i.test(l));

  if (!output) return <div className="emptyResult">Esperando revisión...</div>;
  const last = visible.slice(-18);
  return <div className="friendlyPanel livePanel">
    <div className="friendlyHeader"><b>{running ? "Progreso de la revisión" : "Progreso guardado"}</b><span>{visible.length} eventos</span></div>
    <div className="progressSummaryCards">
      <div><small>Estado</small><b>{running ? "Revisando..." : "Terminado"}</b></div>
      <div><small>Últimos pasos</small><b>{last.length}</b></div>
    </div>
    <div className="progressList scrollBox">
      {last.map((line, i) => <div key={i} className={`progressLine ${statusClass(line)}`}><span>{line}</span></div>)}
    </div>
    {visible.length > last.length && <p className="mutedNote">Mostrando los últimos {last.length} eventos. Usa “Ver log completo” para copiar todo.</p>}
  </div>;
}

function FinalResultView({ output }) {
  const report = parseFinalReport(output);
  const hasFinal = /RESULTADO FINAL|INFORME AVANZADO/i.test(output);
  if (!hasFinal) return <ProgressView output={output} running={false} />;

  return <div className="friendlyPanel finalReport scrollBox reportScroll">
    <div className={`resultBanner ${statusClass(report.status)}`}>
      <div><small>Resultado final</small><b>{report.status}</b></div>
      <span>{report.reviewType}</span>
    </div>

    <div className="simpleInfoGrid">
      <div><small>Placa / chip</small><b>{report.chip}</b></div>
      <div><small>Pines encontrados en BSDL</small><b>{report.pinsAvailable || "-"}</b></div>
      <div><small>Pines tocados por esta prueba</small><b>{report.pinsUsed || "-"}</b></div>
      <div><small>Resultados</small><b>PASS {report.pass} · AVISO {report.aviso || 0} · FAIL {report.fail} · ERROR {report.error}</b></div>
    </div>

    {report.tests.length > 0 && <div className="testChips">
      {report.tests.map((t) => <span key={t}>✓ {t}</span>)}
    </div>}

    <PinProblemMatrix title="Tabla de pines" rows={report.pins} empty="No hay pines normales en este informe." />
    <PinProblemMatrix title="Tabla de conexiones" rows={report.connections} empty="No hay conexiones TX/RX/SPI/I2C/GPIO en este informe." connection />
  </div>;
}

function StatusDot({ value }) {
  const c = statusClass(value || "PASS");
  const label = value || "PASS";
  return <span className={`statusDot ${c}`} title={label}>{label === "PASS" ? "✓" : label === "AVISO" ? "!" : "✕"}</span>;
}

function PinProblemMatrix({ title, rows, empty, connection }) {
  return <div className="resultTableBox matrixBox">
    <div className="tableTitle"><b>{title}</b><span>{rows.length}</span></div>
    {rows.length === 0 ? <p className="emptyTable">{empty}</p> : <div className="tableScroll"><table className="resultTable matrixTable"><thead><tr>
      <th>{connection ? "Conexión" : "Pin"}</th>
      <th>Estado</th>
      <th>Cambio</th>
      <th>Pegado 0</th>
      <th>Pegado 1</th>
      <th>Corto</th>
      <th>Abierto</th>
      <th>Conexión</th>
      <th>Detalle</th>
    </tr></thead><tbody>
      {rows.map((r, i) => <tr key={`${r.pin}-${i}`} className={statusClass(r.status)}>
        <td><b>{r.pin}</b><small>{r.extra}</small></td>
        <td><span className={`pill ${statusClass(r.status)}`}>{r.status}</span></td>
        <td><StatusDot value={r.change} /></td>
        <td><StatusDot value={r.stuck0} /></td>
        <td><StatusDot value={r.stuck1} /></td>
        <td><StatusDot value={r.short} /></td>
        <td><StatusDot value={r.open} /></td>
        <td><StatusDot value={r.connection} /></td>
        <td className="detailCell">{r.details.length ? r.details.map((d, k) => <div key={k}><b>{d.revision}:</b> {d.reason}</div>) : "Sin errores detectados."}</td>
      </tr>)}
    </tbody></table></div>}
  </div>;
}

function App() {
  const [apiUrl] = useState(getDefaultApiUrl());
  const [file, setFile] = useState(null);
  const [netlistFile, setNetlistFile] = useState(null);
  const [savedInfo, setSavedInfo] = useState(null);
  const [uutRef, setUutRef] = useState("U1");
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [reports, setReports] = useState([]);
  const [options, setOptions] = useState({ external_line_test: true, netlist_test: true, no_short_test: false, map_only: false });
  const [board, setBoard] = useState(null);
  const [selectedPin, setSelectedPin] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [uploadOpen, setUploadOpen] = useState(true);
  const [pinFilter, setPinFilter] = useState("");
  const [selectedUartOther, setSelectedUartOther] = useState("");
  const [showRawLog, setShowRawLog] = useState(false);
  const outputRef = useRef(null);

  const summary = useMemo(() => calculateResultCounts(output), [output]);

  const hasBsdl = Boolean(file || savedInfo?.has_bsdl);
  const hasNetlist = Boolean(netlistFile || savedInfo?.has_netlist);

  useEffect(() => {
    async function loadSaved() {
      try {
        const res = await fetch(`${apiUrl}/api/current`);
        const data = await res.json();
        if (data.ok) {
          setSavedInfo(data.data);
          if (data.data?.board) {
            setBoard(data.data.board);
            setSelectedPin(data.data.board.pins?.[0]?.name || null);
          }
        }
      } catch {}
    }
    loadSaved();
  }, [apiUrl]);

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

  const uartOtherCandidates = useMemo(() => {
    if (!currentPin?.uart_pair) return [];
    const role = currentPin.uart_pair.role;
    const opposite = role === "TX" ? "RX" : "TX";
    return (board?.pins || []).filter((p) => p?.uart_pair?.role === opposite);
  }, [board, currentPin]);

  const activeUartOtherPin = useMemo(() => {
    if (!currentPin?.uart_pair) return "";
    const names = uartOtherCandidates.map((p) => p.name);
    const def = currentPin.uart_pair.other_pin || names[0] || "";
    return names.includes(selectedUartOther) ? selectedUartOther : def;
  }, [currentPin, uartOtherCandidates, selectedUartOther]);

  const activeUartMode = useMemo(() => {
    if (!currentPin?.uart_pair || !activeUartOtherPin) return null;
    const other = (board?.pins || []).find((p) => p.name === activeUartOtherPin);
    const same = other?.uart_pair?.id && other.uart_pair.id === currentPin.uart_pair.id;
    return { other, same, id: same ? currentPin.uart_pair.id : "MANUAL" };
  }, [board, currentPin, activeUartOtherPin]);

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

  function appendOutput(text) {
    if (text === "__DONE__") return;
    setOutput((prev) => prev + text);
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
    if (!file && savedInfo?.has_bsdl) formData.append("use_saved", "true");
    Object.entries({ ...options, simple_output: false, external_bidir: false, uut_ref: uutRef || "U1", ...extra })
      .forEach(([k, v]) => formData.append(k, String(v)));
    return formData;
  }

  async function analyzeBoard() {
    if (!hasBsdl) { setError("Selecciona un BSDL primero o usa el último guardado."); return; }
    setError(""); setBoard(null); setSelectedPin(null);
    try {
      appendOutput("Leyendo documentos y creando mapa de pines...\n");
      const res = await fetch(`${apiUrl}/api/analyze`, { method: "POST", body: makeForm() });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "No pude analizar archivos.");
      setBoard(data.data);
      setSelectedPin(data.data.pins?.[0]?.name || null);
      setSavedInfo({ has_bsdl: true, has_netlist: Boolean(netlistFile || data.data.net_count), bsdl_name: file?.name || savedInfo?.bsdl_name, netlist_name: netlistFile?.name || savedInfo?.netlist_name, board: data.data });
      appendOutput(`Mapa listo: ${data.data.chipname}, ${data.data.pin_count} pines, ${data.data.net_count} nets.\n`);
    } catch (err) { setError(err.message || "No pude analizar los archivos."); }
  }

  function consumeJob(jobId) {
    const source = new EventSource(`${apiUrl}/api/progress/${jobId}`);
    source.onmessage = (event) => {
      const payload = parseEvent(event.data);
      if (payload.text === "__DONE__") { source.close(); setRunning(false); setDone(true); loadReports(jobId); return; }
      appendOutput((payload.text || "").replaceAll("\\n", "\n"));
    };
    source.onerror = () => { source.close(); setRunning(false); setError("Se cortó la conexión con el servidor de progreso."); loadReports(jobId); };
  }

  async function startTest() {
    if (!hasBsdl) { setError("Selecciona un BSDL primero o usa el último guardado."); return; }
    setRunning(true); setDone(false); setOutput(""); setReports([]); setError("");
    try {
      appendOutput("Revisión completa iniciada. Mientras corre se muestra toda la información técnica.\n\n");
      const res = await fetch(`${apiUrl}/api/start`, { method: "POST", body: makeForm() });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "No se pudo iniciar la revisión.");
      consumeJob(data.job_id);
    } catch (err) { setRunning(false); setError(err.message || "No pude conectar con la Raspberry Pi."); }
  }

  async function startPinTest(pinName = selectedPin) {
    if (!hasBsdl || !pinName) { setError("Selecciona BSDL y un pin."); return; }
    setRunning(true); setDone(false); setOutput(""); setReports([]); setError("");
    try {
      appendOutput(`Revisión individual del pin ${pinName} iniciada.\n\n`);
      const res = await fetch(`${apiUrl}/api/start-pin`, { method: "POST", body: makeForm({ pin: pinName }) });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "No se pudo iniciar la prueba del pin.");
      consumeJob(data.job_id);
    } catch (err) { setRunning(false); setError(err.message || "No pude conectar con la Raspberry Pi."); }
  }

  async function startSpecialPinTest(pinName = selectedPin) {
    if (!hasBsdl || !hasNetlist || !pinName) { setError("Para probar TX/RX o conexión especial necesitas BSDL, netlist y un pin."); return; }
    setRunning(true); setDone(false); setOutput(""); setReports([]); setError("");
    try {
      appendOutput(`Revisión de conexión especial del pin ${pinName} iniciada.\n\n`);
      const res = await fetch(`${apiUrl}/api/start-special-pin`, { method: "POST", body: makeForm({ pin: pinName }) });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "No se pudo iniciar la prueba especial del pin.");
      consumeJob(data.job_id);
    } catch (err) { setRunning(false); setError(err.message || "No pude conectar con la Raspberry Pi."); }
  }


  async function startUartPairTest() {
    if (!hasBsdl || !hasNetlist || !currentPin?.uart_pair || !activeUartOtherPin) {
      setError("Para revisar UART completo necesitas BSDL, netlist y una pareja TX/RX.");
      return;
    }
    const role = currentPin.uart_pair.role;
    const txPin = role === "TX" ? currentPin.name : activeUartOtherPin;
    const rxPin = role === "RX" ? currentPin.name : activeUartOtherPin;
    const other = activeUartMode?.other;
    const samePair = activeUartMode?.same;
    setRunning(true); setDone(false); setOutput(""); setReports([]); setError("");
    try {
      appendOutput(`Revisión UART completa iniciada: TX ${txPin} / RX ${rxPin}.\n`);
      appendOutput(samePair ? `Pareja detectada automáticamente: ${currentPin.uart_pair.id}.\n\n` : "Pareja manual: úsala sólo si sabes que esos dos pines pertenecen a la misma conexión.\n\n");
      const body = makeForm({
        uart_id: samePair ? currentPin.uart_pair.id : "",
        tx_pin: txPin,
        rx_pin: rxPin,
        other_pin: other?.name || activeUartOtherPin,
      });
      const res = await fetch(`${apiUrl}/api/start-uart-pair`, { method: "POST", body });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "No se pudo iniciar la revisión UART completa.");
      consumeJob(data.job_id);
    } catch (err) { setRunning(false); setError(err.message || "No pude conectar con la Raspberry Pi."); }
  }

  return (
    <div className={`appShell ${sidebarOpen ? "withSidebar" : "closedSidebar"}`}>
      <aside className="sideBar">
        <button className="collapse" onClick={() => setSidebarOpen(!sidebarOpen)}>{sidebarOpen ? "‹" : "›"}</button>
        {sidebarOpen && <>
          <div className="boardName compactBoard">
            <small>Placa / chip</small>
            <b>{board?.chipname || "Sin mapa"}</b>
            <span>{board ? `${board.pin_count} pines · ${board.net_count} nets` : "Sube documentos y analiza"}</span>
          </div>
          <input className="pinSearch" placeholder="Buscar pin" value={pinFilter} onChange={(e) => setPinFilter(e.target.value)} />
          <div className="pinList compactPins">
            {filteredPins.map((p) => (
              <button key={p.name} className={`pinBtn ${selectedPin === p.name ? "active" : ""} ${p.special ? "specialPin" : ""}`} onClick={() => setSelectedPin(p.name)}>
                <b>{p.name}</b>
                <span className="bsdlMini">{p.bsdl_text || (p.functions || []).slice(0, 1).join(" · ") || "BSDL"}</span>
                <span className="pinTags">{p.special && <em>{p.special.label}</em>}{p.uart_pair && <em className="uartTag">{p.uart_pair.id}</em>}</span>
              </button>
            ))}
            {!board && <p className="mutedSide">No hay pines todavía.</p>}
          </div>
        </>}
      </aside>

      <main className="page">
        <div className="hero smallHero">
          <div><p className="eyebrow">Raspberry Pi · OpenOCD · Boundary Scan</p><h1>Estación JTAG Universal</h1></div>
          <div className={`status ${running ? "running" : done ? "done" : "idle"}`}>{running ? "Revisando" : done ? "Terminado" : "Listo"}</div>
        </div>

        <div className="stats"><div><b>{summary.ok}</b><span>OK</span></div><div><b>{summary.fail}</b><span>Fallos</span></div><div><b>{summary.err}</b><span>Errores</span></div><div><b>{summary.shorts}</b><span>Cortos?</span></div></div>

        <div className="grid cleanGrid">
          <section className="card controls compactControls">
            <button className="uploadToggle" onClick={() => setUploadOpen(!uploadOpen)}>{uploadOpen ? "Ocultar documentos" : "Subir documentos"}</button>
            {uploadOpen && <div className="uploadPanel">
              <div className="inlineFile"><label>BSDL</label><input type="file" accept=".bsdl,.bsd,.txt" onChange={(e) => setFile(e.target.files?.[0] || null)} disabled={running} /><span>{file ? file.name : savedInfo?.bsdl_name ? `guardado: ${savedInfo.bsdl_name}` : "no seleccionado"}</span></div>
              <div className="inlineFile"><label>NET</label><input type="file" accept=".net,.cir,.csv,.txt,.xml" onChange={(e) => setNetlistFile(e.target.files?.[0] || null)} disabled={running} /><span>{netlistFile ? netlistFile.name : savedInfo?.netlist_name ? `guardado: ${savedInfo.netlist_name}` : "opcional"}</span></div>
              <div className="miniRow"><label>Chip netlist</label><input value={uutRef} onChange={(e) => setUutRef(e.target.value.toUpperCase())} placeholder="U1" disabled={running} /></div>
              <div className="checks miniChecks">
                <label><input type="checkbox" checked={options.external_line_test} onChange={(e) => updateOption("external_line_test", e.target.checked)} /> TX/RX/SPI/I2C/GPIO hacia Pi</label>
                <label><input type="checkbox" checked={options.netlist_test} onChange={(e) => updateOption("netlist_test", e.target.checked)} /> Validar netlist</label>
                <label><input type="checkbox" checked={options.no_short_test} onChange={(e) => updateOption("no_short_test", e.target.checked)} /> Saltar cortos generales</label>
                <label><input type="checkbox" checked={options.map_only} onChange={(e) => updateOption("map_only", e.target.checked)} /> Solo mapa, sin JTAG</label>
              </div>
            </div>}
            <div className="buttonStack"><button className="secondary" onClick={analyzeBoard} disabled={running || !hasBsdl}>Analizar / cargar pines</button><button className="primary" onClick={startTest} disabled={running || !hasBsdl}>{running ? "Revisión en curso..." : "Revisión completa"}</button></div>
            {progress && <div className="progressWrap"><div className="progressInfo"><span>Progreso</span><b>{progress.current}/{progress.total} · {progress.percent}%</b></div><div className="bar"><div style={{ width: `${progress.percent}%` }} /></div></div>}
            {error && <div className="error">{error}</div>}
            {reports.length > 0 && <div className="reports"><b>Reportes</b>{reports.map((r) => <a key={r.name} href={`${apiUrl}${r.url}`} target="_blank" rel="noreferrer">{r.name}</a>)}</div>}
          </section>

          <section className="card dashboard cleanDashboard">
            <div className="pinInspector">
              <div><div className="panelTitle noPad">Pin seleccionado</div>{currentPin ? <><h2>{currentPin.name}</h2><p className="muted">IN {currentPin.input_bit} · OUT {currentPin.output_bit} · CTRL {currentPin.control_bit ?? "-"}</p><div className="chips">{(currentPin.functions || []).map((f) => <span key={f}>{f}</span>)}</div><p><b>Nets:</b> {(currentPin.nets || []).length ? currentPin.nets.join(", ") : "sin netlist"}</p>{currentPin.external && <p className="externalHint"><b>Conexión:</b> PI.GPIO{currentPin.external.pi_gpio} · {currentPin.external.direction_hint}</p>}</> : <p className="muted">Selecciona un pin de la barra lateral.</p>}</div>
              <div className="pinActions">
                {currentPin?.special && <span className="specialBig">Especial: {currentPin.special.kind}{currentPin.uart_pair ? ` · ${currentPin.uart_pair.id}` : ""}</span>}
                <button className="primary" onClick={() => startPinTest()} disabled={running || !currentPin}>Probar pin</button>
                {currentPin?.special && <button className="secondary" onClick={() => startSpecialPinTest()} disabled={running || !currentPin || !hasNetlist}>Probar conexión {currentPin.special.kind}</button>}
                {currentPin?.uart_pair && <div className="uartBox">
                  <b>UART completo</b>
                  <small>Lo correcto es usar la pareja del mismo número: TX0 con RX0, TX1 con RX1.</small>
                  <label>Segundo pin</label>
                  <select value={activeUartOtherPin} onChange={(e) => setSelectedUartOther(e.target.value)} disabled={running || !hasNetlist}>
                    {uartOtherCandidates.map((p) => <option key={p.name} value={p.name}>{p.name} · {p.uart_pair?.id}{p.name === currentPin.uart_pair.other_pin ? " · recomendado" : ""}</option>)}
                  </select>
                  {activeUartMode && <small className={activeUartMode.same ? "okText" : "warnText"}>{activeUartMode.same ? `Pareja correcta: ${activeUartMode.id}` : "Atención: pareja manual/no mismo UART"}</small>}
                  <button className="primary" onClick={startUartPairTest} disabled={running || !currentPin || !hasNetlist || !activeUartOtherPin}>Revisar UART completo</button>
                </div>}
              </div>
            </div>
            <div className="resultTop"><b>{running ? "Revisión en progreso" : done ? "Informe de revisión" : "Resultado"}</b><div><button className="copyBtn" onClick={() => navigator.clipboard?.writeText(output || "")}>Copiar todo</button><button className="copyBtn" onClick={() => setShowRawLog(!showRawLog)}>{showRawLog ? "Ocultar log" : "Ver log completo"}</button></div></div>
            {running ? <ProgressView output={output} running={running} /> : <FinalResultView output={output} />}
            {showRawLog && <pre className="terminal rawLog" ref={outputRef}>{output || "Sin log todavía."}</pre>}
          </section>
        </div>
      </main>
    </div>
  );
}

export default App;
