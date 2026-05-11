function renderAlerts(alerts, lastScan){
  $('scanStamp').textContent = formatScanTime(lastScan);
  const list = $('alertList');
  if(!lastScan){
    list.innerHTML = '<div class="info-card"><h3>Prediction scan not started</h3><p>Press Run Prediction Scan. The app will calculate one forecast pass and stop until you run it again.</p></div>';
    return;
  }
  if(!alerts.length){
    list.innerHTML = '<div class="info-card"><h3>No predictive alerts</h3><p>No active forecast risks are stored from the latest scan.</p></div>';
    return;
  }
  list.innerHTML = alerts.map(a=>`
    <div class="alert-card ${a.severity}">
      <div class="alert-top">
        <strong>${a.title}</strong>
        <span class="severity">${a.severity}</span>
      </div>
      <p>${a.message}</p>
      <div class="alert-extra">
        ${a.problem ? `<p><b>Predicted problem:</b> ${a.problem}</p>` : ''}
        ${a.forecast ? `<p><b>Forecast:</b> ${a.forecast}</p>` : ''}
        <p><b>Recommendation:</b> ${a.recommendation}</p>
        <p><b>ETA:</b> ${a.hours_until === 0 ? 'next cycle' : `${a.hours_until}h`} · <b>Metric:</b> ${a.metric}</p>
      </div>
      <div class="button-row">
        ${a.automatable ? `<button class="small-btn ai" onclick="requestAiPlan('${a.id}')">AI Optimize</button>` : ''}
        ${(a.actions||[]).map(act=>`<button class="small-btn" onclick="applyAction('${act.id}','${a.id}')">${act.label}</button>`).join('')}
        <button class="small-btn mail" onclick="notifyTeam('${a.id}')">Notify On-site Team</button>
        <button class="small-btn warn" onclick="dismissAlert('${a.id}')">Dismiss</button>
      </div>
    </div>
  `).join('');
}

async function runPredictionScan(){
  try{
    $('runPredictionBtn').disabled = true;
    $('runPredictionBtn').textContent = 'Scanning...';
    const res = await api('/api/predictive-scan','POST',{});
    if(AppState.farm){
      AppState.farm.alerts = res.alerts;
      AppState.farm.last_prediction_scan = res.last_prediction_scan;
    }
    renderAlerts(res.alerts, res.last_prediction_scan);
    toast('One prediction scan completed');
    await refresh();
  }catch(err){toast(err.message)}
  finally{
    $('runPredictionBtn').disabled = false;
    $('runPredictionBtn').textContent = 'Run Prediction Scan';
  }
}
window.runPredictionScan = runPredictionScan;

async function applyAction(action_id, alert_id=null){
  try{
    await api('/api/apply-action','POST',{action_id, alert_id});
    toast(`Action applied: ${action_id.replaceAll('_',' ')}`);
    await refresh();
  }catch(err){toast(err.message)}
}
window.applyAction = applyAction;

async function notifyTeam(alert_id){
  try{
    const recipient = prompt('Email to notify on-site team:', 'onsite-team@farm.local') || 'onsite-team@farm.local';
    const res = await api('/api/notify-alert','POST',{alert_id, recipient});
    if(res.mailto_url){ window.location.href = res.mailto_url; }
    toast('Email notification prepared');
    await refresh();
  }catch(err){toast(err.message)}
}
window.notifyTeam = notifyTeam;

async function dismissAlert(alert_id){
  try{
    await api('/api/dismiss-alert','POST',{alert_id});
    toast('Alert dismissed');
    await refresh();
  }catch(err){toast(err.message)}
}
window.dismissAlert = dismissAlert;
