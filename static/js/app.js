async function refresh(){
  try{
    const data = await api('/api/state');
    AppState.farm = data;
    renderDashboard(data);
    renderAlerts(data.alerts, data.last_prediction_scan);
    renderClimateTwin(data.racks);
    renderEvents(data.event_log);
    if($('twinScreen').classList.contains('active') && AppState.selectedRack){
      AppState.selectedRack = data.racks.find(r=>r.id === AppState.selectedRack.id) || data.racks[0];
      AppState.selectedLevel = AppState.selectedRack.levels.find(l=>l.level === AppState.selectedLevel.level) || AppState.selectedRack.levels[0];
      renderTwinHotspots();
      renderSlotPanel(AppState.selectedLevel);
    }
  }catch(err){toast(err.message)}
}
window.refresh = refresh;

document.addEventListener('DOMContentLoaded', ()=>{
  $('runAiBtn').addEventListener('click', requestAiPlan);
  $('runPredictionBtn').addEventListener('click', runPredictionScan);
  refresh();
  // Dashboard telemetry keeps moving; predictive alerts do not recalculate until Run Prediction Scan is pressed.
  AppState.refreshHandle = setInterval(refresh, 2600);
});
