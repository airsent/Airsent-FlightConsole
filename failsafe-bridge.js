/* Optional review-only bridge. Copies selected telemetry fields, never commands.
   Channel data is transient: no vehicle data is written to persistent storage. */
(()=>{
  'use strict';
  const keys=['voltage','current','battery','sats','hdop','ekf_healthy','pixhawk_connected','fix_type','cam_hot','jetson_temp','front_cam_active','down_cam_active','vib_x','vib_y','vib_z'];
  const channel=typeof BroadcastChannel==='function'?new BroadcastChannel('airsent-failsafe-trial-v1'):null;
  let latest=null;
  function publish(data,source){
    if(!data||typeof data!=='object'||!['demo','telemetry'].includes(source))return;
    const values={};for(const key of keys){const value=data[key];if(typeof value==='boolean'||typeof value==='string'||(typeof value==='number'&&Number.isFinite(value)))values[key]=value;}
    latest={type:'review-frame',version:1,source,receivedAt:Date.now(),values};
    channel?.postMessage(latest);
  }
  if(channel)channel.onmessage=event=>{
    const message=event.data;
    if(message?.type==='request-review-frame'){if(latest)channel.postMessage(latest);return;}
    if(message?.type==='review-frame'&&message.version===1&&['demo','telemetry'].includes(message.source)&&Number.isFinite(message.receivedAt)&&message.values&&typeof message.values==='object'){
      window.dispatchEvent(new CustomEvent('airsent-review-frame',{detail:message}));
    }
  };
  window.AirsentReviewBridge={publish,request:()=>channel?.postMessage({type:'request-review-frame'})};
  addEventListener('pagehide',()=>channel?.close(),{once:true});
})();
