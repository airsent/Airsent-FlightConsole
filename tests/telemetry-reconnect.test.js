const fs=require('fs'), vm=require('vm'), assert=require('assert/strict'), path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../telemetry.js'),'utf8');
let now=1000, resets=0, timers=[];
class WS {static OPEN=1;static CONNECTING=0;constructor(){this.readyState=0;}close(){this.closed=true;}}
const c={socket:null,isRealMode:true,isConnecting:false,reconnectTimer:null,lastFreshTelemetryAt:0,telemetryConnectionStartedAt:0,mapHasZoomed:false,
 Date:{now:()=>now},WebSocket:WS, window:{airsentApiUrl:()=> 'wss://example/telem'},console,
 setTimeout:f=>{timers.push(f);return timers.length;},clearTimeout:()=>{},setInterval:()=>{},
 setDisconnectedState:()=>{resets++;},applyTelemetry:()=>{c.lastFreshTelemetryAt=now;}};
vm.createContext(c);
vm.runInContext(source.slice(source.indexOf('function connectTelemetry()'),source.indexOf('// ─── DEMO MODE')),c);
c.connectTelemetry();const first=c.socket;first.readyState=1;first.onopen();
// A console opened while the aircraft is parked must not thrash: it holds the socket
// rather than tearing it down every few seconds waiting for a first frame.
now=20000;c.checkTelemetryFreshness();assert.equal(c.socket,first,'no first frame yet is not a broken socket');
now=31001;c.checkTelemetryFreshness();assert.ok(first.closed);assert.equal(c.socket,null);assert.equal(resets,1);
timers.pop()();const second=c.socket;second.readyState=1;second.onopen();second.onmessage({data:'{}'});
now+=2000;c.checkTelemetryFreshness();assert.equal(c.socket,second,'live socket retained');
// The aircraft drops its relay and is back in ~2 s. That gap must not cost the
// operator their console: this is the flicker the thresholds were raised to stop.
now+=3000;c.checkTelemetryFreshness();assert.equal(c.socket,second,'a brief aircraft gap must not tear down a healthy socket');
second.onmessage({data:'{}'});const delayedClose=second.onclose;
now+=12001;c.checkTelemetryFreshness();assert.equal(c.socket,null);assert.ok(second.closed);
timers.pop()();const third=c.socket;delayedClose();assert.equal(c.socket,third,'old callback must not clear replacement');
third.readyState=1;third.onopen();third.onmessage({data:'{}'});now+=100;c.checkTelemetryFreshness();assert.equal(c.socket,third);
third.onerror();assert.equal(c.socket,null);assert.ok(timers.length>0);
console.log('Passed reconnect checks: parked aircraft, no first frame, brief gap tolerated, active feed, wedged socket recovery, stale callbacks, socket error');
