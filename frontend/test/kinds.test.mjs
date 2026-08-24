/* Tests for frontend/js/kinds.js — kind chip matchers over occupancy rows.
   Matchers are used inside .some()/filters, so missing fields yield falsy
   (not strictly false); positives are pinned to true. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { KIND_MATCHERS } from '../js/kinds.js';

const row = (extra) => Object.assign({ protocol: 'tcp', bind_scope: 'lan' }, extra);

test('running matches live container statuses only', () => {
  const m = KIND_MATCHERS.running;
  assert.equal(m(row({ containers: [{ status: 'running' }] })), true);
  assert.equal(m(row({ containers: [{ status: 'paused' }] })), true);
  assert.equal(m(row({ containers: [{ status: 'restarting' }] })), true);
  assert.ok(!m(row({ containers: [{ status: 'exited' }] })));
  assert.ok(!m(row({ containers: [] })));
  assert.ok(!m(row()));
});

test('system matches source type or known-service category', () => {
  const m = KIND_MATCHERS.system;
  assert.equal(m(row({ source_type: 'system' })), true);
  assert.equal(m(row({ known_service: { category: 'system' } })), true);
  assert.ok(!m(row({ source_type: 'docker', known_service: { category: 'media' } })));
  assert.ok(!m(row()));
});

test('docker matches docker rows or any container presence', () => {
  const m = KIND_MATCHERS.docker;
  assert.equal(m(row({ source_type: 'docker' })), true);
  assert.equal(m(row({ containers: [{}] })), true);
  assert.ok(!m(row({ source_type: 'compose', containers: [] })));
  assert.ok(!m(row()));
});

test('access requires the known-service flag', () => {
  const m = KIND_MATCHERS.access;
  assert.equal(m(row({ known_service: { is_access_port: true } })), true);
  assert.ok(!m(row({ known_service: { is_access_port: false } })));
  assert.ok(!m(row()));
});

test('udp matches protocols containing udp', () => {
  const m = KIND_MATCHERS.udp;
  assert.equal(m(row({ protocol: 'tcp,udp' })), true);
  assert.equal(m(row({ protocol: 'udp' })), true);
  assert.equal(m(row({ protocol: 'tcp' })), false);
  assert.equal(m(row({ protocol: undefined })), false);
});

test('scope and hidden matchers read their row fields', () => {
  assert.equal(KIND_MATCHERS.localhost(row({ bind_scope: 'localhost' })), true);
  assert.equal(KIND_MATCHERS.localhost(row({ bind_scope: 'public' })), false);
  assert.equal(KIND_MATCHERS.public(row({ bind_scope: 'public' })), true);
  assert.equal(KIND_MATCHERS.public(row({ bind_scope: 'localhost' })), false);
  assert.equal(KIND_MATCHERS.hidden(row({ is_hidden: true })), true);
  assert.equal(KIND_MATCHERS.hidden(row({ is_hidden: false })), false);
  assert.equal(KIND_MATCHERS.hidden(row()), false);
});
