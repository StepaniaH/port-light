/* Focus-trap and roving-focus helpers shared by modals and chip rows. */

export function moveChipFocus(container, key) {
  const chips = Array.prototype.slice.call(container.querySelectorAll('button'));
  const idx = chips.indexOf(document.activeElement);
  if (idx < 0) return false;
  let next = idx;
  if (key === 'ArrowRight' || key === 'ArrowDown') next = Math.min(chips.length - 1, idx + 1);
  else if (key === 'ArrowLeft' || key === 'ArrowUp') next = Math.max(0, idx - 1);
  else if (key === 'Home') next = 0;
  else if (key === 'End') next = chips.length - 1;
  else return false;
  if (chips[next]) chips[next].focus();
  return true;
}

export function trapTab(e, root) {
  if (!root) return;
  const nodes = root.querySelectorAll(
    'button, input, select, textarea, a[href], summary, [tabindex]:not([tabindex="-1"])'
  );
  const list = Array.prototype.filter.call(nodes, function (el) {
    if (el.disabled) return false;
    if (el.closest && el.closest('[inert]')) return false;
    return el.getClientRects().length > 0 || el === document.activeElement;
  });
  if (!list.length) return;
  const first = list[0];
  const last = list[list.length - 1];
  if (!root.contains(document.activeElement)) {
    e.preventDefault();
    (e.shiftKey ? last : first).focus();
    return;
  }
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}
