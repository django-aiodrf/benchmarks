package main

import (
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestFixtureContract(t *testing.T) {
	f := &fixture{}
	for _, test := range []struct {
		path   string
		status int
	}{
		{"/items/1", 200}, {"/items/3", 200}, {"/items/4", 404}, {"/items/1/extra", 404}, {"/health", 200},
	} {
		w := httptest.NewRecorder()
		f.ServeHTTP(w, httptest.NewRequest(http.MethodGet, test.path, nil))
		if w.Code != test.status {
			t.Fatalf("%s: %d", test.path, w.Code)
		}
	}
	w := httptest.NewRecorder()
	f.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/items/2", nil))
	var item map[string]int
	if err := json.Unmarshal(w.Body.Bytes(), &item); err != nil || item["id"] != 2 || item["value"] != 14 {
		t.Fatalf("unexpected body: %s", w.Body.String())
	}
}

func TestKeepAliveReusesConnection(t *testing.T) {
	f := &fixture{}
	server := httptest.NewUnstartedServer(f)
	server.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			f.connections.Add(1)
		}
	}
	server.Start()
	defer server.Close()
	client := server.Client()
	defer client.CloseIdleConnections()
	for i := 0; i < 3; i++ {
		response, err := client.Get(server.URL + "/items/1")
		if err != nil {
			t.Fatal(err)
		}
		_, err = io.Copy(io.Discard, response.Body)
		response.Body.Close()
		if err != nil || response.StatusCode != http.StatusOK {
			t.Fatalf("unexpected response: %d, %v", response.StatusCode, err)
		}
	}
	if f.requests.Load() != 3 || f.connections.Load() != 1 {
		t.Fatalf("requests=%d connections=%d", f.requests.Load(), f.connections.Load())
	}
}
