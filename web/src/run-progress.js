const terminalStates = new Set(['succeeded', 'failed', 'cancelled']);

export async function pollTaskRun({
  fetchRun,
  isCurrent,
  onUpdate,
  maxPolls = 240,
  intervalMs = 1000,
  wait = (duration) => new Promise((resolve) => setTimeout(resolve, duration)),
}) {
  for (let poll = 0; poll < maxPolls; poll += 1) {
    if (!isCurrent()) return 'stale';
    await wait(intervalMs);
    if (!isCurrent()) return 'stale';
    const run = await fetchRun();
    if (!isCurrent()) return 'stale';
    onUpdate(run);
    if (terminalStates.has(run.status)) return run.status;
  }
  return isCurrent() ? 'timeout' : 'stale';
}
