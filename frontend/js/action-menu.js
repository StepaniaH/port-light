/* Port management disclosure; links and buttons retain native semantics. */
export function mountActionMenu(root) {
  const trigger = root.querySelector('[aria-controls]');
  const panel = root.querySelector('#' + trigger.getAttribute('aria-controls'));
  const items = () => [...panel.querySelectorAll('button:not(:disabled), a[href]')];
  const edge = 12;

  function place() {
    if (panel.hidden) return;
    const rect = trigger.getBoundingClientRect();
    const width = Number(window.innerWidth) || 0;
    const height = Number(window.innerHeight) || 0;
    if (!width || !height) return;
    const available = Math.max(0, width - edge * 2);
    const triggerWidth = Math.ceil(rect.width);
    const top = Math.round(rect.bottom) + 8;
    panel.style.setProperty('--action-menu-top', top + 'px');
    panel.style.setProperty('--action-menu-max-height', Math.max(0, height - top - edge) + 'px');
    panel.style.setProperty('--action-menu-trigger-width', triggerWidth + 'px');
    panel.style.removeProperty('--action-menu-width');
    const menuWidth = Math.min(Math.max(triggerWidth, Math.ceil(panel.getBoundingClientRect().width)), available);
    const left = Math.min(Math.max(Math.round(rect.left), edge), Math.max(edge, width - edge - menuWidth));
    panel.style.setProperty('--action-menu-left', left + 'px');
    panel.style.setProperty('--action-menu-width', menuWidth + 'px');
  }

  function close(restore = false) {
    if (panel.hidden) return;
    panel.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    if (restore) trigger.focus();
  }
  function open(focus = false) {
    panel.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    place();
    if (focus) items()[0]?.focus();
  }
  trigger.addEventListener('click', () => panel.hidden ? open() : close());
  root.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden) {
      event.preventDefault(); event.stopPropagation(); close(true); return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    if (panel.hidden) { open(true); return; }
    const buttons = items();
    const index = buttons.indexOf(document.activeElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : (index + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  });
  panel.addEventListener('click', event => {
    const action = event.target.closest('button, a');
    if (action && !action.disabled) close(panel.contains(document.activeElement));
  });
  document.addEventListener('pointerdown', event => { if (!root.contains(event.target)) close(); });
  document.addEventListener('focusin', event => { if (!root.contains(event.target)) close(); });
  window.addEventListener('hashchange', () => close());
  window.addEventListener('resize', place);
  window.addEventListener('scroll', place, { passive: true });
  document.addEventListener('scroll', place, true);
  return { close };
}
