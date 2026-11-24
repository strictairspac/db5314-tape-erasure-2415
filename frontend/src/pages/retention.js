import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {date, esc} from '../utils/dom.js';
import {requestKey} from '../utils/requestKey.js';

export function retentionPage(state, identity) {
  const view = state.retention;
  if (!view) return heading('批次留存复核', '正在载入批次留存明细…');
  const run = view.run;
  const assetRows = view.assets.map(row => [
    esc(row.asset_id), esc(row.kind), esc(row.object_key), esc(row.reason),
    esc(row.contracts.join('、') || '—'),
  ]);
  const recomputeRows = state.recomputations
    .filter(row => row.source_run_id === run.id)
    .map(row => [
      `<a href="#runs/${row.result_run_id}">重算 ${row.result_run_id}</a>`,
      esc(row.cutoff), esc(date(row.created_at)),
      `<a href="#retention/${row.result_run_id}">按补录后合同复核</a>`,
    ]);
  const evidenceLink = view.evidence_id
    ? `<a class="secondary" href="#evidence/${view.evidence_id}">查看原证据</a>`
    : '<span class="note">原批次尚未产生证据</span>';
  const recomputeButton = state.session.role==='operator'
    ? `<button id="recompute" data-id="${run.id}">按补录后合同重算同一检查日</button>` : '';
  const tools = `<span class="actions">${recomputeButton}${evidenceLink}</span>`;
  return `${heading(`批次 ${run.id} · 留存复核`, `按执行当时保存的目录，查看检查日 ${esc(view.cutoff)} 时保住的素材。原证据保持可查，重算沿用当时的目录，不覆盖原证据。`, tools)}
    <dl class="metrics"><div><dt>留存资产</dt><dd>${esc(view.assets.length)}</dd></div><div><dt>生效合同</dt><dd>${esc(view.contracts.length)}</dd></div><div><dt>检查日</dt><dd>${esc(view.cutoff)}</dd></div></dl>
    ${panel('当时保住的素材（按保存的目录快照）', records(['资产', '类型', '素材路径', '留存原因', '保护合同'], assetRows, '该批次没有留存资产。'))}
    ${recomputeRows.length ? panel('按补录后合同重算', records(['重算批次', '检查日', '记录时间', '查看'], recomputeRows)) : ''}`;
}

export function bindRetention(api, update, fail) {
  const button = document.getElementById('recompute');
  if (!button) return;
  const runId = button.dataset.id;
  const key = `recompute-${runId}`;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      // Reuse the same request key across retries so a dropped connection cannot
      // create a second recomputation; the server returns the first one.
      const request_key = sessionStorage.getItem(key) || requestKey();
      sessionStorage.setItem(key, request_key);
      const record = await api(`/runs/${runId}/recompute`, {method:'POST', data:{request_key}});
      sessionStorage.removeItem(key);
      // Land on the recomputed batch so its retained set can be compared with the original.
      location.hash = `retention/${record.result_run_id}`;
    } catch (error) {
      button.disabled = false;
      fail(error);
    }
  });
}
