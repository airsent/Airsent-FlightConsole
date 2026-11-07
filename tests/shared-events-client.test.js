const fs=require('fs'),vm=require('vm'),assert=require('assert');
function setup(role,simulation=false){
 const sockets=[],events=[],timers=[];
 class Socket{constructor(url){this.url=url;this.readyState=1;this.sent=[];sockets.push(this)}send(raw){this.sent.push(JSON.parse(raw))}close(){this.onclose?.()}}
 const window={airsentSimulation:simulation,dispatchEvent:e=>events.push(e)};
 const context={window,localStorage:{getItem:k=>k==='airsentMode'?role:'signed-token'},location:{origin:'https://console.airsent.tech',protocol:'https:',search:''},URL,URLSearchParams,WebSocket:Socket,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail}},setTimeout:fn=>(timers.push(fn),timers.length),clearTimeout(){},crypto:{randomUUID:()=> 'test-uuid'}};
 vm.runInNewContext(fs.readFileSync('shared-events.js','utf8'),context);return {window,sockets,events,timers};
}
for(const role of ['operator','viewer']){
 const {window,sockets,events}=setup(role);const ws=sockets[0];assert.equal(ws.url,'wss://console.airsent.tech/events');ws.onopen();assert.equal(ws.sent[0].type,'events_auth');
 const row={id:1,time:'12:00:00',text:'AIRCRAFT OBSERVED: LOITER / DISARMED'};
 ws.onmessage({data:JSON.stringify({type:'events',events:[row]})});assert.equal(events[0].detail.events[0].text,row.text);
 ws.onmessage({data:JSON.stringify({type:'events',events:[row]})});assert.equal(events.length,1,'duplicate replay should not render twice');
 assert.equal(window.AirsentEvents.note('test'),role==='operator');
 ws.close();assert.equal(window.AirsentEvents.ready,false);assert.equal(events.at(-1).detail.events.length,1,'disconnect must retain history');assert.equal(window.AirsentEvents.note('offline'),false);
}
assert.equal(setup('demo').sockets.length,0);assert.equal(setup('operator',true).sockets.length,0);
const source=fs.readFileSync('script.js','utf8');assert(source.includes('querySelector(".panel-title > span")'),'status must not replace the toolbar');
console.log('PASS: shared journal auth, dedup, retained history on disconnect, operator-only notes, demo/SITL isolation and toolbar preservation');
