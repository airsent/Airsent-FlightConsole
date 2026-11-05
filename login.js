/* Shared login controller, including compatibility with older cached login HTML. */
(() => {
  if (window.airsentLoginController) return;
  window.airsentLoginController = true;
  const el = id => document.getElementById(id);
  const roles = ['operator', 'viewer', 'simulation'];
  function authenticate(username, password) {
    username = username.trim().toLowerCase(); password = password.trim();
    if (username === 'demo' && password === '123456') return Promise.resolve({role:'demo',token:null});
    return new Promise((resolve, reject) => {
      const url = new URL('/presence', location.origin); url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(url.href);
      let complete = false;
      const finish = (error, result) => {
        if (complete) return; complete = true; clearTimeout(timer); ws.close();
        if (error) reject(new Error(error)); else resolve(result);
      };
      const timer = setTimeout(() => finish('Sign-in service unavailable. Please try again.'), 10000);
      ws.onopen = () => ws.send(JSON.stringify({type:'presence_login',username,password}));
      ws.onmessage = event => {
        let data; try {data=JSON.parse(event.data);} catch (_) {return;}
        if (data.type !== 'presence_login') return;
        if (!data.token || !roles.includes(data.role)) finish('INVALID USERNAME OR PASSWORD');
        else finish(null,data);
      };
      ws.onerror = () => finish('Cannot reach the sign-in service. Please try again.');
      ws.onclose = () => { if (!complete) finish('Sign-in connection closed. Please try again.'); };
    });
  }
  window.AirsentLogin = {authenticate};
  let busy = false;
  async function submit() {
    if (busy) return;
    busy=true; const button=el('loginBtn'), error=el('errorMsg'); button.disabled=true;
    error?.classList.remove('visible');
    try {
      const result=await authenticate(el('username').value,el('password').value);
      localStorage.removeItem('airsentSITL');
      localStorage.setItem('airsentMode',result.role);
      if (result.token) localStorage.setItem('airsentPresenceToken',result.token);
      else localStorage.removeItem('airsentPresenceToken');
      if (result.role==='operator') sessionStorage.setItem('airsentSequencePassword',el('password').value.trim());
      else sessionStorage.removeItem('airsentSequencePassword');
      el('bootOverlay')?.classList.remove('hidden');
      if(el('bootMessage')) el('bootMessage').textContent=`Opening ${result.role} console…`;
      if(el('bootProgressFill')) el('bootProgressFill').style.width='100%';
      setTimeout(()=>location.replace(result.role==='simulation'?'dashboard.html?simulation=1':'dashboard.html?login=current'),400);
    } catch (err) {
      if(error) {error.textContent=err.message;error.classList.add('visible');}
      busy=false; button.disabled=false;
    }
  }
  // Capture before legacy inline click/key handlers so cached forms use this controller.
  document.addEventListener('click',event=>{
    if (event.target.closest?.('#loginBtn')) {event.preventDefault();event.stopImmediatePropagation();submit();}
  },true);
  document.addEventListener('keydown',event=>{
    if (event.key==='Enter' && event.target.id==='password') {event.preventDefault();event.stopImmediatePropagation();submit();}
  },true);
})();
