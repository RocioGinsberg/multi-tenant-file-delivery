(function (global) {
  function fmtDate(value) {
    return value ? new Date(value).toLocaleString('zh-CN') : '—';
  }

  function fmtDateTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    return date.toLocaleDateString('zh-CN') + ' ' + date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function fmtBytes(value) {
    const size = Number(value || 0);
    if (!Number.isFinite(size) || size <= 0) return '—';
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
  }

  global.PortalFormat = {
    fmtDate,
    fmtDateTime,
    fmtBytes,
  };
})(window);
