import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

const defaultApi = `http://${window.location.hostname}:8000`;

function App(){
  const [api,setApi]=useState(defaultApi);
  const [modules,setModules]=useState([]);
  const [uploads,setUploads]=useState({bsdl:[], firmware:[]});
  const [busy,setBusy]=useState(false);

  async function refreshUploads(){
    try{ const r=await fetch(`${api}/uploads`); setUploads(await r.json()); }catch(e){alert('No conecta con backend: '+e.message)}
  }

  async function uploadFile(kind, file, moduleId){
    const fd=new FormData(); fd.append('file', file);
    const r=await fetch(`${api}/upload/${kind}`, {method:'POST', body:fd});
    const data=await r.json();
    if(!data.ok) { alert(data.error || 'Error al subir'); return; }
    await refreshUploads();
    if(kind==='bsdl') updateConfig(moduleId, 'bsdl_id', data.bsdl_id);
    if(kind==='firmware') updateConfig(moduleId, 'firmware_id', data.firmware_id);
  }

  function add(type){
    const base = type==='jtag_bsdl' ? {name:'JTAG Básico', openocd_command:''} : {name:'Prueba funcional', flash_command:'openocd -f interface/raspberrypi-native.cfg -f target/TU_TARGET.cfg -c "program {firmware} verify reset exit"', listen_host:'0.0.0.0', listen_port:9000, expected_text:'OK', timeout_seconds:30};
    setModules([...modules,{id:Date.now(), type, open:true, config:base, result:null}]);
  }
  function updateConfig(id,k,v){ setModules(ms=>ms.map(m=>m.id===id?{...m, config:{...m.config,[k]:v}, result:null}:m)); }
  function del(id){ setModules(modules.filter(m=>m.id!==id)); }
  function dup(m){ setModules([...modules,{...m,id:Date.now(),open:true,result:null,config:{...m.config,name:(m.config.name||'Módulo')+' copia'}}]); }
  function validate(m){
    const c=m.config, e=[];
    if(!c.name) e.push('Falta nombre');
    if(m.type==='jtag_bsdl' && !c.bsdl_id) e.push('Falta BSDL');
    if(m.type==='functional_firmware'){
      if(!c.firmware_id) e.push('Falta firmware');
      if(!c.flash_command) e.push('Falta comando para cargar firmware');
      if(!c.expected_text) e.push('Falta mensaje esperado');
    }
    return e;
  }
  async function run(m){
    const errors=validate(m); if(errors.length){alert(errors.join('\n')); return;}
    setBusy(true);
    try{
      const url=m.type==='jtag_bsdl'?'/run/jtag':'/run/functional';
      const body=m.type==='jtag_bsdl'?{
        name:m.config.name, bsdl_id:m.config.bsdl_id, openocd_command:m.config.openocd_command || null,
        do_boundary_info:true, do_idcode_check:true
      }:{
        name:m.config.name, firmware_id:m.config.firmware_id, flash_command:m.config.flash_command,
        listen_host:m.config.listen_host || '0.0.0.0', listen_port:Number(m.config.listen_port||9000),
        expected_text:m.config.expected_text || 'OK', timeout_seconds:Number(m.config.timeout_seconds||30)
      };
      const r=await fetch(`${api}${url}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const data=await r.json();
      setModules(ms=>ms.map(x=>x.id===m.id?{...x,result:data,open:true}:x));
    }catch(e){alert('Error ejecutando: '+e.message)} finally{setBusy(false)}
  }
  async function runAll(){ for(const m of modules) await run(m); }
  const globalErrors=modules.flatMap(m=>validate(m).map(x=>({id:m.id,name:m.config.name,error:x})));

  return <div className="page">
    <h1>Universal Test Station</h1>
    <p>JTAG universal con BSDL + pruebas funcionales con firmware de prueba.</p>
    <div className="card row"><label>Backend API <input value={api} onChange={e=>setApi(e.target.value)} /></label><button onClick={refreshUploads}>Probar / refrescar</button></div>
    <div className="card"><h2>Agregar módulo</h2><button onClick={()=>add('jtag_bsdl')}>+ JTAG Básico con BSDL</button><button onClick={()=>add('functional_firmware')}>+ Prueba funcional con firmware</button><button disabled={busy||!modules.length} onClick={runAll}>Ejecutar todo</button></div>
    {globalErrors.length>0 && <div className="card error"><h2>Errores pendientes</h2>{globalErrors.map((e,i)=><div key={i} onClick={()=>setModules(ms=>ms.map(m=>m.id===e.id?{...m,open:true}:m))}>❌ <b>{e.name}</b>: {e.error}</div>)}</div>}
    {modules.map(m=>{
      const errs=validate(m); const status=errs.length?'red':m.result?(m.result.ok?'green':'red'):'yellow';
      return <div key={m.id} className={'module '+status}>
        <div className="head" onClick={()=>setModules(ms=>ms.map(x=>x.id===m.id?{...x,open:!x.open}:x))}><b>{status==='green'?'🟢':status==='yellow'?'🟡':'🔴'} {m.config.name}</b><span>{m.open?'▲':'▼'}</span></div>
        {m.open && <div className="body">
          <label>Nombre <input value={m.config.name||''} onChange={e=>updateConfig(m.id,'name',e.target.value)} /></label>
          {m.type==='jtag_bsdl' && <>
            <h3>JTAG Básico</h3>
            <p>Sube BSDL. El sistema extrae IDCODE, IR length, Boundary length y genera revisiones según tipos de pines.</p>
            <label>Archivo BSDL <input type="file" onChange={e=>e.target.files[0]&&uploadFile('bsdl', e.target.files[0], m.id)} /></label>
            <label>BSDL seleccionado <select value={m.config.bsdl_id||''} onChange={e=>updateConfig(m.id,'bsdl_id',e.target.value)}><option value="">-- seleccionar --</option>{uploads.bsdl.map(b=><option key={b.id} value={b.id}>{b.filename} {b.info?.entity?`(${b.info.entity})`:''}</option>)}</select></label>
            <label>Comando OpenOCD/JTAG opcional <input value={m.config.openocd_command||''} onChange={e=>updateConfig(m.id,'openocd_command',e.target.value)} placeholder={'openocd -f interface/raspberrypi-native.cfg -f target/xxx.cfg -c "init; scan_chain; shutdown"'} /></label>
          </>}
          {m.type==='functional_firmware' && <>
            <h3>Prueba funcional</h3>
            <p>Sube firmware que active la interfaz a probar y mande un mensaje TCP a la Raspberry.</p>
            <label>Firmware <input type="file" onChange={e=>e.target.files[0]&&uploadFile('firmware', e.target.files[0], m.id)} /></label>
            <label>Firmware seleccionado <select value={m.config.firmware_id||''} onChange={e=>updateConfig(m.id,'firmware_id',e.target.value)}><option value="">-- seleccionar --</option>{uploads.firmware.map(f=><option key={f.id} value={f.id}>{f.filename}</option>)}</select></label>
            <label>Comando para cargar firmware <textarea value={m.config.flash_command||''} onChange={e=>updateConfig(m.id,'flash_command',e.target.value)} /></label>
            <small>Usa <code>{'{firmware}'}</code> donde va la ruta del archivo.</small>
            <div className="grid"><label>Puerto escucha <input value={m.config.listen_port||9000} onChange={e=>updateConfig(m.id,'listen_port',e.target.value)} /></label><label>Timeout segundos <input value={m.config.timeout_seconds||30} onChange={e=>updateConfig(m.id,'timeout_seconds',e.target.value)} /></label></div>
            <label>Mensaje esperado <input value={m.config.expected_text||''} onChange={e=>updateConfig(m.id,'expected_text',e.target.value)} /></label>
          </>}
          {errs.length>0 && <div className="smallErr">{errs.map((e,i)=><div key={i}>❌ {e}</div>)}</div>}
          <div className="actions"><button disabled={busy} onClick={()=>run(m)}>Ejecutar módulo</button><button onClick={()=>dup(m)}>Duplicar</button><button onClick={()=>del(m.id)}>Eliminar</button></div>
          {m.result && <Result r={m.result}/>} 
        </div>}
      </div>
    })}
  </div>
}
function Result({r}){return <div className={r.ok?'result ok':'result bad'}><h3>{r.ok?'✅':'❌'} {r.message}</h3>{r.steps?.map((s,i)=><div className="step" key={i}><b>{s.ok?'✅':'❌'} {s.name}</b><pre>{JSON.stringify(s.details,null,2)}</pre></div>)}</div>}
createRoot(document.getElementById('root')).render(<App/>);
