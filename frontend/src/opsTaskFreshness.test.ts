import { describe, expect, it } from 'vitest';

import { isStaleSync, renderSyncAge, STALE_SYNC_THRESHOLD_MS } from './opsTaskFreshness';

describe('ops task freshness helpers', () => {
  const now = Date.parse('2026-05-14T12:00:00.000Z');

  it('renders readable sync ages for fresh values', () => {
    expect(renderSyncAge(new Date(now - 2000).toISOString(), now)).toBe('刚刚同步');
    expect(renderSyncAge(new Date(now - 12_000).toISOString(), now)).toBe('12 秒前');
    expect(renderSyncAge(new Date(now - 3 * 60_000).toISOString(), now)).toBe('3 分钟前');
  });

  it('marks missing or five-minute-old sync timestamps as stale', () => {
    expect(isStaleSync(null, now)).toBe(true);
    expect(isStaleSync(new Date(now - STALE_SYNC_THRESHOLD_MS + 1).toISOString(), now)).toBe(false);
    expect(isStaleSync(new Date(now - STALE_SYNC_THRESHOLD_MS).toISOString(), now)).toBe(true);
  });
});

