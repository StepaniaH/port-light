/* Minimal browser globals so the ES modules can load under `node --test`.
   Only what module top-level code touches: dom.js resolves its element
   references here and state.js reads sessionStorage once. Import this file
   before dynamically importing anything under ../js/. Elements also support
   enough tree operations (appendChild/remove, attribute maps, and a small
   querySelectorAll over parsed innerHTML) for render tests that inspect
   generated settings markup. */

function memoryStorage() {
  const store = new Map();
  return {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
}

/* Overwrite unconditionally (Node ≥22 ships an experimental localStorage that
   warns without --localstorage-file); a plain in-memory stub is deterministic. */

globalThis.sessionStorage = memoryStorage();
globalThis.localStorage = memoryStorage();

const VOID_TAGS = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
  'link', 'meta', 'param', 'source', 'track', 'wbr']);

const byId = new Map();
let bodyEl = null;

function decodeEntities(text) {
  return String(text)
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

function escapeText(text) {
  return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function parseCompound(part) {
  const out = { tag: '', id: '', classes: [], attrs: [] };
  const tag = part.match(/^[a-zA-Z][\w-]*/);
  if (tag) out.tag = tag[0].toLowerCase();
  const id = part.match(/#([\w-]+)/);
  if (id) out.id = id[1];
  const classes = part.matchAll(/\.([\w-]+)/g);
  for (const c of classes) out.classes.push(c[1]);
  const attrs = part.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g);
  for (const a of attrs) out.attrs.push({ name: a[1], value: a[2] === undefined ? null : a[2] });
  return out;
}

function matchCompound(node, part) {
  const c = parseCompound(part);
  if (c.tag && node.tagName !== c.tag) return false;
  if (c.id && node.id !== c.id) return false;
  for (const cls of c.classes) {
    if (String(node.className || '').split(/\s+/).indexOf(cls) < 0) return false;
  }
  for (const a of c.attrs) {
    const v = node.getAttribute(a.name);
    if (v === null) return false;
    if (a.value !== null && v !== a.value) return false;
  }
  return true;
}

function chainMatch(node, tokens) {
  let ti = tokens.length - 1;
  if (!matchCompound(node, tokens[ti].compound)) return false;
  let el = node.parentNode;
  ti--;
  while (ti >= 0) {
    if (tokens[ti + 1].comb === '>') {
      if (!el || !matchCompound(el, tokens[ti].compound)) return false;
      el = el.parentNode;
    } else {
      while (el && !matchCompound(el, tokens[ti].compound)) el = el.parentNode;
      if (!el) return false;
      el = el.parentNode;
    }
    ti--;
  }
  return true;
}

function walk(node, fn) {
  for (const child of node.childNodes.slice()) {
    fn(child);
    walk(child, fn);
  }
}

function tokenize(selector) {
  return selector.trim().replace(/>/g, ' > ').split(/\s+/)
    .map((part, i) => ({ comb: i === 0 ? '' : part === '>' ? '>' : ' ', compound: part === '>' ? '' : part }))
    .filter((t) => t.compound !== '');
}

function parseHtml(hostNode, html) {
  const rootFrame = { node: hostNode, children: [] };
  let stack = [rootFrame];
  const re = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)>|<([a-zA-Z][\w-]*)((?:\s+[\w-]+(?:=(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[0].charAt(0) === '<' && m[0].charAt(1) === '!') continue;
    if (m[1]) {
      const name = m[1].toLowerCase();
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tag === name) {
          stack[i].node.childNodes = stack[i].children;
          stack = stack.slice(0, i);
          break;
        }
      }
      continue;
    }
    const node = makeElement(m[2].toLowerCase());
    const attrRe = /([\w-]+)(?:=("([^"]*)"|'([^']*)'|[^\s>]+))?/g;
    let am;
    while ((am = attrRe.exec(m[3] || ''))) {
      const raw = am[3] !== undefined ? am[3] : am[4] !== undefined ? am[4] : am[2];
      node.setAttribute(am[1], raw === undefined ? '' : decodeEntities(raw));
    }
    const frame = stack[stack.length - 1];
    node.parentNode = frame.node;
    frame.children.push(node);
    if (!VOID_TAGS.has(node.tagName) && m[4] !== '/') {
      stack.push({ node: node, tag: node.tagName, children: [] });
    }
  }
  while (stack.length > 1) {
    const frame = stack.pop();
    frame.node.childNodes = frame.children;
  }
  return rootFrame.children;
}

if (!globalThis.document) {
  function createElement(tag) {
    return makeElement(String(tag || 'div').toLowerCase());
  }
  globalThis.document = {
    getElementById(id) {
      if (!byId.has(id)) byId.set(id, makeElement('div'));
      return byId.get(id);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    documentElement: makeElement('html'),
    activeElement: null,
    addEventListener() {},
    createElement,
    get body() {
      if (!bodyEl) bodyEl = makeElement('body');
      return bodyEl;
    },
  };
}

function makeElement(tag) {
  const node = {
    tagName: tag,
    childNodes: [],
    parentNode: null,
    attrs: {},
    _id: '',
    _text: '',
    _inner: null,
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    setAttribute(k, v) {
      node.attrs[k] = String(v);
      if (k === 'class') node.className = String(v);
    },
    getAttribute(k) {
      return Object.prototype.hasOwnProperty.call(node.attrs, k) ? node.attrs[k] : null;
    },
    removeAttribute(k) { delete node.attrs[k]; },
    addEventListener() {},
    focus() {},
    style: { setProperty() {} },
    hidden: false,
    value: '',
    disabled: false,
    className: '',
    tabIndex: 0,
    elements: {},
    appendChild(child) {
      child.parentNode = node;
      node.childNodes.push(child);
      return child;
    },
    remove() {
      if (node._id && byId.get(node._id) === node) byId.delete(node._id);
      if (node.parentNode) {
        const sibs = node.parentNode.childNodes;
        const i = sibs.indexOf(node);
        if (i >= 0) sibs.splice(i, 1);
        node.parentNode = null;
      }
    },
    querySelector(sel) {
      const hit = node.querySelectorAll(sel)[0];
      return hit === undefined ? null : hit;
    },
    querySelectorAll(selector) {
      const tokens = tokenize(String(selector));
      if (!tokens.length) return [];
      const out = [];
      walk(node, function (el) {
        if (chainMatch(el, tokens)) out.push(el);
      });
      return out;
    },
  };
  Object.defineProperty(node, 'id', {
    get() { return node._id; },
    set(v) {
      if (node._id && byId.get(node._id) === node) byId.delete(node._id);
      node._id = String(v);
      if (node._id) byId.set(node._id, node);
    },
  });
  Object.defineProperty(node, 'textContent', {
    get() { return node._text; },
    set(v) { node._text = v === null || v === undefined ? '' : String(v); },
  });
  Object.defineProperty(node, 'innerHTML', {
    get() {
      if (node._inner !== null) return node._inner;
      return escapeText(node._text);
    },
    set(v) {
      node._inner = String(v);
      node.childNodes = parseHtml(node, node._inner);
    },
  });
  return node;
}

if (!globalThis.window) {
  globalThis.window = globalThis;
}

if (!globalThis.location) {
  globalThis.location = { origin: 'http://127.0.0.1:2100' };
}

if (!globalThis.window.matchMedia) {
  globalThis.window.matchMedia = () => ({ matches: false });
}
