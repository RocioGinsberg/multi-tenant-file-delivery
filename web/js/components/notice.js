(function (global) {
  function block(message, options) {
    const tone = options && options.tone ? options.tone : 'error';
    const styleMap = {
      error: 'font-size:13px;color:#dc2626;padding:16px 0;text-align:center',
      warn: 'font-size:13px;color:#92400e;padding:16px 0;text-align:center',
      info: 'font-size:13px;color:var(--c-text-3);padding:16px 0;text-align:center',
    };
    const style = options && options.style ? options.style : styleMap[tone] || styleMap.error;
    return `<div style="${style}">${message || ''}</div>`;
  }

  global.PortalNotice = {
    block,
  };
})(window);
