const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function browser(savedRole) {
 const storage = new Map([['airsentMode',savedRole],['airsentPresenceToken','signed-session'],['airsentPresenceId','device-a']]);
 let socket, redirected;
 const nodes=Object.fromEntries(['consoleUsersTotal','consoleOperators','consoleViewers'].map(id=>[id,{textContent:'',title:''}]));
 class WS {static OPEN=1;static CLOSED=3;static CONNECTING=0;constructor(){socket=this;this.readyState=1;this.sent=[];}send(s){this.sent.push(JSON.parse(s));}close(){this.readyState=3;}}
 const context={localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},window:{addEventListener(){},dispatchEvent(){}},document:{getElementById:id=>nodes[id]||null,addEventListener(){}},location:{origin:'https://console.airsent.tech',pathname:'/dashboard.html',replace:url=>redirected=url},URL,WebSocket:WS,Date,CustomEvent:class{},setInterval(){},clearInterval(){},setTimeout(){},clearTimeout(){}};
 vm.runInNewContext(fs.readFileSync('presence.js','utf8'),context);
 socket.onopen();
 return {storage,socket,redirected:()=>redirected};
}
const mismatched=browser('operator');
assert.equal(mismatched.socket.sent[0].token,'signed-session');
assert.equal(mismatched.socket.sent[0].role,undefined);
mismatched.socket.onmessage({data:JSON.stringify({type:'presence_registered',role:'viewer',device:'a'})});
assert.equal(mismatched.storage.get('airsentMode'),'operator');
assert.equal(mismatched.storage.has('airsentPresenceToken'),false);
assert.equal(mismatched.redirected(),'index.html?reason=role-mismatch');
const correct=browser('operator');
correct.socket.onmessage({data:JSON.stringify({type:'presence_registered',role:'operator',device:'b'})});
assert.equal(correct.redirected(),undefined);
assert.equal(correct.storage.get('airsentPresenceToken'),'signed-session');
console.log('PASS: stale viewer token after operator login forces verified sign-in; correct operator session retained');
