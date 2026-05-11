window.AppState = {
  farm: null,
  selectedRack: null,
  selectedLevel: null,
  selectedClimateRack: null,
  refreshHandle: null
};

window.$ = function(id){ return document.getElementById(id); };

window.api = async function(path, method='GET', body=null){
  const res = await fetch(path, {
    method,
    headers: body ? {'Content-Type':'application/json'} : {},
    body: body ? JSON.stringify(body) : null
  });
  const data = await res.json().catch(()=>({}));
  if(!res.ok) throw new Error(data.error || 'Request failed');
  return data;
};

window.toast = function(message){
  const el = $('toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(()=>el.classList.remove('show'), 2300);
};

window.formatScanTime = function(ts){
  if(!ts) return 'No scan run yet';
  return `Last scan: ${new Date(ts * 1000).toLocaleTimeString()}`;
};
