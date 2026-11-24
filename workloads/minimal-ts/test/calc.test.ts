// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { test } from "node:test";

import { add, clamp, isEven, mean } from "../src/calc.ts";

test("add sums two numbers", () => {
  assert.equal(add(2, 3), 5);
  assert.equal(add(-1, 1), 0);
  assert.equal(add(2, -3), -1);
});

test("clamp keeps a value inside its bounds", () => {
  assert.equal(clamp(5, 1, 10), 5);
  assert.equal(clamp(0, 1, 10), 1);
  assert.equal(clamp(11, 1, 10), 10);
  assert.equal(clamp(1, 1, 10), 1);
  assert.equal(clamp(10, 1, 10), 10);
});

test("clamp refuses inverted bounds and says which", () => {
  assert.throws(() => clamp(5, 10, 1), { name: "RangeError", message: "low 10 is above high 1" });
  assert.doesNotThrow(() => clamp(5, 5, 5));
});

test("isEven tells even from odd", () => {
  assert.equal(isEven(0), true);
  assert.equal(isEven(2), true);
  assert.equal(isEven(3), false);
  assert.equal(isEven(-4), true);
  assert.equal(isEven(-3), false);
});

test("mean averages, and is null on nothing", () => {
  assert.equal(mean([]), null);
  assert.equal(mean([4]), 4);
  assert.equal(mean([1, 2, 3, 4]), 2.5);
  assert.equal(mean([-2, 2]), 0);
});
