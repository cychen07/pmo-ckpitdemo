/// <reference types="node" />

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const cssPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'app.css');

function matchIndex(text: string, pattern: RegExp): number {
  const match = pattern.exec(text);
  return match?.index ?? -1;
}

describe('stale-dot reduced motion styles', () => {
  it('disables the pulse animation when prefers-reduced-motion is enabled', () => {
    const css = readFileSync(cssPath, 'utf8');

    const baseAnimationIndex = css.indexOf('animation: stale-pulse 1.8s ease-in-out infinite;');
    const reducedMotionIndex = matchIndex(
      css,
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\.stale-dot\s*\{[\s\S]*?animation:\s*none;/,
    );

    expect(baseAnimationIndex).toBeGreaterThanOrEqual(0);
    expect(reducedMotionIndex).toBeGreaterThan(baseAnimationIndex);
  });
});
