(function (global) {
  function block(message, options) {
    const style = options && options.style
      ? options.style
      : 'font-size:13px;color:var(--c-text-3);padding:32px 0;text-align:center';
    return `<div style="${style}">${message || '暂无数据'}</div>`;
  }

  global.PortalEmptyState = {
    block,
  };
})(window);
