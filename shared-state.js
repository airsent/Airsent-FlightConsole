/* Operator-authored console settings; aircraft telemetry remains authoritative. */
(() => {
  const role=()=>localStorage.getItem('airsentMode');
  if(new URLSearchParams(location.search).get('trial')==='demo' || (['localhost','127.0.0.1'].includes(location.hostname)&&localStorage.getItem('airsentSITL')==='1'))return;
  if(!['operator','viewer'].includes(role()) || ['/', '/index.html','/signin.html'].includes(location.pathname)) return;
  const keys=['airsent-failsafe-activation-v1','airsent-failsafe-local-drafts-v1','airsent_mission_v1','airsent-preflight-manual-v1','airsent-parameter-drafts-v1','airsent-asc-enabled-v1'];
  const nativeSet=Storage.prototype.setItem, pending=new Set();
  let ws,ready=false,versions={},status='CONNECTING',notice,deadline;
  const report=text=>{status=text;if(notice)notice.textContent='SHARED SETTINGS · '+text;window.dispatchEvent(new Event('airsent-shared-status'));};
  const editable=()=>role()==='operator'&&ready&&ws?.readyState===1;
  const event=(key,value)=>window.dispatchEvent(new CustomEvent('airsent-shared-change',{detail:{key,value}}));
  window.AirsentShared={editable,get ready(){return ready},set(key,value){
    if(!keys.includes(key))throw Error('Unknown shared setting');
    if(!editable())throw Error('Shared settings are offline or read only');
    if(pending.has(key))throw Error('Wait for the current change to be saved');
    pending.add(key);report('SAVING');
    clearTimeout(deadline);deadline=setTimeout(()=>{ready=false;report('OFFLINE · SAVE NOT CONFIRMED');ws.close();},6000);
    ws.send(JSON.stringify({type:'shared_set',key,value,version:versions[key]}));
  }};
  Storage.prototype.setItem=function(key,value){
    if(this===localStorage&&keys.includes(key)){window.AirsentShared.set(key,JSON.parse(value));return;}
    return nativeSet.call(this,key,value);
  };
  const connect=()=>{
    const url=new URL('/shared-state',location.origin);url.protocol=location.protocol==='https:'?'wss:':'ws:';
    const socket=ws=new WebSocket(url.href);
    deadline=setTimeout(()=>{if(!ready)socket.close();},8000);
    socket.onopen=()=>socket.send(JSON.stringify({type:'shared_auth',token:localStorage.getItem('airsentPresenceToken')||''}));
    socket.onmessage=message=>{
      if(socket!==ws)return;
      try{
        const data=JSON.parse(message.data);if(data.type!=='shared_snapshot')return;
        const first=!ready;versions=data.versions;pending.clear();ready=true;clearTimeout(deadline);
        for(const key of keys){
          const value=data.values[key];const old=localStorage.getItem(key),encoded=JSON.stringify(value);
          if(first&&versions[key]===0&&old&&old!==encoded){
            let backup={};try{backup=JSON.parse(localStorage.getItem('airsent-shared-local-backup-v1')||'{}')}catch{}
            if(!(key in backup)){backup[key]=JSON.parse(old);nativeSet.call(localStorage,'airsent-shared-local-backup-v1',JSON.stringify(backup));}
          }
          nativeSet.call(localStorage,key,encoded);
          if(old!==encoded){window.dispatchEvent(new StorageEvent('storage',{key,newValue:encoded,oldValue:old,storageArea:localStorage}));}
          if(old!==encoded||first||data.error)event(key,value);
        }
        report(data.error?'NOT SAVED · '+data.error:'LIVE');
      }catch(error){ready=false;report('ERROR · RECONNECTING');socket.close();}
    };
    socket.onerror=()=>socket.close();
    socket.onclose=()=>{if(socket!==ws)return;ready=false;pending.clear();clearTimeout(deadline);report('OFFLINE · CHANGES DISABLED');setTimeout(connect,2500);};
  };
  for(const name of ['click','input','change','submit','pointerdown'])document.addEventListener(name,e=>{
    const page=location.pathname.split('/').pop();
    const selector=page==='dashboard.html'?'[data-policy-system]': ['mission.html','parameters.html','preflight.html','failsafe.html'].includes(page)?window.airsentMutationSelector:'';
    if(selector&&e.target.closest?.(selector)&&(!editable()||pending.size)){
      e.preventDefault();e.stopImmediatePropagation();
    }
  },true);
  document.addEventListener('DOMContentLoaded',()=>{
    notice=document.createElement('div');notice.setAttribute('role','status');notice.id='sharedSettingsStatus';
    if(!['dashboard.html','mission.html','parameters.html','preflight.html','failsafe.html'].includes(location.pathname.split('/').pop()))notice.hidden=true;
    notice.style.cssText='position:fixed;bottom:8px;right:8px;z-index:10000;background:#08151e;color:#8fcce5;padding:5px 9px;border:1px solid #284653;font:10px monospace;max-width:65vw';
    document.body.append(notice);report(status);connect();
  });
})();
