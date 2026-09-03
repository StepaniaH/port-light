/* Multi-host scale policy: layout choice, refresh guidance, and bounded sweeps. */

const BASELINE_PEER_CAPACITY = 6;
const REFRESH_HOST_CONCURRENCY = 3;
const DEFAULT_REFRESH_CHOICES = Object.freeze([
  5000, 10000, 15000, 30000, 60000, 120000, 300000,
]);

export function usesFocusedFleet(layout) {
  return layout === 'tabs';
}

export function refreshChoices(currentValue) {
  const current = Math.max(1000, Math.min(300000, Number(currentValue) || 5000));
  return Array.from(new Set(DEFAULT_REFRESH_CHOICES.concat([current]))).sort(function (a, b) {
    return a - b;
  });
}

export function recommendedPeerLimit(refreshMs, hardLimit) {
  const hard = Math.max(1, Number(hardLimit) || 32);
  const interval = Math.max(1000, Number(refreshMs) || 5000);
  // Six peers at the default five-second cadence is the existing supported load.
  return Math.min(hard, Math.max(1, Math.floor(interval * BASELINE_PEER_CAPACITY / 5000)));
}

export async function refreshFleet(items, worker) {
  const input = Array.from(items || []);
  const results = new Array(input.length);
  let next = 0;

  async function run() {
    while (next < input.length) {
      const index = next++;
      results[index] = await worker(input[index], index);
    }
  }

  const count = Math.min(REFRESH_HOST_CONCURRENCY, input.length);
  await Promise.all(Array.from({ length: count }, run));
  return results;
}
