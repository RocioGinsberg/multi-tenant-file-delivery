(function (global) {
  const ROOT_ID = 'portal-modal-root';
  let activeState = null;

  function ensureRoot() {
    let root = document.getElementById(ROOT_ID);
    if (root) return root;
    root = document.createElement('div');
    root.id = ROOT_ID;
    root.style.cssText = 'position:fixed;inset:0;display:none;align-items:center;justify-content:center;z-index:120';
    document.body.appendChild(root);
    return root;
  }

  function cleanupActive() {
    if (!activeState) return;
    document.removeEventListener('keydown', activeState.onKeydown);
    activeState = null;
  }

  function close(root) {
    cleanupActive();
    root.style.display = 'none';
    root.innerHTML = '';
  }

  function showDialog(options) {
    const root = ensureRoot();
    return new Promise((resolve) => {
      root.style.display = 'flex';
      root.innerHTML = `
        <div data-modal-overlay style="position:absolute;inset:0;background:rgba(15,23,42,.34)"></div>
        <div role="dialog" aria-modal="true" style="position:relative;width:min(92vw,460px);background:var(--c-surface);border:1px solid var(--c-border);border-radius:16px;box-shadow:0 24px 64px rgba(15,23,42,.18);padding:22px">
          <div style="font-size:16px;font-weight:800;color:var(--c-text);margin-bottom:8px">${options.title || ''}</div>
          <div style="font-size:13px;line-height:1.7;color:var(--c-text-2);margin-bottom:16px">${options.message || ''}</div>
          ${options.mode === 'prompt' ? `<input data-modal-input type="text" value="${options.defaultValue || ''}" style="width:100%;padding:9px 12px;border:1px solid var(--c-border);border-radius:10px;font-size:13px;outline:none;background:var(--c-surface);margin-bottom:16px"/>` : ''}
          <div style="display:flex;justify-content:flex-end;gap:8px">
            <button data-modal-cancel type="button" style="padding:8px 16px;background:var(--c-surface);color:var(--c-text);border:1px solid var(--c-border);border-radius:8px;font-size:13px;font-weight:600;cursor:pointer">${options.cancelLabel || '取消'}</button>
            <button data-modal-confirm type="button" style="padding:8px 16px;background:${options.confirmTone === 'danger' ? '#dc2626' : '#2563eb'};color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer">${options.confirmLabel || '确认'}</button>
          </div>
        </div>
      `;

      const input = root.querySelector('[data-modal-input]');
      const onCancel = () => {
        close(root);
        resolve(options.mode === 'prompt' ? null : false);
      };
      const onConfirm = () => {
        const value = input ? input.value : true;
        close(root);
        resolve(options.mode === 'prompt' ? value : true);
      };
      const onKeydown = (event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          onCancel();
        }
        if (event.key === 'Enter' && options.mode === 'prompt') {
          event.preventDefault();
          onConfirm();
        }
      };

      root.querySelector('[data-modal-overlay]').addEventListener('click', onCancel, {once: true});
      root.querySelector('[data-modal-cancel]').addEventListener('click', onCancel, {once: true});
      root.querySelector('[data-modal-confirm]').addEventListener('click', onConfirm, {once: true});
      cleanupActive();
      activeState = {root, onKeydown};
      document.addEventListener('keydown', onKeydown);

      if (input) {
        input.focus();
        input.select();
      } else {
        root.querySelector('[data-modal-confirm]').focus();
      }
    });
  }

  function show(options) {
    const root = ensureRoot();
    root.style.display = 'flex';
    root.innerHTML = `
      <div data-modal-overlay style="position:absolute;inset:0;background:rgba(15,23,42,.34)"></div>
      <div role="dialog" aria-modal="true" style="position:relative;width:min(92vw,${options.width || '560px'});background:var(--c-surface);border:1px solid var(--c-border);border-radius:16px;box-shadow:0 24px 64px rgba(15,23,42,.18);padding:24px;max-height:80vh;overflow-y:auto">
        ${options.title ? `<div style="font-size:16px;font-weight:800;color:var(--c-text);margin-bottom:14px">${options.title}</div>` : ''}
        <div data-modal-body>${options.bodyHtml || ''}</div>
      </div>
    `;

    const onClose = () => close(root);
    const onKeydown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      }
    };

    cleanupActive();
    activeState = {root, onKeydown};
    document.addEventListener('keydown', onKeydown);
    root.querySelector('[data-modal-overlay]').addEventListener('click', onClose, {once: true});

    return {
      root,
      close: onClose,
      setBody(html) {
        const body = root.querySelector('[data-modal-body]');
        if (body) body.innerHTML = html;
      },
    };
  }

  async function confirm(options) {
    return showDialog({
      mode: 'confirm',
      cancelLabel: '取消',
      confirmLabel: '确认',
      ...(options || {}),
    });
  }

  async function prompt(options) {
    return showDialog({
      mode: 'prompt',
      cancelLabel: '取消',
      confirmLabel: '确定',
      defaultValue: '',
      ...(options || {}),
    });
  }

  global.PortalModal = {
    show,
    closeActive() {
      if (activeState) close(activeState.root);
    },
    confirm,
    prompt,
  };
})(window);
