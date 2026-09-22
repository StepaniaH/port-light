/* Automation examples, activity, and lease controls. */
import { S } from './state.js?v=103';
import { t, escapeHtml } from './text.js?v=103';
import { api } from './api.js?v=103';
import { remainingSeconds, fmtRemaining, formatAgo } from './leases.js?v=103';
import { settingsCard, kvRow } from './settings-format.js?v=103';

  function copyButton(id, labelKey, primary = false) {
    const label = escapeHtml(t(labelKey));
    return '<button type="button" class="' + (primary ? 'btn-primary' : 'btn-secondary') +
      '" data-copy="' + id + '" data-label="' + label + '">' + label + '</button>';
  }

  function snippetBlock(captionKey, id, code) {
    return '<div class="snippet"><div class="snippet-head"><p class="snippet-cap">' +
      escapeHtml(t(captionKey)) + '</p>' + copyButton(id, 'settings.auto.connect.copy') +
      '</div><pre id="' + id + '">' + escapeHtml(code) + '</pre></div>';
  }

  function setupStep(number, titleKey, id, code, hintKey, copyKey, extra = '') {
    return '<section class="auto-step"><span class="auto-step-number" aria-hidden="true">' +
      number + '</span><div class="auto-step-content"><h3>' + escapeHtml(t(titleKey)) +
      '</h3><p class="auto-help">' + escapeHtml(t(hintKey)) +
      '</p><div class="auto-step-actions">' + copyButton(id, copyKey, number === '1') + extra +
      '</div><details class="auto-prompt"><summary>' +
      escapeHtml(t('settings.auto.connect.showPrompt')) + '</summary><pre id="' + id + '">' +
      escapeHtml(code) + '</pre></details></div></section>';
  }

  export function automationCardsHtml(a) {
    const origin = location.origin;
    const base = origin + (location.pathname || '/').replace(/\/(?:index\.html)?$/, '');
    const guide = base + '/ai-setup.md';
    const publicGuide = 'https://raw.githubusercontent.com/StepaniaH/port-light/main/docs/ai-setup.md';
    const port = Number(a.listen_port) > 0 ? String(a.listen_port) : '<port>';
    const dockerEnv = { PORT_LIGHT_URL: 'http://127.0.0.1:' + port };
    const sourceEnv = { PORT_LIGHT_URL: base };
    if (a.auth_required) {
      dockerEnv.PORT_LIGHT_AUTH = '<user:password>';
      sourceEnv.PORT_LIGHT_AUTH = '<user:password>';
    }
    if (a.agent_token) {
      dockerEnv.PORT_LIGHT_AGENT_TOKEN = '<your-token>';
      sourceEnv.PORT_LIGHT_AGENT_TOKEN = '<your-token>';
    }
    const mcpDocker = JSON.stringify({
      mcpServers: {
        'port-light': {
          command: 'docker',
          args: ['exec', '-i', ...Object.keys(dockerEnv).flatMap(key => ['-e', key]),
            'port-light', 'python', '/app/mcp/server.py'],
          env: dockerEnv,
        },
      },
    }, null, 2);
    const mcpSource = JSON.stringify({
      mcpServers: {
        'port-light': {
          command: '/absolute/path/to/port-light-mcp',
          env: sourceEnv,
        },
      },
    }, null, 2);
    const curlUrl = (base + '/api/ports/suggest?count=2').replaceAll("'", "'\\''");
    let curl = "curl --fail-with-body -sS '" + curlUrl + "'";
    if (a.auth_required) curl += ' \\\n  -u "<user:password>"';
    if (a.agent_token) curl += ' \\\n  -H "X-Agent-Token: <your-token>"';

    const cli = "port-light --url '" + base.replaceAll("'", "'\\''") + "' ";
    const setupCommands = cli + 'mcp-config --client codex\n' +
      cli + 'mcp-config --client claude-code\n' + cli + 'verify';

    const connect =
      '<div class="auto-connect">' +
      setupStep('1', 'settings.auto.connect.promptTitle', 'al-setup-prompt',
        t('settings.auto.connect.prompt', { guide, publicGuide, url: base }),
        'settings.auto.connect.promptHint', 'settings.auto.connect.copySetup',
        '<a class="auto-guide" href="' + escapeHtml(guide) + '" target="_blank" rel="noopener">' +
          escapeHtml(t('settings.auto.connect.guide')) + '<span aria-hidden="true"> ↗</span></a>') +
      setupStep('2', 'settings.auto.connect.tryTitle', 'al-try-prompt',
        t('settings.auto.connect.tryPrompt'), 'settings.auto.connect.verifyHint',
        'settings.auto.connect.copyTask') +
      '<details class="auto-manual"><summary>' + escapeHtml(t('settings.auto.connect.manual')) + '</summary>' +
      '<p class="muted">' + escapeHtml(t('settings.auto.connect.manualHint')) + '</p>' +
      snippetBlock('settings.auto.connect.commands', 'al-setup-commands', setupCommands) +
      snippetBlock('settings.auto.connect.mcpSource', 'al-mcp-src', mcpSource) +
      snippetBlock('settings.auto.connect.mcpDocker', 'al-mcp-docker', mcpDocker) +
      '<p class="muted">' + escapeHtml(t('settings.auto.connect.dockerHint')) + '</p>' +
      '<a href="' + escapeHtml(base + '/skill.md') + '" target="_blank" rel="noopener">' +
      escapeHtml(t('settings.auto.connect.skill')) + '</a>' +
      '<p class="muted">' + escapeHtml(t('settings.auto.connect.skillHint')) + '</p>' +
      snippetBlock('settings.auto.connect.curl', 'al-curl', curl) +
      (a.agent_token ? '<p class="muted">' + escapeHtml(t('settings.auto.connect.curlToken')) + '</p>' : '') +
      '</details></div>';

    const statusRows = [
      kvRow('settings.auto.agentToken',
        t(a.agent_token ? 'settings.on' : 'settings.off'),
        a.agent_token ? 'settings.on' : 'settings.off'),
      kvRow('settings.auto.suggest', t('settings.auto.suggestValue'), 'settings.auto.suggestValue'),
      kvRow('settings.auto.metrics', t(a.metrics ? 'settings.on' : 'settings.off'), ''),
      kvRow('settings.auto.webhook', t(a.webhook ? 'settings.on' : 'settings.off'), ''),
      kvRow('settings.auto.history', a.history_days > 0 ? String(a.history_days) : t('settings.off'), ''),
      kvRow('settings.auto.events', t(a.events_stream ? 'settings.on' : 'settings.off'), ''),
    ].join('');

    const ev = a.agent_events || null;
    const activity = ev
      ? '<p class="auto-summary" data-auto-summary>' +
        escapeHtml(t('settings.auto.activity.total')) + ': ' + ev.total + ' · ' +
        escapeHtml(t('settings.auto.activity.activeLeases')) + ': ' + (ev.active_leases || 0) + ' · ' +
        escapeHtml(t('settings.auto.activity.lastUsed', {
          time: ev.last_used_at ? formatAgo(ev.last_used_at) : t('settings.auto.activity.never'),
        })) + '</p>' +
        ((ev.recent || []).length ? '<div class="auto-table-scroll"><table class="auto-table"><thead><tr>' +
        ['thTime', 'thCount', 'thScope', 'thLabel', 'thLeased']
          .map(k => '<th>' + escapeHtml(t('settings.auto.activity.' + k)) + '</th>').join('') +
        '</tr></thead><tbody>' +
        (ev.recent || []).map(r =>
          '<tr><td>' + new Date(r.ts * 1000).toLocaleString() + '</td><td>' + r.count +
          '</td><td>' + escapeHtml(r.scope) + '</td><td>' + escapeHtml(r.label || '—') +
          '</td><td>' + (r.leased ? '✓' : '—') + '</td></tr>').join('') +
        '</tbody></table></div>' : '<p class="auto-empty">' +
          escapeHtml(t('settings.auto.activity.empty')) + '</p>')
      : '<p class="muted" data-auto="activity-disabled">' +
        escapeHtml(t('settings.auto.activity.disabled')) + '</p>';

    const leases = ev && (ev.lease_rows || []).length
      ? (ev.lease_rows).map(l =>
        '<div class="lease-row"><span class="lease-port">' + l.port + '</span>' +
        '<span class="lease-label">' + escapeHtml(l.label || '—') + '</span>' +
        '<span class="lease-left">' + escapeHtml(t('settings.auto.leases.remaining',
          { time: fmtRemaining(remainingSeconds(l.expires_at)) })) + '</span>' +
        '<button type="button" class="btn-delete" data-release-port="' + l.port +
        '" data-reservation="' + !!l.is_reservation + '">' +
        escapeHtml(t('settings.auto.leases.release')) + '</button></div>').join('')
      : '<p class="auto-empty">' + escapeHtml(t('settings.auto.leases.none')) + '</p>';

    return settingsCard('settings.auto.connect.title', 'settings.auto.connect.blurb', connect) +
      settingsCard('settings.auto.status.title', 'settings.auto.status.blurb',
        '<div class="auto-status">' + statusRows + '</div>') +
      settingsCard('settings.auto.activity.title', 'settings.auto.activity.blurb', activity) +
      settingsCard('settings.auto.leases.title', 'settings.auto.leases.blurb', leases);
  }

  export async function releaseLease(port, btn) {
    const reservation = btn.getAttribute && btn.getAttribute('data-reservation') === 'true';
    const token = reservation ? window.prompt(t('settings.auto.leases.tokenPrompt')) : '';
    if (reservation && !token) return;
    btn.disabled = true;
    try {
      const res = await api((reservation ? '/api/reservations/' : '/api/manual-ports/') + port, {
        method: 'DELETE', headers: reservation ? { 'X-Reservation-Token': token } : {},
      });
      if (!res.ok) {
        btn.disabled = false;
        return;
      }
      const metaRes = await api('/api/meta');
      if (metaRes.ok) S.meta = await metaRes.json();
    } catch (err) {
      btn.disabled = false;
      return;
    }
    rerenderAutomationCards();
  }

  export function rerenderAutomationCards() {
    const panel = document.getElementById('settings-panel-automation');
    if (!panel || !S.meta) return;
    panel.innerHTML = automationCardsHtml(S.meta.automation || {});
  }

  let _delegated = false;
  export function ensureAutomationDelegates() {
    if (_delegated) return;
    _delegated = true;
    document.addEventListener('click', function (e) {
      const copyBtn = e.target.closest('[data-copy]');
      if (copyBtn) {
        const src = document.getElementById(copyBtn.getAttribute('data-copy'));
        if (!src) return;
        Promise.resolve().then(() => {
          if (!navigator.clipboard) throw new Error('Clipboard unavailable');
          return navigator.clipboard.writeText(src.textContent.trim());
        }).then(function () {
          copyBtn.textContent = t('settings.auto.connect.copied');
          setTimeout(function () {
            copyBtn.textContent = copyBtn.getAttribute('data-label') ||
              t('settings.auto.connect.copy');
          }, 1200);
        }).catch(function () {
          window.prompt(t('settings.auto.connect.manualCopy'), src.textContent.trim());
        });
        return;
      }
      const relBtn = e.target.closest('[data-release-port]');
      if (relBtn) releaseLease(Number(relBtn.getAttribute('data-release-port')), relBtn);
    });
  }
