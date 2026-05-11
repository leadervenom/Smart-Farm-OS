const CONTROL_LABELS = {
  ventilation:'Ventilation',
  lighting:'Lighting',
  nutrient_pump:'Nutrient Pump',
  circulation:'Circulation',
  humidifier:'Humidifier'
};

function controlClass(value){
  value = Number(value);
  if(value >= 78) return 'high';
  if(value <= 25) return 'low';
  return 'mid';
}

function renderClimateTwin(racks){
  if(!racks || !racks.length) return;
  if(!AppState.selectedClimateRack || !racks.find(r=>r.id === AppState.selectedClimateRack.id)){
    AppState.selectedClimateRack = racks[0];
  }else{
    AppState.selectedClimateRack = racks.find(r=>r.id === AppState.selectedClimateRack.id);
  }

  $('climateTwinGrid').innerHTML = racks.map(r=>renderClimateRackCard(r)).join('');
  document.querySelectorAll('.climate-rack-card').forEach(card=>{
    card.addEventListener('click', (e)=>{
      if(e.target.closest('button')) return;
      AppState.selectedClimateRack = racks.find(r=>r.id === card.dataset.rack);
      renderClimateTwin(racks);
    });
  });
  renderRackControlPanel(AppState.selectedClimateRack);
}

function renderClimateRackCard(r){
  const plan = r.climate_plan || {};
  const selected = AppState.selectedClimateRack && AppState.selectedClimateRack.id === r.id ? 'selected' : '';
  const controls = r.controls || {};
  const levels = [...r.levels].sort((a,b)=>b.level-a.level);
  const mainActions = (plan.actions || []).slice(0,2);
  return `
    <div class="climate-rack-card ${selected}" data-rack="${r.id}">
      <div class="climate-rack-top">
        <div><span class="label">Rack Climate Twin</span><h3>${r.name}</h3></div>
        <span class="climate-score ${plan.priority || 'low'}">${plan.score || '--'}</span>
      </div>
      <div class="mini-reactor-rack">
        ${levels.map(l=>`
          <div class="mini-rack-level ${l.status}" title="Rack ${l.rack} Level ${l.level}">
            <span>L${l.level}</span>
            <b>${Math.round(l.temperature)}°C</b>
            <em>${Math.round(l.humidity)}%</em>
          </div>
        `).join('')}
      </div>
      <div class="actuator-grid">
        ${Object.entries(CONTROL_LABELS).map(([key,label])=>`
          <span class="actuator-chip ${controlClass(controls[key])}">${label.split(' ')[0]} <b>${controls[key]}%</b></span>
        `).join('')}
      </div>
      <p class="climate-summary">${plan.summary || 'Waiting for rack plan...'}</p>
      <div class="optimizer-preview">
        ${mainActions.map(a=>`<div><b>${a.control}</b> ${a.target_percent !== null && a.target_percent !== undefined ? `→ ${a.target_percent}%` : ''}<span>${a.reason}</span></div>`).join('') || '<div><b>Maintain</b><span>No active correction needed.</span></div>'}
      </div>
      <button class="small-btn" onclick="optimizeRack('${r.id}')">Optimize ${r.name}</button>
    </div>
  `;
}

function renderRackControlPanel(rack){
  if(!rack){
    $('rackControlPanel').innerHTML = '<h3>Select a rack</h3>';
    return;
  }
  const plan = rack.climate_plan || {};
  const controls = rack.controls || {};
  $('rackControlPanel').innerHTML = `
    <h3>${rack.name} Multivariable Override</h3>
    <p>These sliders are rack-specific. Change one rack and the digital twin above changes on the next telemetry tick.</p>
    <div class="control-link-strip">
      ${(plan.imbalances || []).map(i=>`<span>${i.metric}: ${i.value} (${i.direction})</span>`).join('') || '<span>Variables inside target envelope</span>'}
    </div>
    <div id="rackSliders">
      ${Object.entries(CONTROL_LABELS).map(([key,label])=>`
        <div class="slider-row rack-slider-row">
          <div class="slider-meta"><span>${label}</span><span id="rack_${rack.id}_${key}_val">${controls[key]}%</span></div>
          <input type="range" min="0" max="100" value="${controls[key]}" data-rack="${rack.id}" data-control="${key}" />
        </div>
      `).join('')}
    </div>
    <div class="info-card inner-card">
      <h3>Conflict Awareness</h3>
      ${(plan.conflicts || []).map(c=>`<p>${c}</p>`).join('') || '<p>No major control conflict detected for this rack.</p>'}
    </div>
  `;
  $('rackSliders').querySelectorAll('input[type=range]').forEach(input=>{
    input.addEventListener('input', ()=>{
      $(`rack_${input.dataset.rack}_${input.dataset.control}_val`).textContent = `${input.value}%`;
    });
    input.addEventListener('change', async()=>{
      await rackManualOverride(input.dataset.rack, input.dataset.control, Number(input.value));
    });
  });
}

async function rackManualOverride(rack, target, value){
  try{
    await api('/api/rack-control','POST',{rack,target,value});
    toast(`Rack ${rack} ${target.replace('_',' ')} set to ${value}%`);
    await refresh();
  }catch(err){toast(err.message)}
}
window.rackManualOverride = rackManualOverride;

async function optimizeRack(rack){
  try{
    const btn = document.querySelector(`button[onclick="optimizeRack('${rack}')"]`);
    if(btn){ btn.disabled = true; btn.textContent = 'Optimizing...'; }
    const res = await api('/api/optimize-rack','POST',{rack, apply:true});
    toast(`${res.plan.mode === 'gemini' ? 'Gemini' : 'Local'} optimized Rack ${rack}`);
    await refresh();
  }catch(err){toast(err.message)}
}
window.optimizeRack = optimizeRack;
