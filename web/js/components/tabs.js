(function (global) {
  function switchNamedTabs(options) {
    const buttons = Array.from(document.querySelectorAll(options.buttonSelector || '.tab-btn'));
    const names = options.names || [];
    buttons.forEach((button, index) => {
      button.classList.toggle('active', names[index] === options.active);
    });
    const panels = options.panels || {};
    Object.entries(panels).forEach(([name, panel]) => {
      const el = document.getElementById(panel.id);
      if (!el) return;
      el.style.display = name === options.active ? (panel.display || '') : 'none';
    });
  }

  function switchElementTabs(options) {
    const active = options.active;
    Object.entries(options.buttons || {}).forEach(([name, button]) => {
      if (button) button.classList.toggle('active', name === active);
    });
    Object.entries(options.panels || {}).forEach(([name, panel]) => {
      if (panel) panel.style.display = name === active ? '' : 'none';
    });
  }

  global.PortalTabs = {
    switchNamedTabs,
    switchElementTabs,
  };
})(window);
