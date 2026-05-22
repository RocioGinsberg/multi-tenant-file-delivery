(function (global) {
  'use strict';

  function render(selector) {
    var target = document.querySelector(selector || 'header.hdr');
    if (!target) return;
    var path = window.location.pathname || '/';
    var uploadActive = path === '/' || path.endsWith('/index.html');
    var workspaceActive = path.endsWith('/workspaces.html');
    target.innerHTML = ''
      + '<div class="hdr-inner">'
      +   '<a href="/" class="logo">'
      +     '<div class="logo-mark">AU</div>'
      +     '<span class="logo-text">Auto Upload</span>'
      +   '</a>'
      +   '<nav class="nav">'
      +     '<a href="/" class="nav-link ' + (uploadActive ? 'active' : '') + '">上传台</a>'
      +     '<a href="/workspaces.html" class="nav-link ' + (workspaceActive ? 'active' : '') + '">子公司视图</a>'
      +     '<div class="conn-ind">'
      +       '<span class="dot" id="dot"></span>'
      +       '<span id="conn-status">连接中…</span>'
      +     '</div>'
      +   '</nav>'
      + '</div>';
  }

  function setConn(ok) {
    var dot = document.getElementById('dot');
    var label = document.getElementById('conn-status');
    if (dot) dot.className = 'dot ' + (ok ? 'ok' : 'err');
    if (label) label.textContent = ok ? '已连接' : '连接失败';
  }

  global.PortalNav = {
    render: render,
    setConn: setConn,
  };
})(window);
