/* Verified local extension assets for workspace and settings surfaces. */

function extensionAssets(link, name, fields) {
  const extension = link?.[name];
  const path = link?.path;
  const allowed = new Set([...fields, 'revision']);
  if (extension?.api !== 1 || typeof path !== 'string' ||
      !/^\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\/?$/.test(path) ||
      Object.keys(extension).some(key => !allowed.has(key))) return null;
  for (const [key, suffix] of [['entry', 'js'], ['stylesheet', 'css']]) {
    const asset = extension[key];
    if (typeof asset !== 'string' || !asset.startsWith(path.replace(/\/$/, '') + '/') ||
        !new RegExp('^/(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_-]+\\.' + suffix + '$').test(asset)) return null;
  }
  if (extension.revision !== undefined && !/^[a-f0-9]{64}$/.test(extension.revision)) return null;
  const revision = extension.revision || '';
  const versioned = asset => revision ? asset + '?v=' + revision : asset;
  return { entry: versioned(extension.entry), stylesheet: versioned(extension.stylesheet), revision };
}

export function workspaceAssets(link) {
  return extensionAssets(link, 'workspace', ['api', 'entry', 'stylesheet', 'port_action']);
}

export function settingsAssets(link) {
  return extensionAssets(link, 'settings', ['api', 'entry', 'stylesheet']);
}
