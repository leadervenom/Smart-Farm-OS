function openTwin(rackId, levelNum){
  if(!AppState.farm) return;
  AppState.selectedRack = AppState.farm.racks.find(r=>r.id === rackId) || AppState.farm.racks[0];
  AppState.selectedLevel = AppState.selectedRack.levels.find(l=>l.level === levelNum) || AppState.selectedRack.levels[0];
  $('twinTitle').textContent = `${AppState.selectedRack.name} — Expanded View`;
  renderTwinHotspots();
  renderSlotPanel(AppState.selectedLevel);
  nav('twinScreen');
}
window.openTwin = openTwin;

function renderTwinHotspots(){
  const wrap = $('rackHotspots');
  const levels = [...AppState.selectedRack.levels].sort((a,b)=>b.level-a.level);
  wrap.innerHTML = levels.map(l=>`<button class="hotspot ${l.status}" data-level="${l.level}"><span class="h-level">L${l.level}</span><span class="h-state">${l.status.replace('_',' ')}</span></button>`).join('');
  wrap.querySelectorAll('.hotspot').forEach(btn=>btn.addEventListener('click', ()=>{
    const level = AppState.selectedRack.levels.find(l=>l.level === Number(btn.dataset.level));
    AppState.selectedLevel = level;
    renderSlotPanel(level);
  }));
}

function renderSlotPanel(l){
  $('slotPanel').innerHTML = `
    <strong>Rack ${l.rack} Level ${l.level}</strong>
    <p style="margin-top:6px;color:#88a79a">${l.status.replace('_',' ')} · this panel now tracks environment variables only, not plant database records.</p>
    <div class="slot-kpi-grid">
      <div class="slot-kpi"><strong>${l.temperature}°C</strong><span>Temperature</span></div>
      <div class="slot-kpi"><strong>${l.humidity}%</strong><span>Humidity</span></div>
      <div class="slot-kpi"><strong>${l.ph}</strong><span>Water pH</span></div>
      <div class="slot-kpi"><strong>${l.ec}</strong><span>EC / nutrients</span></div>
      <div class="slot-kpi"><strong>${l.airflow}</strong><span>Airflow m/s</span></div>
      <div class="slot-kpi"><strong>${Math.round(l.light_lux)}</strong><span>Light lux</span></div>
    </div>
    <div class="button-row">
      <button class="small-btn" onclick="nav('microScreen')">Open Climate Controls</button>
      <button class="small-btn warn" onclick="requestAiPlan()">AI Optimize</button>
    </div>
  `;
}
