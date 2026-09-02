import { test } from 'node:test';
import assert from 'node:assert/strict';

const { compactIpv6, summarizeBindAddresses } = await import('../js/bind-addresses.js');

test('bind summaries normalize, deduplicate, and rank wildcard addresses first', () => {
  const rows = summarizeBindAddresses([
    '127.0.0.1', '10.0.0.8', '0.0.0.0', '10.0.0.8',
    '::1', '[2001:0db8:0000:0000:0000:0000:0000:0020]', '::',
  ], { showV4: true, showV6: true, density: 'standard' });
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], {
    family: 'v4', label: 'IPv4', primary: '0.0.0.0', display: '0.0.0.0',
    wildcard: true, additional: 2, addresses: ['0.0.0.0', '10.0.0.8', '127.0.0.1'],
  });
  assert.equal(rows[1].primary, '::');
  assert.equal(rows[1].wildcard, true);
  assert.equal(rows[1].additional, 2);
  assert.deepEqual(rows[1].addresses, ['::', '2001:db8::20', '::1']);
});

test('IPv4-mapped IPv6 is shown once as IPv4 and family filters are honored', () => {
  const v4 = summarizeBindAddresses(
    ['::ffff:192.168.1.4', '192.168.1.4', 'fd12::4'],
    { showV4: true, showV6: false });
  assert.equal(v4.length, 1);
  assert.equal(v4[0].family, 'v4');
  assert.deepEqual(v4[0].addresses, ['192.168.1.4']);

  const v6 = summarizeBindAddresses(
    ['::ffff:192.168.1.4', 'fd12::4'],
    { showV4: false, showV6: true });
  assert.equal(v6.length, 1);
  assert.equal(v6[0].family, 'v6');
});

test('long IPv6 addresses use density-aware middle elision', () => {
  const full = '2001:0db8:85a3:0000:0000:8a2e:0370:7334';
  assert.equal(compactIpv6(full, 'loose'), '2001:db8:85a3:…:7334');
  assert.equal(compactIpv6(full, 'standard'), '2001:db8:…:7334');
  assert.equal(compactIpv6(full, 'compact'), '2001:…:7334');
  assert.equal(compactIpv6('::1', 'compact'), '::1');
});
