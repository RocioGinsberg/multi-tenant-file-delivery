(function (global) {
  function renderCard(config) {
    const factsHtml = config.factsHtml ? `<div class="mt-3">${config.factsHtml}</div>` : '';
    const bodyHtml = config.bodyHtml || '';
    const actionsHtml = config.actionsHtml ? `<div class="${config.actionsClassName || 'detail-actions'}">${config.actionsHtml}</div>` : '';
    const sectionsHtml = (config.sections || []).join('');
    return `${config.headerHtml || ''}${factsHtml}${bodyHtml}${actionsHtml}${sectionsHtml}${config.footerHtml || ''}`;
  }

  global.PortalTaskDetail = {
    renderCard,
  };
})(window);
