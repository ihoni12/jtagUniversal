import React,{useState}from'react';import{createRoot}from'react-dom/client';import'./style.css';
const defaultApi=`http://${window.location.hostname}:8000`;
function Table({rows,cols}){return <div className="tableWrap"><table><thead><tr>{cols.map(c=><th key={c.k}>{c.t}</th>)}</tr></thead><tbody>{rows?.map((r,i)=><tr key={i}>{cols.map(c=><td key={c.k}>{String(r[c.k]??'')}</td>)}</tr>)}</tbody></table></div>}
function JsonBlock({data}){return <pre>{JSON.stringify(data,null,2)}</pre>}
function App(){const[api,setApi]=useState(defaultApi);const[info,setInfo]=useState(null);const[file,setFile]=useState('');const[busy,setBusy]=useState(false);const[tab,setTab]=useState('resumen');
async function test(){try{let r=await fetch(api);alert((await r.json()).message||'Backend OK')}catch(e){alert('No conecta: '+e.message)}}
async function upload(f){if(!f)return;setBusy(true);const fd=new FormData();fd.append('file',f);try{let r=await fetch(`${api}/upload/bsdl`,{method:'POST',body:fd});let d=await r.json();if(!d.ok){alert(d.error||'Error');return}setInfo(d.info);setFile(d.filename);setTab('resumen')}catch(e){alert('Error subiendo: '+e.message)}finally{setBusy(false)}}
const tabs=['resumen','pines','instrucciones','boundary','atributos','json'];
return <div className="page"><header><div><h1>BSDL Test Station</h1><p>Lee un archivo BSDL y separa la información para JTAG Boundary Scan.</p></div><span className="badge">Python 3.13 OK · Sin CGI</span></header>
<section className="card"><h2>Conexión</h2><div className="row"><label>Backend API<input value={api} onChange={e=>setApi(e.target.value)}/></label><button onClick={test}>Probar</button></div></section>
<section className="card upload"><h2>Subir BSDL</h2><input type="file" accept=".bsdl,.bsd,.txt" onChange={e=>upload(e.target.files[0])}/>{busy&&<p>Analizando...</p>}{file&&<p className="ok">Archivo cargado: <b>{file}</b></p>}</section>
{info&&<section className="card"><div className="tabs">{tabs.map(t=><button className={tab===t?'active':''} onClick={()=>setTab(t)} key={t}>{t}</button>)}</div>
{tab==='resumen'&&<div><h2>Resumen</h2><div className="cards"><div><b>Chip / Entity</b><span>{info.entity||'No encontrado'}</span></div><div><b>IR length</b><span>{info.attributes.INSTRUCTION_LENGTH??'?'}</span></div><div><b>Boundary length</b><span>{info.attributes.BOUNDARY_LENGTH??'?'}</span></div><div><b>IDCODE</b><span>{info.idcode.hex||'No encontrado'}</span></div><div><b>Pines mapeados</b><span>{info.summary.mapped_pins}</span></div><div><b>Celdas boundary</b><span>{info.summary.boundary_cells}</span></div></div><h3>Conteo de celdas</h3><JsonBlock data={info.summary.cell_counts}/></div>}
{tab==='pines'&&<div><h2>Puertos y pines</h2><h3>PIN_MAP</h3><Table rows={info.pin_map} cols={[{k:'port',t:'Puerto'},{k:'package_pin',t:'Pin físico'}]}/><h3>PORT</h3><Table rows={info.ports} cols={[{k:'name',t:'Nombre'},{k:'direction',t:'Dirección'},{k:'type',t:'Tipo'}]}/></div>}
{tab==='instrucciones'&&<div><h2>Instrucciones JTAG</h2><Table rows={info.instructions} cols={[{k:'instruction',t:'Instrucción'},{k:'opcode',t:'Opcode'}]}/><h3>Register Access</h3><JsonBlock data={info.register_access}/></div>}
{tab==='boundary'&&<div><h2>Boundary Register</h2><Table rows={info.boundary_register} cols={[{k:'bit',t:'Bit'},{k:'cell',t:'Celda'},{k:'port',t:'Puerto'},{k:'package_pin',t:'Pin físico'},{k:'function',t:'Función'},{k:'safe',t:'Safe'},{k:'control_cell',t:'Control'},{k:'disable_value',t:'Disable'}]}/></div>}
{tab==='atributos'&&<div><h2>Atributos principales</h2><JsonBlock data={info.attributes}/><h3>IDCODE bits</h3><pre>{info.idcode.bits||'No encontrado'}</pre></div>}
{tab==='json'&&<div><h2>Todo el análisis</h2><JsonBlock data={info}/></div>}
</section>}
<footer>Esto solo lee el BSDL. No prueba el chip ni detecta cortos sin ejecutar JTAG real.</footer></div>}
createRoot(document.getElementById('root')).render(<App/>);
