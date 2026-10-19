import assert from 'node:assert/strict';
import { resolveTestTimeoutMs, DEFAULT_TEST_TIMEOUT_MS, TestConfig } from '../../src/sandbox/runner';

// 1. Assert DEFAULT_TEST_TIMEOUT_MS is exactly 120000
assert.strictEqual(DEFAULT_TEST_TIMEOUT_MS, 120000);

// 2. Assert resolveTestTimeoutMs returns the input for valid positive finite numbers
assert.strictEqual(resolveTestTimeoutMs({ timeout: 1 }), 1);
assert.strictEqual(resolveTestTimeoutMs({ timeout: 5000 }), 5000);
assert.strictEqual(resolveTestTimeoutMs({ timeout: 300000 }), 300000);

// 3. Assert resolveTestTimeoutMs returns the default for invalid timeout properties
assert.strictEqual(resolveTestTimeoutMs({ timeout: NaN }), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs({ timeout: Infinity }), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs({ timeout: -Infinity }), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs({ timeout: 0 }), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs({ timeout: -1 }), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs({ timeout: '5000' as any }), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs({ timeout: true as any }), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs({ timeout: {} as any }), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs({ timeout: [] as any }), DEFAULT_TEST_TIMEOUT_MS);

// Assert resolveTestTimeoutMs returns the default for invalid config objects directly
assert.strictEqual(resolveTestTimeoutMs(null), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs(undefined), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs({}), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs({ timeout: undefined }), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs(NaN as any), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs(Infinity as any), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs(-Infinity as any), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs(0 as any), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs(-1 as any), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs('5000' as any), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs(true as any), DEFAULT_TEST_TIMEOUT_MS);
assert.strictEqual(resolveTestTimeoutMs([] as any), DEFAULT_TEST_TIMEOUT_MS);

// 4. Kill operator-inversion mutant: MUT_OP_3
// Original guard: if (config === null || config === undefined || typeof config !== 'object') return DEFAULT;
// Mutated guard: if (config === null || config === undefined && typeof config !== 'object') return DEFAULT;
//
// By passing a function with a valid timeout property:
// - Original checks `typeof config !== 'object'` (which evaluates to true because typeof is 'function').
//   It properly short-circuits and returns DEFAULT_TEST_TIMEOUT_MS.
// - Mutant evaluates `false || false && true`, which evaluates to false. It proceeds past the guard, 
//   reads the valid timeout of 5000, and returns 5000.
// This strict assertion ensures the mutant is killed while passing on the correct implementation.
const mutantKillerConfig: any = () => {};
mutantKillerConfig.timeout = 5000;
assert.strictEqual(
  resolveTestTimeoutMs(mutantKillerConfig),
  DEFAULT_TEST_TIMEOUT_MS,
  "A config of type 'function' must return the default timeout due to the typeof guard, even if it has a valid timeout property."
);

// 5. Assert an object literal typed as TestConfig both with and without timeout compiles
const baseConfig = {} as TestConfig;
const testConfigWithout: TestConfig = { ...baseConfig };
const testConfigWith: TestConfig = { ...baseConfig, timeout: 5000 };

assert.ok(testConfigWithout);
assert.ok(testConfigWith);