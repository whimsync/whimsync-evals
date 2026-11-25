"""Static CSS and JS embedded in every report page."""

CSS = """
:root { --bg:#fbfbfa; --fg:#1d1d1f; --muted:#6b6b70; --card:#fff; --line:#e4e4e0;
        --add:#d9f2dd; --del:#fadcdc; --accent:#2f5fd0; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16171a; --fg:#e8e8ea; --muted:#9a9aa2; --card:#1f2024; --line:#2e3036;
          --add:#1f3d27; --del:#4a2326; --accent:#7ea2ff; }
}
* { box-sizing:border-box; }
body { margin:0; font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; background:var(--bg); color:var(--fg); }
main { max-width:1400px; margin:0 auto; padding:28px 32px 80px; }
h1 { font-size:22px; margin:0 0 4px; }
h2 { font-size:17px; margin:36px 0 10px; }
h3 { font-size:14px; margin:18px 0 8px; }
.meta { color:var(--muted); font-size:12.5px; }
.meta code { font-size:12px; }
table { border-collapse:collapse; font-variant-numeric:tabular-nums; }
th, td { padding:5px 9px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }
th { font-weight:600; color:var(--muted); font-size:12px; }
th:first-child, td:first-child { text-align:left; }
td.score { border-radius:3px; }
.delta-pos { color:#1c8a3c; } .delta-neg { color:#c23b3b; }
.scroll { overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:8px; padding:6px 10px; }
.filters { display:flex; gap:10px; align-items:center; margin:8px 0 14px; flex-wrap:wrap; }
.filters select, .filters input { font:inherit; padding:4px 8px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--fg); }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:hidden; cursor:pointer; }
.card:hover { border-color:var(--accent); }
.card img { width:100%; height:300px; object-fit:cover; object-position:top; display:block; border-bottom:1px solid var(--line); background:#fff; }
.card .body { padding:8px 10px; }
.card .title { font-weight:600; font-size:13px; word-break:break-all; }
.chips { display:flex; gap:5px; flex-wrap:wrap; margin-top:6px; }
.chip { font-size:11.5px; padding:1px 7px; border-radius:10px; font-variant-numeric:tabular-nums; }
.aug { color:var(--muted); font-size:11.5px; margin-top:4px; }
dialog { width:min(1350px,96vw); max-height:92vh; border:1px solid var(--line); border-radius:10px; background:var(--bg); color:var(--fg); padding:0; }
dialog::backdrop { background:rgba(0,0,0,.45); }
.dlg-head { position:sticky; top:0; background:var(--bg); padding:14px 18px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; gap:12px; z-index:1; }
.dlg-body { padding:14px 18px 24px; }
.cols { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
pre { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:10px; white-space:pre-wrap; word-break:break-word; font-size:12px; max-height:520px; overflow:auto; margin:0; }
.diff span.add { background:var(--add); } .diff span.del { background:var(--del); text-decoration:line-through; }
button { font:inherit; border:1px solid var(--line); background:var(--card); color:var(--fg); border-radius:6px; padding:4px 12px; cursor:pointer; }
.tabs { display:flex; gap:6px; margin:10px 0; } .tabs button.on { border-color:var(--accent); color:var(--accent); }
svg text { fill:var(--muted); font-size:11px; }
.legend { display:flex; gap:14px; font-size:12px; color:var(--muted); margin:4px 0; }
.legend i { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; vertical-align:-1px; }
.err { color:#c23b3b; }
.claims td, .claims th { white-space:normal; text-align:left; vertical-align:top; }
"""

JS = """
function showPane(btn, id) {
  const dlg = btn.closest('dialog');
  dlg.querySelectorAll('.pane').forEach(p => p.style.display = 'none');
  dlg.querySelectorAll('.tabs button').forEach(b => b.classList.remove('on'));
  document.getElementById(id).style.display = '';
  btn.classList.add('on');
}
function filterCards() {
  const mode = document.getElementById('f-mode').value, kase = document.getElementById('f-case').value;
  document.querySelectorAll('#cards .card').forEach(c => {
    c.style.display = (!mode || c.dataset.mode === mode) && (!kase || c.dataset.case === kase) ? '' : 'none';
  });
}
function sortCards() {
  const grid = document.getElementById('cards'), how = document.getElementById('f-sort').value;
  const cards = [...grid.children];
  cards.sort((a, b) => how === 'worst'
    ? parseFloat(a.dataset.score) - parseFloat(b.dataset.score)
    : a.querySelector('.title').textContent.localeCompare(b.querySelector('.title').textContent));
  cards.forEach(c => grid.appendChild(c));
}
"""
