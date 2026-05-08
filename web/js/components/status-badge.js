(function (global) {
  function chip(status, deps) {
    if (!status) return deps.emptyHtml || '<span class="text-gray-400">—</span>';
    const esc = deps.esc;
    const label = deps.label ? deps.label(status) : status;
    const style = deps.style ? deps.style(status) : '';
    const className = deps.className || 'status-chip';
    return `<span class="${className}" style="${style}">${esc(label || '—')}</span>`;
  }

  function executeLabel(status) {
    return {
      pending_execution: '待执行',
      executing: '执行中',
      executed: '已执行',
      execution_failed: '执行失败',
    }[status] || '—';
  }

  function executeBadge(status) {
    if (!status) return '<span class="text-xs text-gray-300">—</span>';
    const style = {
      pending_execution: 'background:#fff7ed;color:#c2410c;border:1px solid #fed7aa',
      executing: 'background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe',
      executed: 'background:#ecfdf5;color:#047857;border:1px solid #a7f3d0',
      execution_failed: 'background:#fef2f2;color:#b91c1c;border:1px solid #fecaca',
    }[status] || '';
    return `<span class="text-xs font-semibold px-2 py-1 rounded-full" style="${style}">${executeLabel(status)}</span>`;
  }

  function approvalBadge(status) {
    const labels = {pending: '待审批', approved: '已通过', rejected: '已拒绝'};
    return `<span class="badge b-${status}">${labels[status] || status || '—'}</span>`;
  }

  function recommendationBadge(rec) {
    if (!rec) return '<span class="text-xs text-gray-300">—</span>';
    const map = {
      approve: ['rec-approve', '建议通过'],
      reject: ['rec-reject', '建议拒绝'],
      review: ['rec-review', '需人工研判'],
    };
    const [cls, label] = map[rec] || ['', rec];
    return `<span class="badge ${cls}">${label}</span>`;
  }

  function confidenceBar(val) {
    if (val == null) return '<span class="text-xs text-gray-300">—</span>';
    const color = val >= 80 ? '#28cd41' : val >= 60 ? '#ff9f0a' : '#ff3b30';
    return `<span class="conf-track"><span class="conf-fill" style="width:${val}%;background:${color}"></span></span><span class="text-xs font-mono text-gray-500">${val}%</span>`;
  }

  global.PortalStatusBadge = {
    chip,
    executeLabel,
    executeBadge,
    approvalBadge,
    recommendationBadge,
    confidenceBar,
  };
})(window);
