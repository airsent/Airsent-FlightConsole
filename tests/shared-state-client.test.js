const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('shared-state.js','utf8');
function client(role){
 class Storage{constructor(){this.map=new Map([['airsentMode',role],['airsentPresenceToken','signed']]);}getItem(k){return this.map.get(k)||null;}setItem(k,v){this.map.set(k,String(v));}}
 const storage=new Storage(),listeners={},events=[],sockets=[];
 class WS{constructor(){this.readyState=1;this.sent=[];sockets.push(this);}send(raw){this.sent.push(JSON.parse(raw));}close(){this.readyState=3;this.onclose();}}
 const window={dispatchEvent:e=>events.push(e)};
 const c={window,Storage,localStorage:storage,WebSocket:WS,URL,URLSearchParams,location:{pathname:'/dashboard.html',origin:'https://console.airsent.tech',protocol:'https:'},document:{addEventListener:(k,f)=>listeners[k]=f,createElement:()=>({style:{},setAttribute(){}}),body:{append(){}}},CustomEvent:class{constructor(type,data){this.type=type;this.detail=data.detail;}},StorageEvent:class{constructor(type,data){Object.assign(this,{type},data);}},Event:class{constructor(type){this.type=type;}},setTimeout:()=>1,clearTimeout(){}};
 vm.runInNewContext(source,c);listeners.DOMContentLoaded();return {window,storage,events,ws:sockets[0]};
}
const key='airsent-failsafe-activation-v1';const values=Object.fromEntries(['airsent-failsafe-local-drafts-v1','airsent-preflight-manual-v1','airsent-parameter-drafts-v1'].map(k=>[k,{}]));Object.assign(values,{[key]:{},'airsent_mission_v1':{home:null,waypoints:[]},'airsent-asc-enabled-v1':false});const versions=Object.fromEntries(Object.keys(values).map(k=>[k,0]));
const snapshot=(c,extra={})=>c.ws.onmessage({data:JSON.stringify({type:'shared_snapshot',values,versions,...extra})});
const op=client('operator');assert.throws(()=>op.window.AirsentShared.set(key,{rc:true}));snapshot(op);op.storage.setItem(key,JSON.stringify({rc:true}));assert.equal(op.ws.sent[0].type,'shared_set');assert.equal(op.ws.sent[0].version,0);assert.equal(op.storage.getItem(key),'{}','local cache waits for server acknowledgement');
snapshot(op,{values:{...values,[key]:{rc:true}},versions:{...versions,[key]:1}});assert.equal(op.storage.getItem(key),'{"rc":true}');assert.ok(op.events.some(e=>e.type==='airsent-shared-change'));
op.ws.close();assert.throws(()=>op.window.AirsentShared.set(key,{}));
const viewer=client('viewer');snapshot(viewer);assert.throws(()=>viewer.storage.setItem(key,'{}'));assert.equal(viewer.ws.sent.length,0);
console.log('PASS: shared settings wait for authoritative snapshot; viewer/offline writes blocked; revisions sent; saved state renders on acknowledgement');
