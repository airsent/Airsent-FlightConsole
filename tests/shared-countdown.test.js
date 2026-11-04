const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const code = fs.readFileSync('shared-countdown.js', 'utf8');
function client(mode, launch = false) {
  const nodes = {}, timers = [];
  let time = 0;
  const ids = launch ? ['mainClock','statusBadge','abortReason','btnStart','btnHold','btnAbort','btnReset','cfgMins','cfgSecs','cfgHandoverMins','cfgHandoverSecs','handoffInput','clockSign','progressFill','handoverBadge'] : ['lmClock','lmStatus','lmFaultBar','lmBtnStart','lmBtnHold','lmBtnAbort','lmBtnReset','lmHandoverBadge'];
  ids.forEach(id => nodes[id] = {style:{}, disabled:false, listeners:{}, addEventListener(type, fn) { this.listeners[type] = fn; }});
  let socket;
  class WS { constructor() { socket = this; this.sent = []; } send(raw) { this.sent.push(JSON.parse(raw)); } close() {} }
  const context = {localStorage:{getItem:()=>mode}, sessionStorage:{getItem:()=> 'test-secret', setItem(){}, removeItem(){}}, document:{getElementById:id=>nodes[id],activeElement:null}, performance:{now:()=>time}, location:{origin:'https://console.airsent.tech',protocol:'https:'}, URL, WebSocket:WS, setInterval:fn=>timers.push(fn),setTimeout(){}, window:{confirm:()=>true,alert(){},prompt:()=> 'test-secret'}};
  vm.runInNewContext(code, context);
  const receive = data => socket.onmessage({data:JSON.stringify(data)});
  socket.onopen();
  const state = {type:'sequence_state',state:'standby',total:180,remaining:180,handoverAt:120,launchElapsed:0,revision:0};
  receive(state);
  return {nodes,socket,receive,state,advance(ms) {time += ms; timers.forEach(fn=>fn());}};
}
for (const launch of [false,true]) {
  const viewer = client('viewer', launch);
  const start = launch ? 'btnStart' : 'lmBtnStart';
  assert.equal(viewer.nodes[start].disabled, true);
  viewer.nodes[start].listeners.click();
  assert.equal(viewer.socket.sent.length, 0);
  const op = client('operator', launch);
  assert.equal(op.nodes[start].disabled, false);
  op.nodes[start].listeners.click();
  assert.equal(op.socket.sent[0].type, 'sequence_auth');
  op.receive({type:'sequence_auth',authorized:true});
  assert.equal(op.socket.sent[1].cmd, 'start');
  op.receive({...op.state,state:'counting',remaining:143,revision:1});
  assert.equal(op.nodes[launch?'mainClock':'lmClock'].textContent, '02:23');
  op.advance(4000);
  assert.equal(op.nodes[launch?'mainClock':'lmClock'].textContent, '--:--');
  assert.equal(op.nodes[start].disabled, true);
  op.receive({...op.state,state:'hold',remaining:143,revision:2});
  assert.equal(op.nodes[launch?'mainClock':'lmClock'].textContent, '02:23');
}
console.log('Passed shared countdown viewer permissions, authentication, both page renderers, stale state and recovery');
