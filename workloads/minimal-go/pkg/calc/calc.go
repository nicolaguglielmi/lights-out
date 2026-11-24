// SPDX-License-Identifier: Apache-2.0

// Package calc provides small pure numeric helpers with explicit branches
// for mutation testing (docs/adr/0102).
package calc

import "errors"

// ErrInvertedBounds indicates low > high in Clamp.
var ErrInvertedBounds = errors.New("low bound exceeds high bound")

// Add returns the sum of two integers.
func Add(a, b int) int {
	return a + b
}

// Clamp restricts value to [low, high]. The boundary inequalities (< and >)
// carry equivalent mutants when value == low or value == high.
func Clamp(value, low, high int) (int, error) {
	if low > high {
		return 0, ErrInvertedBounds
	}
	if value < low {
		return low, nil
	}
	if value > high {
		return high, nil
	}
	return value, nil
}

// IsEven reports whether n is evenly divisible by 2.
func IsEven(n int) bool {
	return n%2 == 0
}

// Sum returns the sum of values, or 0 for an empty slice.
func Sum(values []int) int {
	total := 0
	for _, v := range values {
		total += v
	}
	return total
}
