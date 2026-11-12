// Package sqlite preserves database/sql semantics over the guest SQL port.
package sqlite

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"encoding/json"
	"errors"
	"io"
	"runtime"
	"unsafe"

	"modernc.org/sqlite/internal/wire"
)

const maxReply = 32 << 20

//go:wasmimport deck sql
func hostSQL(ptr uint32, size uint32) uint32

//go:wasmimport deck sql_result
func hostSQLResult(ptr uint32, size uint32) uint32

type Error struct {
	message string
	code    int
}

func (e *Error) Error() string { return e.message }
func (e *Error) Code() int     { return e.code }

func address(data []byte) uint32 { return uint32(uintptr(unsafe.Pointer(&data[0]))) }

func invoke(query bool, text string, arguments []driver.NamedValue) (wire.SQLResponse, error) {
	request := wire.SQLRequest{Query: query, Text: text}
	for _, argument := range arguments {
		if argument.Name != "" {
			return wire.SQLResponse{}, errors.New("named SQL parameters unsupported")
		}
		v, err := wire.Encode(argument.Value)
		if err != nil {
			return wire.SQLResponse{}, err
		}
		request.Args = append(request.Args, v)
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return wire.SQLResponse{}, err
	}
	length := hostSQL(address(payload), uint32(len(payload)))
	runtime.KeepAlive(payload)
	if length == 0 || length > maxReply {
		return wire.SQLResponse{}, errors.New("SQL port reply exceeds limit")
	}
	reply := make([]byte, length)
	if hostSQLResult(address(reply), length) != 1 {
		return wire.SQLResponse{}, errors.New("SQL port response unavailable")
	}
	var result wire.SQLResponse
	if err := json.Unmarshal(reply, &result); err != nil {
		return result, err
	}
	if result.Error != "" {
		return result, &Error{result.Error, result.Code}
	}
	return result, nil
}

type sqliteDriver struct{}
type connection struct{}
type transaction struct{}
type result struct{ inserted, affected int64 }
type rows struct {
	cols   []string
	values [][]wire.Value
	cursor int
}

func init()                                           { sql.Register("sqlite", sqliteDriver{}) }
func (sqliteDriver) Open(string) (driver.Conn, error) { return connection{}, nil }
func (connection) Close() error                       { return nil }
func (connection) Prepare(string) (driver.Stmt, error) {
	return nil, errors.New("SQL port requires direct exec/query")
}
func (connection) Begin() (driver.Tx, error) {
	_, err := invoke(false, "BEGIN", nil)
	if err != nil {
		return nil, err
	}
	return transaction{}, nil
}
func (transaction) Commit() error   { _, err := invoke(false, "COMMIT", nil); return err }
func (transaction) Rollback() error { _, err := invoke(false, "ROLLBACK", nil); return err }
func (connection) ExecContext(_ context.Context, query string, args []driver.NamedValue) (driver.Result, error) {
	data, err := invoke(false, query, args)
	if err != nil {
		return nil, err
	}
	return result{data.Inserted, data.Affected}, nil
}
func (connection) QueryContext(_ context.Context, query string, args []driver.NamedValue) (driver.Rows, error) {
	data, err := invoke(true, query, args)
	if err != nil {
		return nil, err
	}
	return &rows{cols: data.Columns, values: data.Rows}, nil
}
func (r result) LastInsertId() (int64, error) { return r.inserted, nil }
func (r result) RowsAffected() (int64, error) { return r.affected, nil }
func (r *rows) Columns() []string             { return r.cols }
func (r *rows) Close() error                  { return nil }
func (r *rows) Next(dest []driver.Value) error {
	if r.cursor == len(r.values) {
		return io.EOF
	}
	for i, raw := range r.values[r.cursor] {
		v, err := raw.Decode()
		if err != nil {
			return err
		}
		dest[i] = v
	}
	r.cursor++
	return nil
}
