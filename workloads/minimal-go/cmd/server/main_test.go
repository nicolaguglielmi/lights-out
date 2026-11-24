// SPDX-License-Identifier: Apache-2.0
package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRoutes(t *testing.T) {
	mux := newMux()

	for _, tc := range []struct {
		path string
		code int
		want string
	}{
		{"/ok", http.StatusOK, "ok\n"},
		{"/", http.StatusOK, "minimal-go sum=3\n"},
		{"/missing", http.StatusNotFound, "not found\n"},
	} {
		req := httptest.NewRequest(http.MethodGet, tc.path, nil)
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, req)
		if rec.Code != tc.code || rec.Body.String() != tc.want {
			t.Fatalf("GET %s = (%d, %q), want (%d, %q)", tc.path, rec.Code, rec.Body.String(), tc.code, tc.want)
		}
	}
}
