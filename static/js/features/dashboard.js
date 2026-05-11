function drawSpark(canvas, values, status){
  if(!canvas || !values || values.length < 2) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth * dpr;
  const h = canvas.clientHeight * dpr;
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0,0,w,h);
  const min = Math.min(...values), max = Math.max(...values);
  const range = Math.max(0.001, max - min);
  ctx.lineWidth = 2 * dpr;
  ctx.strokeStyle = status === 'critical' ? '#ff5b6e' : status === 'warning' ? '#ffc95f' : '#17f79a';
  ctx.shadowColor = ctx.strokeStyle;
  ctx.shadowBlur = 8 * dpr;
  ctx.beginPath();
  values.forEach((v,i)=>{
    const x = (i/(values.length-1))*w;
    const y = h - ((v-min)/range)*h;
    if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
  });
  ctx.stroke();
}

function levelSeverityClass(level){
  if(level.status === 'imbalanced') return 'critical';
  if(level.status === 'slight_imbalance') return 'warning';
  return 'stable';
}

function localHealthVerdict(score){
  score = Number(score || 0);
  if(score >= 75) return {label:'Good', level:'good', detail:'Overall system is usable, but keep watching forecast drift.'};
  if(score >= 45) return {label:'Bad', level:'bad', detail:'System is drifting. Run AI optimization before the next crop cycle.'};
  return {label:'Critical', level:'critical', detail:'Immediate correction and human inspection are needed.'};
}

function renderDashboard(data){
  const verdict = data.health_verdict || localHealthVerdict(data.health_score);
  $('healthScore').textContent = data.health_score;
  $('healthVerdict').textContent = verdict.label;
  $('healthVerdict').className = `health-verdict ${verdict.level || verdict.label.toLowerCase()}`;
  const hero = document.querySelector('.hero-card');
  hero.classList.remove('good','bad','critical');
  hero.classList.add(verdict.level || verdict.label.toLowerCase());
  $('plantState').textContent = `Overall: ${verdict.label} — ${verdict.detail}`;
  $('systemLock').textContent = data.controls.lockdown ? 'LOCKED' : (data.controls.auto_mode ? 'AUTO' : 'MANUAL');

  const grid = $('metricGrid');
  grid.innerHTML = data.sensors.map(s=>`
    <div class="metric-card ${s.status}">
      <span class="status-dot"></span>
      <div class="name">${s.name}</div>
      <div><span class="value">${s.value}</span> <span class="unit">${s.unit}</span></div>
      <canvas class="spark" data-metric="${s.id}"></canvas>
      <div class="name">Ideal ${s.ideal_min}–${s.ideal_max}</div>
    </div>
  `).join('');
  grid.querySelectorAll('canvas.spark').forEach(c=>{
    const sensor = data.sensors.find(s=>s.id === c.dataset.metric);
    drawSpark(c, sensor.history, sensor.status);
  });

  renderRacks(data.racks);
  renderAiPlan(data.last_ai_plan);
}

function renderRacks(racks){
  const stage = $('rackStage');
  stage.innerHTML = racks.map(r=>`
    <div class="rack-tower" data-rack="${r.id}">
      <div class="rack-head"><strong>${r.name}</strong><span>${r.levels.length} LEVELS</span></div>
      ${r.levels.map(l=>`
        <div class="rack-level ${l.status}" data-rack="${r.id}" data-level="${l.level}">
          <div>
            <div class="level-title">LEVEL ${l.level}</div>
            <strong>${Math.round(l.temperature)}°C / ${Math.round(l.humidity)}%</strong>
          </div>
          <div class="pods">
            <i class="pod sensor ${levelSeverityClass(l)}"></i>
            <i class="pod sensor ${l.airflow < 1.1 ? 'mid' : ''}"></i>
            <i class="pod sensor ${l.light_lux > 580 ? 'mid' : ''}"></i>
          </div>
        </div>
      `).join('')}
    </div>
  `).join('');
  stage.querySelectorAll('.rack-level,.rack-tower').forEach(el=>{
    el.addEventListener('click', (e)=>{
      e.stopPropagation();
      openTwin(el.dataset.rack, Number(el.dataset.level || 4));
    });
  });
}

function renderAiPlan(plan){
  const card = $('aiPlanCard');
  if(!plan){
    card.innerHTML = '<span class="label">AI System Optimizer</span><p>No optimization has been applied yet.</p>';
    return;
  }
  card.innerHTML = `
    <span class="label">${plan.mode === 'gemini' ? 'Gemini System Optimization' : 'Local System Optimization'}</span>
    <p>${plan.summary}</p>
    <p><b>Expected:</b> ${plan.expected_outcome || 'N/A'}</p>
    <div class="ai-actions">
      ${(plan.actions||[]).slice(0,7).map(a=>`<div class="ai-action"><b>${a.rack ? `Rack ${a.rack} · ` : ''}${a.control}</b> ${a.target_percent !== undefined && a.target_percent !== null ? `→ ${a.target_percent}%` : `${Number(a.change_percent||0)>0?'+':''}${a.change_percent||0}%`} — ${a.reason}</div>`).join('')}
    </div>
    <p style="color:#88a79a;margin-top:8px">${plan.operator_note || ''}</p>
  `;
}

async function requestAiPlan(alertId=null){
  try{
    $('runAiBtn').disabled = true;
    $('runAiBtn').textContent = 'Optimizing...';
    const res = await api('/api/ai-plan','POST', alertId ? {alert_id: alertId} : {});
    toast(res.plan.mode === 'gemini' ? 'Gemini optimization applied' : 'Local optimization applied');
    await refresh();
  }catch(err){toast(err.message)}
  finally{
    $('runAiBtn').disabled = false;
    $('runAiBtn').textContent = 'AI Optimize System';
  }
}
window.requestAiPlan = requestAiPlan;
