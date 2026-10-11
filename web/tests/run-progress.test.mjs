import test from 'node:test';
import assert from 'node:assert/strict';

import { pollTaskRun } from '../src/run-progress.js';

const noWait = async () => undefined;

test('polling stops at its bound and a new query continues', async () => {
  let reads = 0;
  const timedOut = await pollTaskRun({
    fetchRun: async () => {
      reads += 1;
      return { status: 'running' };
    },
    isCurrent: () => true,
    onUpdate: () => undefined,
    maxPolls: 3,
    wait: noWait,
  });

  assert.equal(timedOut, 'timeout');
  assert.equal(reads, 3);

  const continued = await pollTaskRun({
    fetchRun: async () => ({ status: 'succeeded' }),
    isCurrent: () => true,
    onUpdate: () => undefined,
    maxPolls: 3,
    wait: noWait,
  });
  assert.equal(continued, 'succeeded');
});

test('late API responses after a run switch are ignored', async () => {
  let releaseFetch;
  let enteredFetch;
  const fetchEntered = new Promise((resolve) => { enteredFetch = resolve; });
  const lateResponse = new Promise((resolve) => { releaseFetch = resolve; });
  let current = true;
  const updates = [];

  const polling = pollTaskRun({
    fetchRun: async () => {
      enteredFetch();
      return lateResponse;
    },
    isCurrent: () => current,
    onUpdate: (run) => updates.push(run),
    maxPolls: 1,
    wait: noWait,
  });

  await fetchEntered;
  current = false;
  releaseFetch({ status: 'succeeded', progress: { attempt_id: 'stale' } });

  assert.equal(await polling, 'stale');
  assert.deepEqual(updates, []);
});

test('terminal states stop polling immediately', async () => {
  let reads = 0;
  const result = await pollTaskRun({
    fetchRun: async () => {
      reads += 1;
      return { status: 'cancelled' };
    },
    isCurrent: () => true,
    onUpdate: () => undefined,
    maxPolls: 10,
    wait: noWait,
  });

  assert.equal(result, 'cancelled');
  assert.equal(reads, 1);
});
