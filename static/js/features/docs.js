function renderEvents(events){
  if(!events.length){
    $('eventLog').innerHTML = '<div class="event"><strong>No documentation logs yet.</strong><p>Prediction scans, rack overrides, AI plans, and actions will appear here.</p></div>';
    return;
  }
  $('eventLog').innerHTML = events.slice().reverse().map(e=>`
    <div class="event">
      <strong>${e.type.replaceAll('_',' ')}</strong>
      <p><code>${new Date(e.ts*1000).toLocaleTimeString()}</code> ${JSON.stringify(e.detail).slice(0,130)}</p>
    </div>
  `).join('');
}
