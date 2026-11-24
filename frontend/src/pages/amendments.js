import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {state as store} from '../stores/session.js';
import {date, esc} from '../utils/dom.js';
import {requestKey} from '../utils/requestKey.js';
import {submit} from '../hooks/submit.js';

const statusLabels = {accepted: '已入账', conflict: '退回核对'};

export function amendmentsPage(state) {
  const operator = state.session.role === 'operator';
  const rows = state.amendments.map(row => [
    esc(row.contract_id), esc(row.asset_id), esc(row.effective_from) + ' ~ ' + esc(row.effective_to || '长期'),
    esc(row.hold_until ?? '无固定截止'),
    `<span class="status" data-state="${esc(row.status==='accepted'?'complete':'failed')}">${esc(statusLabels[row.status] || row.status)}</span>`,
    row.conflict_with ? `与入账 ${esc(row.conflict_with)} 重号，已退回核对` : '—',
    esc(date(row.created_at)),
  ]);
  const form = operator ? `<form id="amendment-form">
      <div class="field-grid">
        <label class="field"><span>合同编号</span><input name="contract_id" required maxlength="64" /></label>
        <label class="field"><span>资产编号</span><input name="asset_id" required maxlength="64" /></label>
        <label class="field"><span>保护截止日（含当日，可空）</span><input name="hold_until" type="date" /></label>
        <label class="field"><span>生效起始日</span><input name="effective_from" type="date" required /></label>
        <label class="field"><span>生效截止日（可空表示长期）</span><input name="effective_to" type="date" /></label>
        <label class="field"><span>更正说明</span><input name="note" maxlength="500" /></label>
      </div>
      <div class="actions form-actions"><button>提交更正</button></div>
    </form>` : '<p class="note">仅经办员可以补录合同更正。</p>';
  return `${heading('合同更正', '补录只影响指定日期段。同一合同的相交日期先入账的一份生效，另一份退回核对；不相交的可以分别入账。', '')}
    <div class="spread"><section>${panel('已记录的更正', records(['合同', '资产', '生效区间', '保护至', '状态', '备注', '记录时间'], rows, '尚未记录合同更正。'))}</section>
    <aside class="brief"><h2>补录更正</h2>${form}<p class="note">更正入账后重试同一请求不会重复入账，仍显示第一次的入账时间。</p></aside></div>`;
}

export function bindAmendments(api, update, fail) {
  submit('amendment-form', async data => {
    const body = Object.fromEntries(data);
    for (const key of ['hold_until', 'effective_to', 'note']) if (!body[key]) body[key] = null;
    // One key per distinct submission: a retry after a dropped connection reuses
    // it so the correction is not recorded twice; editing fields derives a new key.
    const signature = JSON.stringify(body);
    if (store.amendmentSignature !== signature) {
      store.amendmentSignature = signature;
      store.amendmentKey = requestKey();
    }
    body.request_key = store.amendmentKey;
    await api('/amendments', {method:'POST', data:body});
    store.amendmentSignature = null;
    store.amendmentKey = null;
    await update();
  }, fail);
}
