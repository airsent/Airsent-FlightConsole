/* Server journal is authoritative for real console history. Demo/SITL stay separate. */
(() => {
  const mode=(localStorage.getItem('airsentMode')||'demo').toLowerCase();
  // Every real console (operator / actual / real / viewer) shares one history. Demo and SITL stay separate.
  if (!['operator','actual','real','viewer'].includes(mode) || window.airsentSimulation || new URLSearchParams(location.search).get('trial') === 'demo') return;
  const canNote=mode!=='viewer';
  let ws, timer, ready=false;
  const records=new Map();
  const report=()=>window.dispatchEvent(new CustomEvent('airsent-events', {detail:{events:[...records.values()].sort((a,b)=>a.id-b.id),ready}}));
  window.AirsentEvents={get ready(){return ready},note(text){
    if(!canNote||!ready||ws?.readyState!==1)return false;
    ws.send(JSON.stringify({type:'operator_note',id:crypto.randomUUID(),text}));return true;
  },refresh(){
    // Reload the whole shared history from the server.
    records.clear();ready=false;report();
    try{ws?.close();}catch{}
  }};
  function connect(){
    const url=new URL('/events',location.origin);url.protocol=location.protocol==='https:'?'wss:':'ws:';
    const socket=ws=new WebSocket(url.href);
    let deadline=setTimeout(()=>socket.close(),8000);
    socket.onopen=()=>socket.send(JSON.stringify({type:'events_auth',token:localStorage.getItem('airsentPresenceToken')||''}));
    socket.onmessage=message=>{
      if(socket!==ws)return;
      try{
        const data=JSON.parse(message.data);if(data.type!=='events')return;
        clearTimeout(deadline);clearTimeout(timer);timer=setTimeout(()=>socket.close(),8000);
        const changed=!ready || data.events.some(event=>!records.has(event.id));
        ready=true;for(const event of data.events)records.set(event.id,event);if(changed)report();
      }catch{socket.close();}
    };
    socket.onerror=()=>socket.close();
    socket.onclose=()=>{if(socket!==ws)return;clearTimeout(deadline);clearTimeout(timer);ready=false;report();setTimeout(connect,2000);};
  }
  connect();
})();
