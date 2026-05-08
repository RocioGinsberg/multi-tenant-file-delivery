(function (global) {
  function renderProgressTimeline(data, deps) {
    const steps = deps.steps || [];
    const current = deps.currentStatus;
    const order = steps.map((step) => step.key);
    const currentIndex = order.indexOf(current);
    const failed = deps.failed ? deps.failed(current) : false;
    let html = '<div class="timeline">';
    steps.forEach((step, index) => {
      if (index > 0) html += `<div class="tl-line ${index <= currentIndex ? 'done' : ''}"></div>`;
      let cls = '';
      if (failed && index >= currentIndex) cls = 'fail';
      else if (index < currentIndex) cls = 'done';
      else if (index === currentIndex) cls = 'active';
      html += `<div class="tl-step"><div class="tl-dot ${cls}"></div><span class="text-gray-${cls ? '700' : '400'}">${step.label}</span></div>`;
    });
    html += '</div>';
    return html;
  }

  function renderAttemptList(items, deps) {
    const esc = deps.esc;
    const fmtTime = deps.fmtTime;
    const statusLabel = deps.statusLabel;
    const metaLine = deps.metaLine || (() => '');
    const timeLine = deps.timeLine || ((item) => `${fmtTime(item.started_at)} ~ ${fmtTime(item.finished_at)}`);
    const emptyHtml = deps.emptyHtml || '<div class="text-xs text-gray-400">暂无 attempt 记录</div>';
    if (!items.length) return emptyHtml;
    return items.map((item) => `
      <div class="timeline-item">
        <div class="text-sm font-medium">${esc(statusLabel(item.attempt_status) || '未知状态')} · Attempt #${item.attempt_no || 0}</div>
        <div class="${deps.metaClassName || 'timeline-meta'}">${metaLine(item)}</div>
        <div class="${deps.metaClassName || 'timeline-meta'}">${timeLine(item)}</div>
      </div>
    `).join('');
  }

  function renderEventList(items, deps) {
    const esc = deps.esc;
    const fmtTime = deps.fmtTime;
    const eventLabel = deps.eventLabel;
    const transitionLine = deps.transitionLine || ((item) => `${esc(item.from_status || '—')} → ${esc(item.to_status || '—')}`);
    const detailLine = deps.detailLine || ((item) => fmtTime(item.created_at));
    const emptyHtml = deps.emptyHtml || '<div class="text-xs text-gray-400">暂无 event 记录</div>';
    if (!items.length) return emptyHtml;
    return items.map((item) => `
      <div class="timeline-item">
        <div class="text-sm font-medium">${esc(eventLabel(item.event_type) || item.event_type || 'event')}</div>
        <div class="${deps.metaClassName || 'timeline-meta'}">${transitionLine(item)}</div>
        <div class="${deps.metaClassName || 'timeline-meta'}">${detailLine(item)}</div>
      </div>
    `).join('');
  }

  global.PortalTimeline = {
    renderProgressTimeline,
    renderAttemptList,
    renderEventList,
  };
})(window);
