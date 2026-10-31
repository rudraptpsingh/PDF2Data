const samples = {
  metadata: { label: 'Metadata · Page 1', data: { type: 'metadata', page: 1, reference: 'TN-014', date: '2026-05-12', status: 'candidate' }, preview: '<h4>Document metadata</h4><dl><dt>REFERENCE</dt><dd>TN-014</dd><dt>DATE</dt><dd>12 May 2026</dd><dt>SOURCE</dt><dd>Page 1 · Metadata region</dd></dl>' },
  table: { label: 'Table · Page 1', data: { type: 'table', page: 1, headers: ['Charging source', 'MWh'], rows: [['Renewable input', '510'], ['Total input', '1,000']], status: 'candidate' }, preview: '<h4>Charging energy</h4><table><thead><tr><th>Charging source</th><th>MWh</th></tr></thead><tbody><tr><td>Renewable input</td><td>510</td></tr><tr><td>Total input</td><td>1,000</td></tr></tbody></table>' },
  footer: { label: 'Footer · Page 1', data: { type: 'footer', page: 1, text: 'Technical note', page_label: 'Page 1', status: 'candidate' }, preview: '<h4>Page footer</h4><dl><dt>TEXT</dt><dd>Technical note</dd><dt>PAGE LABEL</dt><dd>Page 1</dd><dt>SOURCE</dt><dd>Bottom region · Page 1</dd></dl>' }
};
let region = 'table', format = 'preview';
function renderSample() {
  const sample = samples[region], target = document.getElementById('parsed-output');
  document.getElementById('active-label').textContent = sample.label;
  document.querySelector('.parsed-panel').dataset.active = region;
  document.querySelectorAll('[data-region]').forEach(button => button.classList.toggle('selected', button.dataset.region === region));
  if (format === 'json') { target.replaceChildren(); const pre = document.createElement('pre'); pre.textContent = JSON.stringify(sample.data, null, 2); target.append(pre); }
  else target.innerHTML = sample.preview; // Static authored demo markup, never uploaded document content.
  document.querySelectorAll('[data-format]').forEach(button => { const selected = button.dataset.format === format; button.classList.toggle('active', selected); button.setAttribute('aria-selected', String(selected)); });
  target.setAttribute('aria-labelledby', format === 'json' ? 'json-tab' : 'preview-tab');
}
document.querySelectorAll('[data-region]').forEach(button => {
  const select = () => { region = button.dataset.region; renderSample(); };
  button.addEventListener('mouseenter', select); button.addEventListener('focus', select); button.addEventListener('click', select);
});
document.querySelectorAll('[data-format]').forEach(button => button.addEventListener('click', () => { format = button.dataset.format; renderSample(); }));
document.querySelectorAll('[data-copy],[data-copy-target]').forEach(button => button.addEventListener('click', async () => {
  const text = button.dataset.copy || document.getElementById(button.dataset.copyTarget).textContent;
  try {
    await navigator.clipboard.writeText(text); const previous = button.textContent; button.textContent = 'Copied';
    document.getElementById('copy-status').textContent = 'Copied to clipboard';
    setTimeout(() => { button.textContent = previous; }, 1800);
  } catch { document.getElementById('copy-status').textContent = 'Select and copy the command text.'; }
}));
renderSample();
