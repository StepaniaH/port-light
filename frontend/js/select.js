/* In-page single selects. Native controls retain form values and input/change events. */
const controls = new Map();
let opened;
let nextId = 0;

function setAttribute(node, name, value) {
  if (node.getAttribute(name) !== value) node.setAttribute(name, value);
}

function enhance(select) {
  if (controls.has(select)) { controls.get(select).sync(); return; }
  if (select.multiple || select.size > 1) return;
  const wrapper = document.createElement('span');
  wrapper.className = 'pl-select';
  select.before(wrapper);
  wrapper.append(select);
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'pl-select-trigger';
  button.setAttribute('role', 'combobox');
  button.setAttribute('aria-haspopup', 'listbox');
  button.setAttribute('aria-expanded', 'false');
  wrapper.append(button);
  const value = document.createElement('span');
  button.append(value);
  select.classList.add('pl-select-native');
  select.tabIndex = -1;
  select.setAttribute('aria-hidden', 'true');
  const list = document.createElement('div');
  list.id = 'pl-options-' + (++nextId);
  list.className = 'pl-select-options';
  list.setAttribute('role', 'listbox');
  button.setAttribute('aria-controls', list.id);
  let highlighted = -1;
  let signature = '';
  let typed = '';
  let typedAt = 0;
  const enabled = () => [...select.options].map((option, index) => ({ option, index })).filter(({ option }) =>
    !option.disabled && !option.hidden && !(option.parentElement?.tagName === 'OPTGROUP' && option.parentElement.disabled));

  function label() {
    return select.getAttribute('aria-label') || [...(select.labels || [])].map(item => {
      const copy = item.cloneNode(true);
      for (const child of copy.querySelectorAll('select, .pl-select')) child.remove();
      return copy.textContent.trim();
    }).filter(Boolean).join(' ');
  }
  function position() {
    const rect = button.getBoundingClientRect();
    const availableBelow = window.innerHeight - rect.bottom - 8;
    const above = availableBelow < Math.min(240, list.scrollHeight) && rect.top > availableBelow;
    list.style.maxHeight = Math.max(48, Math.min(320, above ? rect.top - 12 : availableBelow)) + 'px';
    list.style.minWidth = Math.min(rect.width, window.innerWidth - 16) + 'px';
    list.style.maxWidth = (window.innerWidth - 16) + 'px';
    list.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - list.offsetWidth - 8)) + 'px';
    list.style.top = (above ? Math.max(8, rect.top - list.offsetHeight - 4) : rect.bottom + 4) + 'px';
  }
  function highlight(index) {
    highlighted = index;
    for (const row of list.querySelectorAll('[role="option"]')) {
      row.classList.toggle('highlighted', Number(row.dataset.index) === index);
    }
    const active = list.querySelector('[data-index="' + index + '"]');
    if (active) {
      button.setAttribute('aria-activedescendant', active.id);
      active.scrollIntoView({ block: 'nearest' });
    } else button.removeAttribute('aria-activedescendant');
  }
  function close() {
    if (opened !== control) return;
    opened = null;
    list.remove();
    button.setAttribute('aria-expanded', 'false');
    button.removeAttribute('aria-activedescendant');
  }
  function rows() {
    list.replaceChildren(...[...select.options].filter(option => !option.hidden).map(option => {
      const index = option.index;
      const row = document.createElement('div');
      row.id = list.id + '-' + index;
      row.dataset.index = String(index);
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', String(option.selected));
      const disabled = !enabled().some(item => item.index === index);
      row.setAttribute('aria-disabled', String(disabled));
      row.textContent = (option.parentElement?.tagName === 'OPTGROUP' ? option.parentElement.label + ' — ' : '') + option.label;
      row.addEventListener('pointermove', () => { if (!disabled) highlight(index); });
      row.addEventListener('pointerdown', event => event.preventDefault());
      row.addEventListener('click', () => { if (!disabled) commit(index); });
      return row;
    }));
  }
  function sync() {
    if (value.textContent !== (select.selectedOptions[0]?.label || '')) value.textContent = select.selectedOptions[0]?.label || '';
    if (button.disabled !== select.disabled) button.disabled = select.disabled;
    if (wrapper.hidden !== select.hidden) wrapper.hidden = select.hidden;
    setAttribute(button, 'aria-label', label());
    setAttribute(list, 'aria-label', label());
    setAttribute(button, 'aria-required', String(select.required));
    setAttribute(button, 'aria-invalid', select.getAttribute('aria-invalid') || 'false');
    const description = select.getAttribute('aria-describedby');
    if (description) setAttribute(button, 'aria-describedby', description);
    else button.removeAttribute('aria-describedby');
    if (select.disabled || select.hidden || !select.isConnected) close();
    if (opened === control) {
      const current = JSON.stringify([...select.options].map(option => [option.label, option.value, option.disabled, option.hidden, option.selected, option.parentElement?.disabled, option.parentElement?.label]));
      if (signature !== current) { signature = current; rows(); highlight(select.selectedIndex); position(); }
    }
  }
  function open() {
    if (select.disabled || !enabled().length) return;
    opened?.close();
    opened = control;
    document.body.append(list);
    button.setAttribute('aria-expanded', 'true');
    signature = '';
    sync();
    position();
    highlight(enabled().some(item => item.index === select.selectedIndex) ? select.selectedIndex : enabled()[0].index);
  }
  function commit(index) {
    const changed = select.selectedIndex !== index;
    select.selectedIndex = index;
    close();
    button.focus();
    if (changed) {
      select.dispatchEvent(new Event('input', { bubbles: true }));
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
    sync();
  }
  const control = { sync, close, button, list, position };
  controls.set(select, control);
  button.addEventListener('click', event => { event.preventDefault(); sync(); if (opened === control) close(); else open(); });
  button.addEventListener('keydown', event => {
    const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' ', 'Escape'];
    if (event.key === 'Tab') { close(); return; }
    if (keys.includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === 'Escape') { close(); return; }
      if (event.key === 'Enter' || event.key === ' ') {
        if (opened === control && highlighted >= 0) commit(highlighted);
        else open();
        return;
      }
      if (opened !== control) { open(); if (event.key.startsWith('Arrow')) return; }
      const items = enabled();
      const current = items.findIndex(item => item.index === highlighted);
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : Math.max(0, Math.min(items.length - 1, current + (event.key === 'ArrowDown' ? 1 : -1)));
      if (items[index]) highlight(items[index].index);
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      event.stopPropagation();
      if (Date.now() - typedAt > 700) typed = '';
      typedAt = Date.now(); typed += event.key.toLocaleLowerCase();
      const items = enabled();
      const match = items.find(item => item.option.label.toLocaleLowerCase().startsWith(typed));
      if (match) { if (opened === control) highlight(match.index); else commit(match.index); }
    }
  });
  button.addEventListener('blur', close);
  select.addEventListener('focus', () => button.focus());
  select.addEventListener('input', sync);
  select.addEventListener('change', sync);
  select.addEventListener('invalid', event => { event.preventDefault(); button.focus(); setAttribute(button, 'aria-invalid', 'true'); });
  sync();
}

export function enhanceSelects(root = document) {
  for (const [select, control] of controls) {
    if (!select.isConnected) { control.close(); controls.delete(select); }
  }
  if (root.matches?.('select')) enhance(root);
  for (const select of root.querySelectorAll('select')) enhance(select);
}

export function observeSelects(root) {
  enhanceSelects(root);
  const observer = new MutationObserver(records => {
    if (records.some(record => record.target.closest?.('select, label') ||
        [...record.addedNodes, ...record.removedNodes].some(node => node.nodeType === 1 && (node.matches('select, option, optgroup') || node.querySelector('select'))))) {
      enhanceSelects(root);
    }
  });
  observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true,
    attributeFilter: ['disabled', 'hidden', 'selected', 'label', 'aria-label', 'aria-describedby', 'aria-invalid', 'required'] });
  const outside = event => { if (opened && !opened.button.contains(event.target) && !opened.list.contains(event.target)) opened.close(); };
  const position = event => { if (opened && !opened.list.contains(event.target)) opened.position(); };
  document.addEventListener('pointerdown', outside);
  window.addEventListener('resize', position);
  document.addEventListener('scroll', position, true);
  root.addEventListener('reset', () => requestAnimationFrame(() => enhanceSelects(root)));
  return () => {
    observer.disconnect(); opened?.close();
    document.removeEventListener('pointerdown', outside);
    window.removeEventListener('resize', position);
    document.removeEventListener('scroll', position, true);
  };
}
