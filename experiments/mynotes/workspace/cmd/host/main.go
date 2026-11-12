// The host serves static assets, sends requests to a guest and provides SQLite.
package main

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/tetratelabs/wazero"
	"github.com/tetratelabs/wazero/api"
	"github.com/tetratelabs/wazero/imports/wasi_snapshot_preview1"
	"holodeck.local/workspace/wire"
	_ "modernc.org/sqlite"
)

const maxMessage = 32 << 20

type sqlPort struct {
	connection *sql.Conn
	reply      []byte
}

func errorReply(err error) wire.SQLResponse {
	var coded interface{ Code() int }
	var code int
	if errors.As(err, &coded) {
		code = coded.Code()
	}
	return wire.SQLResponse{Error: err.Error(), Code: code}
}

func (p *sqlPort) run(ctx context.Context, request wire.SQLRequest) (wire.SQLResponse, error) {
	args := make([]any, len(request.Args))
	for i, v := range request.Args {
		decoded, err := v.Decode()
		if err != nil {
			return wire.SQLResponse{}, err
		}
		args[i] = decoded
	}
	if !request.Query {
		res, err := p.connection.ExecContext(ctx, request.Text, args...)
		if err != nil {
			return wire.SQLResponse{}, err
		}
		inserted, _ := res.LastInsertId()
		affected, _ := res.RowsAffected()
		return wire.SQLResponse{Inserted: inserted, Affected: affected}, nil
	}
	rows, err := p.connection.QueryContext(ctx, request.Text, args...)
	if err != nil {
		return wire.SQLResponse{}, err
	}
	defer rows.Close()
	columns, err := rows.Columns()
	if err != nil {
		return wire.SQLResponse{}, err
	}
	response := wire.SQLResponse{Columns: columns}
	values := make([]any, len(columns))
	addresses := make([]any, len(columns))
	for i := range values {
		addresses[i] = &values[i]
	}
	for rows.Next() {
		if len(response.Rows) >= 50_000 {
			return wire.SQLResponse{}, errors.New("SQL result exceeds row limit")
		}
		if err := rows.Scan(addresses...); err != nil {
			return wire.SQLResponse{}, err
		}
		row := make([]wire.Value, len(values))
		for i, value := range values {
			row[i], err = wire.Encode(value)
			if err != nil {
				return wire.SQLResponse{}, err
			}
		}
		response.Rows = append(response.Rows, row)
	}
	return response, rows.Err()
}

func (p *sqlPort) dispatch(ctx context.Context, module api.Module, offset, size uint32) uint32 {
	payload, ok := module.Memory().Read(offset, size)
	if !ok || size > maxMessage {
		return 0
	}
	var request wire.SQLRequest
	err := json.Unmarshal(payload, &request)
	var result wire.SQLResponse
	if err == nil {
		result, err = p.run(ctx, request)
	}
	if err != nil {
		result = errorReply(err)
	}
	p.reply, err = json.Marshal(result)
	if err != nil || len(p.reply) > maxMessage {
		p.reply = nil
		return 0
	}
	return uint32(len(p.reply))
}

func (p *sqlPort) read(_ context.Context, module api.Module, offset, size uint32) uint32 {
	if uint32(len(p.reply)) != size || !module.Memory().Write(offset, p.reply) {
		return 0
	}
	p.reply = nil
	return 1
}

type guest struct {
	mu         sync.Mutex
	module     api.Module
	connection *sql.Conn
}

func (g *guest) call(ctx context.Context, function string, args ...uint64) (uint32, error) {
	f := g.module.ExportedFunction(function)
	if f == nil {
		return 0, fmt.Errorf("missing guest export %q", function)
	}
	result, err := f.Call(ctx, args...)
	if err != nil {
		return 0, err
	}
	if len(result) != 1 {
		return 0, fmt.Errorf("invalid result from %q", function)
	}
	return uint32(result[0]), nil
}

func (g *guest) dispatch(ctx context.Context, request wire.Request) (wire.Response, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	response, err := g.exchange(ctx, request)
	if err != nil {
		// A trapped guest cannot leave an incomplete SQL transaction open.
		_, _ = g.connection.ExecContext(context.Background(), "ROLLBACK")
	}
	return response, err
}

func (g *guest) exchange(ctx context.Context, request wire.Request) (wire.Response, error) {
	var response wire.Response
	payload, err := json.Marshal(request)
	if err != nil {
		return response, err
	}
	offset, err := g.call(ctx, "reserve", uint64(len(payload)))
	if err != nil {
		return response, err
	}
	if !g.module.Memory().Write(offset, payload) {
		return response, errors.New("guest input out of bounds")
	}
	size, err := g.call(ctx, "dispatch")
	if err != nil {
		return response, err
	}
	if size == 0 || size > maxMessage {
		return response, errors.New("guest result exceeds limit")
	}
	offset, err = g.call(ctx, "response")
	if err != nil {
		return response, err
	}
	data, ok := g.module.Memory().Read(offset, size)
	if !ok {
		return response, errors.New("guest result out of bounds")
	}
	err = json.Unmarshal(data, &response)
	return response, err
}

func handler(g *guest, assets, bind string) http.Handler {
	files := http.FileServer(http.Dir(assets))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != bind {
			http.Error(w, "host must match local bind", http.StatusBadRequest)
			return
		}
		if strings.HasPrefix(r.URL.Path, "/api/v1/") {
			if origin := r.Header.Get("Origin"); origin != "" && origin != "http://"+bind {
				http.Error(w, "invalid origin", http.StatusForbidden)
				return
			}
			body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 4<<20))
			if err != nil {
				http.Error(w, "request exceeds budget", http.StatusRequestEntityTooLarge)
				return
			}
			response, err := g.dispatch(r.Context(), wire.Request{Method: r.Method, URL: r.URL.RequestURI(), Header: r.Header, Body: body})
			if err != nil {
				log.Print(err)
				http.Error(w, "guest execution failed", http.StatusBadGateway)
				return
			}
			for key, values := range response.Header {
				for _, value := range values {
					w.Header().Add(key, value)
				}
			}
			w.WriteHeader(response.Status)
			_, _ = w.Write(response.Body)
			return
		}
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			http.NotFound(w, r)
			return
		}
		if filepath.Ext(r.URL.Path) == "" {
			http.ServeFile(w, r, filepath.Join(assets, "index.html"))
			return
		}
		files.ServeHTTP(w, r)
	})
}

func run() error {
	ctx := context.Background()
	modulePath := flag.String("module", "", "WASI application module")
	dataPath := flag.String("db", "", "local SQLite path")
	assets := flag.String("assets", "", "read-only static UI assets")
	port := flag.Uint("port", 55767, "loopback port")
	flag.Parse()
	if *modulePath == "" || *dataPath == "" || *assets == "" || *port == 0 || *port > 65535 {
		return errors.New("usage: host -module GUEST -db SQLITE -assets STATIC [-port PORT]")
	}
	binary, err := os.ReadFile(*modulePath)
	if err != nil {
		return err
	}
	db, err := sql.Open("sqlite", *dataPath)
	if err != nil {
		return err
	}
	defer db.Close()
	connection, err := db.Conn(ctx)
	if err != nil {
		return err
	}
	defer connection.Close()
	for _, pragma := range []string{"PRAGMA foreign_keys=ON", "PRAGMA busy_timeout=5000", "PRAGMA journal_mode=WAL"} {
		if _, err := connection.ExecContext(ctx, pragma); err != nil {
			return err
		}
	}
	rt := wazero.NewRuntimeWithConfig(ctx, wazero.NewRuntimeConfig().WithMemoryLimitPages(8192))
	defer rt.Close(ctx)
	if _, err := wasi_snapshot_preview1.Instantiate(ctx, rt); err != nil {
		return err
	}
	portBridge := sqlPort{connection: connection}
	_, err = rt.NewHostModuleBuilder("deck").
		NewFunctionBuilder().WithFunc(portBridge.dispatch).Export("sql").
		NewFunctionBuilder().WithFunc(portBridge.read).Export("sql_result").
		Instantiate(ctx)
	if err != nil {
		return err
	}
	module, err := rt.InstantiateWithConfig(ctx, binary, wazero.NewModuleConfig().
		WithStartFunctions("_initialize").WithSysWalltime().WithSysNanotime().WithSysNanosleep().
		WithRandSource(rand.Reader).WithStderr(os.Stderr))
	if err != nil {
		return err
	}
	g := &guest{module: module, connection: connection}
	if _, err := g.call(ctx, "setup"); err != nil {
		return err
	}
	bind := fmt.Sprintf("127.0.0.1:%d", *port)
	log.Printf("workspace host listening at http://%s", bind)
	server := http.Server{Addr: bind, Handler: handler(g, *assets, bind), ReadHeaderTimeout: 5 * time.Second}
	return server.ListenAndServe()
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
