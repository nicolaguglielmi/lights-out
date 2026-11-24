// SPDX-License-Identifier: Apache-2.0
package calc

import (
	"errors"
	"testing"
)

func TestAdd(t *testing.T) {
	if got := Add(2, 3); got != 5 {
		t.Fatalf("Add(2, 3) = %d, want 5", got)
	}
	if got := Add(-1, 1); got != 0 {
		t.Fatalf("Add(-1, 1) = %d, want 0", got)
	}
	if got := Add(2, -3); got != -1 {
		t.Fatalf("Add(2, -3) = %d, want -1", got)
	}
}

func TestClamp(t *testing.T) {
	cases := []struct {
		v, lo, hi int
		want      int
	}{
		{5, 1, 10, 5},
		{0, 1, 10, 1},
		{11, 1, 10, 10},
		{1, 1, 10, 1},
		{10, 1, 10, 10},
		{5, 5, 5, 5},
	}
	for _, tc := range cases {
		got, err := Clamp(tc.v, tc.lo, tc.hi)
		if err != nil || got != tc.want {
			t.Fatalf("Clamp(%d, %d, %d) = (%d, %v), want (%d, nil)", tc.v, tc.lo, tc.hi, got, err, tc.want)
		}
	}
	if _, err := Clamp(5, 10, 1); !errors.Is(err, ErrInvertedBounds) {
		t.Fatalf("Clamp(5, 10, 1) err = %v, want ErrInvertedBounds", err)
	}
}

func TestIsEven(t *testing.T) {
	if !IsEven(0) || !IsEven(2) || !IsEven(-4) {
		t.Fatal("expected even numbers to report true")
	}
	if IsEven(3) || IsEven(-3) {
		t.Fatal("expected odd numbers to report false")
	}
}

func TestSum(t *testing.T) {
	if got := Sum(nil); got != 0 {
		t.Fatalf("Sum(nil) = %d, want 0", got)
	}
	if got := Sum([]int{1, 2, 3, -1}); got != 5 {
		t.Fatalf("Sum([1,2,3,-1]) = %d, want 5", got)
	}
}
