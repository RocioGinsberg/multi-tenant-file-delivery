(function (global) {
  function esc(value) {
    if (!value) return '';
    const div = document.createElement('div');
    div.textContent = value;
    return div.innerHTML;
  }

  function escAttr(value) {
    return esc(value).replace(/"/g, '&quot;');
  }

  global.PortalDom = {
    esc,
    escAttr,
  };
})(window);
