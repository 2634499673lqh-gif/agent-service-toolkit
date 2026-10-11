export type RunPollState = 'succeeded' | 'failed' | 'cancelled' | 'timeout' | 'stale';

export function pollTaskRun<T extends { status: string }>(options: {
  fetchRun: () => Promise<T>;
  isCurrent: () => boolean;
  onUpdate: (run: T) => void;
  maxPolls?: number;
  intervalMs?: number;
  wait?: (duration: number) => Promise<void>;
}): Promise<RunPollState>;
