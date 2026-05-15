export const STALE_SYNC_THRESHOLD_MS = 5 * 60 * 1000;

export function renderSyncAge(value?: string | null, nowMs = Date.now()): string {
  if (!value) {
    return '未同步';
  }
  const deltaSec = Math.max(0, Math.floor((nowMs - new Date(value).getTime()) / 1000));
  if (deltaSec < 5) return '刚刚同步';
  if (deltaSec < 60) return `${deltaSec} 秒前`;
  const deltaMin = Math.floor(deltaSec / 60);
  if (deltaMin < 60) return `${deltaMin} 分钟前`;
  const deltaHour = Math.floor(deltaMin / 60);
  return `${deltaHour} 小时前`;
}

export function isStaleSync(value?: string | null, nowMs = Date.now()): boolean {
  if (!value) {
    return true;
  }
  return nowMs - new Date(value).getTime() >= STALE_SYNC_THRESHOLD_MS;
}

