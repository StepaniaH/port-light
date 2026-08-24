/* Minimal browser globals so the ES modules can load under `node --test`.
   Only what module top-level code touches: dom.js resolves its element
   references here and state.js reads sessionStorage once. Import this file
   before dynamically importing anything under ../js/. */

function stubElement() {
  return {
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    setAttribute() {},
    getAttribute: () => null,
    removeAttribute() {},
    addEventListener() {},
    focus() {},
    querySelector: () => null,
    style: { setProperty() {} },
    hidden: false,
    textContent: '',
    value: '',
  };
}

/* Overwrite unconditionally (Node ≥22 ships an experimental localStorage that
   warns without --localstorage-file); a plain in-memory stub is deterministic. */

function memoryStorage() {
  const store = new Map();
  return {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
}

globalThis.sessionStorage = memoryStorage();
globalThis.localStorage = memoryStorage();

if (!globalThis.document) {
  const byId = new Map();
  globalThis.document = {
    getElementById(id) {
      if (!byId.has(id)) byId.set(id, stubElement());
      return byId.get(id);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    documentElement: stubElement(),
    activeElement: null,
    addEventListener() {},
    createElement() {
      const el = { _text: '' };
      Object.defineProperty(el, 'textContent', {
        set(v) { el._text = String(v); },
        get() { return el._text; },
      });
      Object.defineProperty(el, 'innerHTML', {
        get() {
          return el._text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        },
      });
      return el;
    },
  };
}

if (!globalThis.window) {
  globalThis.window = globalThis;
}

if (!globalThis.window.matchMedia) {
  globalThis.window.matchMedia = () => ({ matches: false });
}
