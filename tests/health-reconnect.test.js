const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),health=require('../health-model');
const source=fs.readFileSync('health.html','utf8'),code=source.slice(source.indexOf('let ws=null,lastHealthFrame'),source.indexOf("setInterval(()=>txt('hClock'"));
let now=1000,disconnected=0,frames=0;const sockets=[],intervals=[],retries=[];
class WS{constructor(){sockets.push(this);}close(){this.closed=true;}}
const c={window:{airsentApiUrl:()=> 'wss://example/telem',AirsentHealthModel:health},WebSocket:WS,Date:{now:()=>now},isActual:true,setDisconnected:()=>disconnected++,applyData:()=>frames++,addLog(){},console,setTimeout:fn=>retries.push(fn),clearTimeout(){},setInterval:fn=>intervals.push(fn)};
vm.createContext(c);vm.runInContext(code,c);
c.connectWs();now=9000;intervals[0]();assert.ok(sockets[0].closed);assert.equal(disconnected,1,'no first frame must clear the page');
retries.shift()();const ws=sockets[1];ws.onmessage({data:JSON.stringify({telemetry_age:0,pixhawk_connected:true})});assert.equal(frames,1);now+=3600;intervals[0]();assert.ok(ws.closed,'silent open socket must reconnect');
retries.shift()();sockets[2].onerror();assert.equal(disconnected,3);assert.ok(sockets[2].closed);
console.log('PASS: health page clears and reconnects for no initial data, silent open sockets and transport errors');
