/* Group declarations without changing the underlying per-port records. */
export function groupPorts(rows, mode = 'service') {
  const groups = new Map();
  for (const row of rows) {
    const configs = row.compose_configs || [];
    const identities = [...new Map(configs.map(c => {
      const project = c.project_name || c.project_dir;
      const key = JSON.stringify([c.project_dir || project, mode === 'service' ? c.service_name : '']);
      return [key, { key, label: project + (mode === 'service' ? ' / ' + c.service_name : '') }];
    })).values()];
    if (!identities.length) identities.push({ key: 'other', label: '' });
    for (const identity of identities) {
      if (!groups.has(identity.key)) groups.set(identity.key, { ...identity, rows: [] });
      groups.get(identity.key).rows.push(row);
    }
  }
  return [...groups.values()];
}

export function portRuns(rows) {
  const runs = [];
  for (const row of [...rows].sort((a, b) => a.port - b.port)) {
    const last = runs[runs.length - 1];
    // Keep conflicts, visibility and status boundaries visible in the summary.
    if (last && last.at(-1).port + 1 === row.port &&
        last[0].status === row.status && !!last[0].conflict === !!row.conflict &&
        !!last[0].is_hidden === !!row.is_hidden && last[0].protocol === row.protocol) last.push(row);
    else runs.push([row]);
  }
  return runs;
}
