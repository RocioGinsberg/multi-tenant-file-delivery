(function (global) {
  function executeStatusLabel(status) {
    return global.PortalStatusBadge.executeLabel(status);
  }

  function executeBadge(status) {
    return global.PortalStatusBadge.executeBadge(status);
  }

  function eventLabel(eventType) {
    const labels = {
      task_queued: '任务已排队',
      task_requeued: '任务已重排',
      attempt_started: '尝试开始',
      attempt_heartbeat: '执行心跳',
      execute_started: '开始执行',
      execute_completed: '执行完成',
      execute_failed: '执行失败',
      attempt_finished: '尝试结束',
      manual_reconcile_checked: '手动对账',
      auto_reconcile_checked: '自动巡检',
      stale_heartbeat_detected: '检测到心跳超时',
    };
    return labels[eventType] || eventType || '事件';
  }

  function renderExecutionEvents(events, deps) {
    const esc = deps.esc;
    const fmtTime = deps.fmtTime;
    if (!Array.isArray(events) || !events.length) {
      return '<div class="text-xs text-gray-400">暂无执行事件</div>';
    }
    return events.slice(0, 6).map(e => `
      <div class="exec-event">
        <div class="flex items-center justify-between gap-3">
          <span class="text-xs font-semibold text-gray-700">${esc(eventLabel(e.event_type || ''))}</span>
          <span class="text-xs text-gray-400">${fmtTime(e.created_at)}</span>
        </div>
        ${(e.payload_json && (e.payload_json.execute_error || e.payload_json.error_message))
          ? `<div class="text-xs text-red-500 mt-1">${esc(e.payload_json.execute_error || e.payload_json.error_message)}</div>`
          : ''}
      </div>
    `).join('');
  }

  function renderExecutionSummary(data, deps) {
    const esc = deps.esc;
    const fmtTime = deps.fmtTime;
    const retryButtonHtml = deps.retryButtonHtml || '';
    const linkLabel = deps.linkLabel || '执行运行详情（运维)';
    const actions = [];
    if (data.prefect_ui_url) {
      actions.push(`<a href="${esc(data.prefect_ui_url)}" target="_blank" class="ext-link text-xs" title="打开该审批触发的执行任务运行详情（运维）">${linkLabel}</a>`);
    }
    if (retryButtonHtml) actions.push(retryButtonHtml);
    const staleAlert = data.heartbeat_stale
      ? `<div class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1 mb-2">
          检测到执行心跳可能过期（约 ${esc(String(data.heartbeat_stale_seconds || 0))} 秒未更新），建议先执行对账。
        </div>`
      : '';
    const recommendedAction = data.recommended_action || '';
    const recommendedActionMap = {
      reconcile_execution: '建议操作：先执行对账（reconcile），确认运行状态后再决定是否重试。',
      retry_execute_in_portal: '建议操作：可直接在 Portal 发起重试执行。',
      observe: '建议操作：当前执行中，建议先观察运行状态。',
      noop: '建议操作：暂无额外操作。',
    };
    const recommendedHint = recommendedAction
      ? `<div class="text-xs text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-md px-2 py-1 mb-2">
          ${esc(recommendedActionMap[recommendedAction] || `建议操作：${recommendedAction}`)}
        </div>`
      : '';
    return `
      <div class="exec-track">
        <div class="flex items-center justify-between gap-3 flex-wrap mb-2">
          <div class="flex items-center gap-2 flex-wrap">
            ${executeBadge(data.execute_status)}
            ${data.execution_task_id ? `<span class="font-mono text-xs text-gray-400">${esc(data.execution_task_id)}</span>` : ''}
          </div>
          <div class="flex items-center gap-2 flex-wrap">${actions.join('')}</div>
        </div>
        <div class="grid grid-cols-2 gap-x-6 gap-y-2 text-sm mb-3">
          <div><span class="text-gray-400">最近尝试：</span><span class="text-gray-700">${esc(data.latest_attempt_status || '—')}</span></div>
          <div><span class="text-gray-400">最近事件：</span><span class="text-gray-700">${esc(eventLabel(data.latest_event_type || ''))}</span></div>
          <div><span class="text-gray-400">开始时间：</span><span class="text-gray-700">${fmtTime(data.latest_attempt_started_at)}</span></div>
          <div><span class="text-gray-400">完成时间：</span><span class="text-gray-700">${fmtTime(data.executed_at || data.latest_attempt_finished_at)}</span></div>
        </div>
        ${staleAlert}
        ${recommendedHint}
        ${data.execute_error ? `<div class="text-xs text-red-500 mb-2">${esc(data.execute_error)}</div>` : ''}
        <div class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">执行时间线</div>
        ${renderExecutionEvents(data.events || [], deps)}
      </div>
    `;
  }

  function renderApprovalExecutionSummary(data, deps) {
    const recommendedAction = data.recommended_action || '';
    const reconcileButtonHtml = deps.reconcileButtonHtml || '';
    const retryButtonHtml = deps.retryButtonHtml || '';
    const orderedActions = recommendedAction === 'reconcile_execution'
      ? `${reconcileButtonHtml}${retryButtonHtml}`
      : `${retryButtonHtml}${reconcileButtonHtml}`;
    return renderExecutionSummary(data, {
      ...deps,
      retryButtonHtml: orderedActions,
    });
  }

  function runtimeOpsLink(url, deps) {
    const esc = deps.esc;
    const label = deps.label || '执行运行详情（运维)';
    if (!url) return deps.emptyHtml || '<span class="text-gray-400">暂无执行运行链接</span>';
    return `<a href="${esc(url)}" target="_blank" rel="noreferrer" class="${deps.className || 'text-indigo-600'}" title="打开该任务对应的执行运行详情（运维）">${label}</a>`;
  }

  function statusChip(status, deps) {
    return global.PortalStatusBadge.chip(status, deps);
  }

  function renderDetailSection(title, bodyHtml, deps) {
    const className = deps && deps.className ? deps.className : 'mt-4';
    const titleClassName = deps && deps.titleClassName
      ? deps.titleClassName
      : 'text-xs font-bold uppercase tracking-wider text-gray-400';
    return `<div class="${className}">
      <div class="${titleClassName}">${title}</div>
      ${bodyHtml}
    </div>`;
  }

  function renderTaskHeader(data, deps) {
    const esc = deps.esc;
    const title = deps.title || '—';
    const subtitle = deps.subtitle || '';
    const trailing = deps.trailing || '';
    return `<div class="flex items-start justify-between gap-4">
      <div>
        <div class="text-sm font-semibold text-gray-900">${esc(title)}</div>
        ${subtitle ? `<div class="text-xs text-gray-500 mt-1">${subtitle}</div>` : ''}
      </div>
      <div class="text-right text-xs text-gray-500">${trailing || ''}</div>
    </div>`;
  }

  function renderFactGrid(items, deps) {
    const className = deps && deps.className ? deps.className : 'grid grid-cols-2 gap-x-6 gap-y-3 text-sm';
    return `<div class="${className}">
      ${items.map(item => `
        <div>
          <span class="${item.labelClassName || 'text-gray-400'}">${item.label}</span>
          ${item.valueHtml || `<span class="${item.valueClassName || 'text-gray-800'}">${item.value || '—'}</span>`}
        </div>
      `).join('')}
    </div>`;
  }

  function renderInfoPanel(title, bodyHtml, deps) {
    const className = deps && deps.className ? deps.className : 'bg-slate-50 rounded-lg p-3 text-sm mb-3';
    const titleClassName = deps && deps.titleClassName
      ? deps.titleClassName
      : 'text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1';
    return `<div class="${className}">
      <div class="${titleClassName}">${title}</div>
      ${bodyHtml}
    </div>`;
  }

  function renderExecutionStatusPanel(data, deps) {
    const esc = deps.esc;
    const fmtTime = deps.fmtTime;
    return renderInfoPanel(deps.title || '执行状态', `
      <div class="flex items-center gap-2 flex-wrap">
        ${executeBadge(data.execute_status)}
        ${data.execution_task_id ? `<span class="font-mono text-xs text-gray-400">${esc(data.execution_task_id)}</span>` : ''}
        ${data.executed_at ? `<span class="text-xs text-gray-500">完成于 ${fmtTime(data.executed_at)}</span>` : ''}
      </div>
      ${data.execute_error ? `<div class="mt-2 text-xs text-red-500">${esc(data.execute_error)}</div>` : ''}
    `, deps.panelDeps || {});
  }

  function renderApprovalStatusBadge(status) {
    return global.PortalStatusBadge.approvalBadge(status);
  }

  function renderRecommendationBadge(rec) {
    return global.PortalStatusBadge.recommendationBadge(rec);
  }

  function renderConfidenceBar(val) {
    return global.PortalStatusBadge.confidenceBar(val);
  }

  function renderAttemptList(items, deps) {
    return global.PortalTimeline.renderAttemptList(items, deps);
  }

  function renderEventList(items, deps) {
    return global.PortalTimeline.renderEventList(items, deps);
  }

  global.ExecutionUI = {
    executeStatusLabel,
    executeBadge,
    eventLabel,
    renderExecutionEvents,
    renderExecutionSummary,
    renderApprovalExecutionSummary,
    runtimeOpsLink,
    statusChip,
    renderDetailSection,
    renderTaskHeader,
    renderFactGrid,
    renderInfoPanel,
    renderExecutionStatusPanel,
    renderApprovalStatusBadge,
    renderRecommendationBadge,
    renderConfidenceBar,
    renderAttemptList,
    renderEventList,
  };
})(window);
