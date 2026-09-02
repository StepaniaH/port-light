import { test } from 'node:test';
import assert from 'node:assert/strict';

const { cardBindAddresses, compactIpv6, summarizeBindAddresses } = await import('../js/bind-addresses.js');

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

test('equivalent mapped IPv6 spellings share one IPv4 summary', () => {
  const addresses = ['192.168.1.4', '::ffff:192.168.1.4', '::ffff:c0a8:104',
    '0:0:0:0:0:FFFF:192.168.1.4', '[0:0:0:0:0:ffff:c0a8:104]'];
  const rows = summarizeBindAddresses(addresses);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].addresses, ['192.168.1.4']);
  assert.deepEqual(summarizeBindAddresses(addresses, { showV4: false }), []);
});

test('IPv6 elision preserves leading and trailing zero compression and zone case', () => {
  assert.equal(compactIpv6('2001:db8:1234::', 'compact'), '2001:…::');
  assert.equal(compactIpv6('2001:db8:1234::', 'standard'), '2001:db8:1234::');
  assert.equal(compactIpv6('2001:db8:1234:5678::', 'compact'), '2001:…::');
  assert.equal(compactIpv6('::1234:abcd:5678:9abc', 'compact'), '::1234:…:9abc');
  const scoped = summarizeBindAddresses(['FE80:0000:0000:0000:ABCD:1234:5678:9ABC%Eth0']);
  assert.equal(scoped[0].primary, 'fe80::abcd:1234:5678:9abc%Eth0');
  assert.ok(scoped[0].display.endsWith(':9abc%Eth0'));
});

test('invalid addresses do not become card bind summaries', () => {
  assert.deepEqual(summarizeBindAddresses(['999.1.2.3', '1:2:3', '::not-an-ip', '1:2:3:4:5:6:7::8']), []);
});

test('card binds use observations or declarations, not reservation fallbacks', () => {
  const manual = { status: 'configured', source_type: 'manual', ips: ['0.0.0.0'] };
  assert.deepEqual(cardBindAddresses(manual), []);
  const compose = {
    status: 'configured', source_type: 'docker', ips: ['192.0.2.1'],
    compose_configs: [{ host_ip: '192.0.2.1' }, { host_ip: null }],
  };
  assert.deepEqual(summarizeBindAddresses(cardBindAddresses(compose))[0].addresses, ['0.0.0.0', '192.0.2.1']);
  assert.deepEqual(cardBindAddresses({ ...compose, ips: ['0.0.0.0'],
    compose_configs: [{ network_mode: 'host', host_ip: null }] }), []);
  assert.deepEqual(cardBindAddresses({ ...compose, ips: ['0.0.0.0'],
    compose_configs: [{ network_mode: 'ns:/proc/1/ns/net', host_ip: null }] }), []);
  assert.deepEqual(cardBindAddresses({ status: 'used', source_type: 'host', ips: ['127.0.0.1'] }), ['127.0.0.1']);
  assert.deepEqual(cardBindAddresses({ status: 'configured', source_type: 'docker',
    containers: [{ bind_ips: ['::'] }] }), ['::']);
});
