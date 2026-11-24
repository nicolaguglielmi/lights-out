// SPDX-License-Identifier: Apache-2.0
//
// Typed calculator module with branches worth mutating (docs/adr/0102),
// executed natively by Node 24's built-in type stripping and test runner.

export function add(a: number, b: number): number {
  return a + b;
}

// Equivalent boundary mutants (`<` to `<=` at `low`, `>` to `>=` at `high`)
// cannot change any return value and are captured in the measured baseline.
export function clamp(value: number, low: number, high: number): number {
  if (low > high) {
    throw new RangeError(`low ${low} is above high ${high}`);
  }
  if (value < low) {
    return low;
  }
  if (value > high) {
    return high;
  }
  return value;
}

export function isEven(n: number): boolean {
  return n % 2 === 0;
}

export function mean(values: readonly number[]): number | null {
  if (values.length === 0) {
    return null;
  }
  let total = 0;
  for (const v of values) {
    total += v;
  }
  return total / values.length;
}
