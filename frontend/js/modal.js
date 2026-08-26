/* Generic modal helpers (add-port and hidden-unlock dialogs). */

import { S } from './state.js?v=71';

export function openModal(id) {
  S.focusBack = document.activeElement;
  document.getElementById(id).classList.remove('hidden');
  document.documentElement.classList.add('modal-open');
  const err = document.getElementById('add-error');
  if (id === 'add-modal' && err) {
    err.hidden = true;
    err.classList.add('hidden');
    err.textContent = '';
  }
  const unlockErr = document.getElementById('unhide-error');
  if (id === 'unhide-modal' && unlockErr) {
    unlockErr.hidden = true;
    unlockErr.classList.add('hidden');
    unlockErr.textContent = '';
  }
  const unlockInput = document.getElementById('unhide-password');
  if (id === 'unhide-modal' && unlockInput) {
    unlockInput.removeAttribute('aria-invalid');
  }
  const input = document.getElementById(id).querySelector('input');
  if (input) input.focus();
}

export function closeModals() {
  document.querySelectorAll('.modal').forEach(function (m) { m.classList.add('hidden'); });
  document.documentElement.classList.remove('modal-open');
  S.pendingAfterUnlock = null;
  if (S.focusBack && typeof S.focusBack.focus === 'function') S.focusBack.focus();
  S.focusBack = null;
}

export function modalOpen() {
  return !!document.querySelector('.modal:not(.hidden)');
}
