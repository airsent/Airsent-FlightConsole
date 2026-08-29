(function(){
  const mode = (localStorage.getItem("airsentMode") || "demo").toLowerCase();
  const role =
    (mode === "operator" || mode === "actual" || mode === "real") ? "operator" :
    (mode === "viewer") ? "viewer" :
    "demo";

  const totalEl = document.getElementById("consoleUsersTotal");
  const opEl = document.getElementById("consoleOperators");
  const viewEl = document.getElementById("consoleViewers");

  function setCounts(data){
    if (!data) data = { total:0, operators:0, viewers:0 };
    if (totalEl) totalEl.textContent = Number(data.total || 0);
    if (opEl) opEl.textContent = Number(data.operators || 0);
    if (viewEl) viewEl.textContent = Number(data.viewers || 0);
  }

  setCounts();
  if (role !== "operator" && role !== "viewer") return;

  let ws = null;
  let heartbeat = null;
  let reconnect = null;

  function presenceUrl(){
    const local = location.hostname === "127.0.0.1" || location.hostname === "localhost";
    if (local) return "wss://console.airsent.tech/presence";
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}/presence`;
  }

  function sendPresence(){
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify({
        type: "presence",
        role,
        page: location.pathname.split("/").pop() || "dashboard.html"
      }));
    } catch(e) {}
  }

  function scheduleReconnect(){
    if (heartbeat) {
      clearInterval(heartbeat);
      heartbeat = null;
    }
    if (reconnect) clearTimeout(reconnect);
    reconnect = setTimeout(connectPresence, 3000);
  }

  function connectPresence(){
    try {
      ws = new WebSocket(presenceUrl());
    } catch(e) {
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      sendPresence();
      heartbeat = setInterval(sendPresence, 5000);
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg && msg.type === "presence_counts") setCounts(msg);
      } catch(e) {}
    };

    ws.onclose = scheduleReconnect;
    ws.onerror = () => {};
  }

  connectPresence();
})();
