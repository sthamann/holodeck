// Package wire defines the value-preserving transport for the guest's SQL port.
package wire

import (
	"database/sql/driver"
	"fmt"
	"time"
)

type Value struct {
	Type string  `json:"type"`
	Int  int64   `json:"int,omitempty"`
	Text string  `json:"text,omitempty"`
	Real float64 `json:"real,omitempty"`
	Blob []byte  `json:"blob,omitempty"`
}

func Encode(value driver.Value) (Value, error) {
	switch v := value.(type) {
	case nil:
		return Value{Type: "null"}, nil
	case int64:
		return Value{Type: "int", Int: v}, nil
	case string:
		return Value{Type: "text", Text: v}, nil
	case []byte:
		return Value{Type: "blob", Blob: v}, nil
	case float64:
		return Value{Type: "real", Real: v}, nil
	case bool:
		if v {
			return Value{Type: "int", Int: 1}, nil
		}
		return Value{Type: "int"}, nil
	case time.Time:
		return Value{Type: "text", Text: v.Format(time.RFC3339Nano)}, nil
	default:
		return Value{}, fmt.Errorf("unsupported SQL value %T", value)
	}
}

func (v Value) Decode() (driver.Value, error) {
	switch v.Type {
	case "null":
		return nil, nil
	case "int":
		return v.Int, nil
	case "text":
		return v.Text, nil
	case "blob":
		if v.Blob == nil {
			return []byte{}, nil
		}
		return v.Blob, nil
	case "real":
		return v.Real, nil
	default:
		return nil, fmt.Errorf("unknown SQL value type %q", v.Type)
	}
}

type SQLRequest struct {
	Query bool    `json:"query"`
	Text  string  `json:"text"`
	Args  []Value `json:"args"`
}

type SQLResponse struct {
	Error    string    `json:"error,omitempty"`
	Code     int       `json:"code,omitempty"`
	Columns  []string  `json:"columns,omitempty"`
	Rows     [][]Value `json:"rows,omitempty"`
	Inserted int64     `json:"inserted,omitempty"`
	Affected int64     `json:"affected,omitempty"`
}

type Request struct {
	Method string              `json:"method"`
	URL    string              `json:"url"`
	Header map[string][]string `json:"header"`
	Body   []byte              `json:"body"`
}

type Response struct {
	Status int                 `json:"status"`
	Header map[string][]string `json:"header"`
	Body   []byte              `json:"body"`
}
