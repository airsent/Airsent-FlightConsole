const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('demo-isolation.js','utf8');
let role='demo',reloads=0,network=0,listener;
class WS {static OPEN=1;constructor(url){this.url=url;network++;}addEventListener(){}send(){network++;}close(){this.closed=true;}}
class Peer {constructor(){network++;}close(){this.closed=true;}}
const window={WebSocket:WS,RTCPeerConnection:Peer,fetch:()=>{network++;return Promise.resolve({});},addEventListener:(type,fn)=>listener=fn};
vm.runInNewContext(source,{window,URL,URLSearchParams,document:{addEventListener(){}},location:{href:'https://console.airsent.tech/dashboard.html',search:'',reload:()=>reloads++},localStorage:{getItem:()=>role}});
(async()=>{
 for(const url of ['wss://console.airsent.tech/telem','wss://console.airsent.tech/cmd','wss://console.airsent.tech/simulation/cmd','ws://localhost:8765']) assert.throws(()=>new window.WebSocket(url));
 await assert.rejects(window.fetch('/video/frontcam/whep'));assert.throws(()=>new window.RTCPeerConnection());assert.equal(network,0);
 await window.fetch('https://tile.example/1/2/3.png');assert.equal(network,1);
 new window.WebSocket('wss://console.airsent.tech/presence'); // Login remains accessible.
 role='viewer';assert.throws(()=>new window.WebSocket('wss://console.airsent.tech/cmd'));new window.WebSocket('wss://console.airsent.tech/telem');await window.fetch('/video/frontcam/whep');
 role='operator';const command=new window.WebSocket('wss://console.airsent.tech/cmd');const peer=new window.RTCPeerConnection();command.send('{}');
 role='demo';const before=network;command.send('{}');assert.equal(network,before);assert.ok(command.closed);
 // Simulate a role change in a tab originally opened as operator.
 role='operator';vm.runInNewContext(source,{window:{...window,airsentDemoIsolation:null,addEventListener:(t,fn)=>listener=fn},URL,URLSearchParams,document:{addEventListener(){}},location:{href:'https://console.airsent.tech/dashboard.html',search:'',reload:()=>reloads++},localStorage:{getItem:()=>role}});
 role='demo';listener({key:'airsentMode'});assert.equal(reloads,1);
 for(const file of ['dashboard.html','pilot.html','preflight.html','health.html','sensor-status.html','launch.html','actuators.html']) assert.match(fs.readFileSync(file,'utf8'),/demo-isolation\.js/);
 console.log('PASS: demo blocks aircraft sockets, sends, camera peers and fetches; allows maps/login; role changes reload existing tabs');
})().catch(e=>{console.error(e);process.exit(1)});
