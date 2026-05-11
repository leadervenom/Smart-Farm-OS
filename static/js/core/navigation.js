window.nav = function(screenId){
  document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));
  $(screenId).classList.add('active');
  document.querySelectorAll('.bottom-nav button').forEach(b=>b.classList.toggle('active', b.dataset.nav === screenId));
};

document.querySelectorAll('[data-nav]').forEach(btn=>btn.addEventListener('click', ()=>nav(btn.dataset.nav)));
