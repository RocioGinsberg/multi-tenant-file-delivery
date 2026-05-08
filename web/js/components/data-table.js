(function (global) {
  function emptyRow(colspan, message, options) {
    const style = options && options.style
      ? options.style
      : 'text-align:center;color:var(--c-text-3);padding:24px';
    return `<tr><td colspan="${colspan}" style="${style}">${message}</td></tr>`;
  }

  function setBodyRows(target, rowsHtml, options) {
    const tbody = typeof target === 'string' ? document.getElementById(target) : target;
    if (!tbody) return;
    if (rowsHtml && rowsHtml.trim()) {
      tbody.innerHTML = rowsHtml;
      return;
    }
    tbody.innerHTML = emptyRow(options.colspan, options.message || '暂无数据', options);
  }

  global.PortalDataTable = {
    emptyRow,
    setBodyRows,
  };
})(window);
