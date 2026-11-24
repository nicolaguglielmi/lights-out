// SPDX-License-Identifier: Apache-2.0

// Command server serves the minimal-go HTTP endpoints (/ok and /).
package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"example.com/minimal-go/pkg/calc"
)

func route(path string) (int, string) {
	switch path {
	case "/ok":
		return http.StatusOK, "ok\n"
	case "/":
		return http.StatusOK, fmt.Sprintf("minimal-go sum=%d\n", calc.Add(1, 2))
	default:
		return http.StatusNotFound, "not found\n"
	}
}

func newMux() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		code, body := route(r.URL.Path)
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(code)
		_, _ = w.Write([]byte(body))
	})
	return mux
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8000"
	}
	srv := &http.Server{
		Addr:              ":" + port,
		Handler:           newMux(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Fatal(srv.ListenAndServe())
}
