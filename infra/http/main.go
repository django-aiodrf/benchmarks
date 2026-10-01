// The controlled HTTP dependency uses Go's net/http server and connection reuse.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math"
	"net"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"strconv"
	"sync/atomic"
	"syscall"
	"time"
)

type fixture struct {
	delay       time.Duration
	connections atomic.Uint64
	requests    atomic.Uint64
}

func (f *fixture) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", "GET")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.URL.Path == "/health" {
		json.NewEncoder(w).Encode(map[string]any{
			"status": "ok", "implementation": "go-net-http", "payload_schema": 1,
			"go_version":           runtime.Version(),
			"delay_ms":             float64(f.delay) / float64(time.Millisecond),
			"connections_accepted": f.connections.Load(), "item_requests": f.requests.Load(),
		})
		return
	}
	var id int
	if _, err := fmt.Sscanf(r.URL.Path, "/items/%d", &id); err != nil || id < 1 || id > 3 || r.URL.Path != fmt.Sprintf("/items/%d", id) {
		http.NotFound(w, r)
		return
	}
	f.requests.Add(1)
	timer := time.NewTimer(f.delay)
	defer timer.Stop()
	select {
	case <-r.Context().Done():
		return
	case <-timer.C:
	}
	json.NewEncoder(w).Encode(map[string]int{"id": id, "value": id * 7})
}

func main() {
	health := flag.Bool("healthcheck", false, "check the local service and exit")
	flag.Parse()
	if *health {
		client := http.Client{Timeout: 2 * time.Second}
		response, err := client.Get("http://127.0.0.1:8090/health")
		if err != nil {
			log.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != 200 {
			os.Exit(1)
		}
		return
	}
	delay := 10.0
	if value := os.Getenv("BENCH_HTTP_DELAY_MS"); value != "" {
		var err error
		delay, err = strconv.ParseFloat(value, 64)
		if err != nil || math.IsNaN(delay) || math.IsInf(delay, 0) || delay < 0 || delay > 10000 {
			log.Fatal("Invalid BENCH_HTTP_DELAY_MS")
		}
	}
	f := &fixture{delay: time.Duration(delay * float64(time.Millisecond))}
	server := &http.Server{
		Addr: ":8090", Handler: f, ReadHeaderTimeout: 5 * time.Second,
		WriteTimeout: 15 * time.Second, IdleTimeout: 60 * time.Second,
		ConnState: func(_ net.Conn, state http.ConnState) {
			if state == http.StateNew {
				f.connections.Add(1)
			}
		},
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		server.Shutdown(shutdown)
	}()
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
