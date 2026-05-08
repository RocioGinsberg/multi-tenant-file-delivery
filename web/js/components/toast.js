(function (global) {
  function showClassToast(elementId, message, options) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const ok = options.ok !== false;
    const duration = options.duration || 2500;
    el.textContent = message;
    el.className = `toast show ${ok ? 'toast-ok' : 'toast-err'}`;
    clearTimeout(el._toastTimer);
    el._toastTimer = setTimeout(() => {
      el.className = 'toast';
    }, duration);
  }

  function showFloatingToast(elementId, message, options) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const duration = options.duration || 3000;
    const styles = {
      ok: 'background:#d1fae5;color:#065f46;border:1px solid #a7f3d0',
      err: 'background:#fee2e2;color:#991b1b;border:1px solid #fecaca',
      warn: 'background:#fef3c7;color:#92400e;border:1px solid #fde68a',
    };
    el.style.cssText = `${options.baseStyle || ''};${styles[options.type] || styles.ok}`;
    el.textContent = message;
    el.style.opacity = '1';
    el.style.transform = 'translateY(0)';
    clearTimeout(el._toastTimer);
    el._toastTimer = setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(8px)';
    }, duration);
  }

  function makeClassToast(elementId, baseOptions) {
    const defaults = baseOptions || {};
    return function boundClassToast(message, ok, extraOptions) {
      const options =
        typeof ok === 'object' && ok !== null
          ? ok
          : { ok: ok !== false };
      showClassToast(elementId, message, {
        ...defaults,
        ...options,
        ...(extraOptions || {}),
      });
    };
  }

  function makeFloatingToast(elementId, baseOptions) {
    const defaults = baseOptions || {};
    return function boundFloatingToast(message, type, extraOptions) {
      const options =
        typeof type === 'object' && type !== null
          ? type
          : { type: type || 'ok' };
      showFloatingToast(elementId, message, {
        ...defaults,
        ...options,
        ...(extraOptions || {}),
      });
    };
  }

  global.PortalToast = {
    showClassToast,
    showFloatingToast,
    makeClassToast,
    makeFloatingToast,
  };
})(window);
