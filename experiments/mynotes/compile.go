// Compile the source slice for a validated, title-only, conditional update
// of an existing MyNotes record. The general Go frontend remains open.
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
)

const inputs = 9
const operations = 6
const cells = 14
const lanes = inputs + cells

type Gate struct {
	Operation [operations]uint8 `json:"operation"`
	Left      [lanes]uint8      `json:"left"`
	Right     [lanes]uint8      `json:"right"`
	Alternate [lanes]uint8      `json:"alternate"`
}

type Model struct {
	Gates   []Gate         `json:"gates"`
	Outputs [][lanes]uint8 `json:"outputs"`
}

type builder struct{ model Model }

func selectLane(slot int) (out [lanes]uint8) {
	out[slot] = 1
	return
}

func (b *builder) gate(op, left, right, alt int) int {
	gate := Gate{Left: selectLane(left), Right: selectLane(right), Alternate: selectLane(alt)}
	gate.Operation[op] = 1
	slot := inputs + len(b.model.Gates)
	b.model.Gates = append(b.model.Gates, gate)
	return slot
}

func function(root, path, name string) *ast.FuncDecl {
	return parseFunction(token.NewFileSet(), filepath.Join(root, path), name)
}

func parseFunction(files *token.FileSet, path, name string) *ast.FuncDecl {
	file, err := parser.ParseFile(files, path, nil, 0)
	if err != nil {
		panic(err)
	}
	for _, decl := range file.Decls {
		if f, ok := decl.(*ast.FuncDecl); ok && f.Name.Name == name {
			return f
		}
	}
	panic("function not found: " + name)
}

func ident(expr ast.Expr, value string) bool {
	id, ok := expr.(*ast.Ident)
	return ok && id.Name == value
}

func selector(expr ast.Expr, receiver, name string) bool {
	sel, ok := expr.(*ast.SelectorExpr)
	return ok && ident(sel.X, receiver) && sel.Sel.Name == name
}

func compare(expr ast.Expr, op token.Token, left, right string) bool {
	b, ok := expr.(*ast.BinaryExpr)
	return ok && b.Op == op && ident(b.X, left) && ident(b.Y, right)
}

func findIf(f *ast.FuncDecl, matches func(*ast.IfStmt) bool) *ast.IfStmt {
	var match *ast.IfStmt
	ast.Inspect(f.Body, func(node ast.Node) bool {
		if stmt, ok := node.(*ast.IfStmt); ok && matches(stmt) {
			if match != nil {
				panic("ambiguous source patterns")
			}
			match = stmt
		}
		return true
	})
	if match == nil {
		panic("unsupported source shape in " + f.Name.Name)
	}
	return match
}

func returnPair(body *ast.BlockStmt) (*ast.ReturnStmt, bool) {
	if len(body.List) != 1 {
		return nil, false
	}
	ret, ok := body.List[0].(*ast.ReturnStmt)
	return ret, ok && len(ret.Results) == 2
}

func serviceGuards(f *ast.FuncDecl) {
	// Match the conditional API guard, the title diff and the no-op branch
	// under the declared profile where content and tags are absent.
	findIf(f, func(s *ast.IfStmt) bool {
		b, ok := s.Cond.(*ast.BinaryExpr)
		ret, returns := returnPair(s.Body)
		if !ok || b.Op != token.NEQ || !returns {
			return false
		}
		star, ptr := b.X.(*ast.StarExpr)
		return ptr && ident(star.X, "ifMatch") && ident(b.Y, "expectedETag") && ident(ret.Results[1], "ErrVersionMismatch")
	})
	findIf(f, func(s *ast.IfStmt) bool {
		b, ok := s.Cond.(*ast.BinaryExpr)
		if !ok || b.Op != token.LAND || !compare(b.X, token.NEQ, "title", "nil") {
			return false
		}
		diff, ok := b.Y.(*ast.BinaryExpr)
		if !ok || diff.Op != token.NEQ || !selector(diff.Y, "existing", "Title") || len(s.Body.List) != 1 {
			return false
		}
		star, ptr := diff.X.(*ast.StarExpr)
		assign, assigned := s.Body.List[0].(*ast.AssignStmt)
		return ptr && ident(star.X, "title") && assigned && len(assign.Lhs) == 1 &&
			len(assign.Rhs) == 1 && ident(assign.Lhs[0], "changedTitle") && ident(assign.Rhs[0], "title")
	})
	findIf(f, func(s *ast.IfStmt) bool {
		outer, ok := s.Cond.(*ast.BinaryExpr)
		ret, returns := returnPair(s.Body)
		if !ok || outer.Op != token.LAND || !returns {
			return false
		}
		first, ok := outer.X.(*ast.BinaryExpr)
		return ok && first.Op == token.LAND && compare(first.X, token.EQL, "changedTitle", "nil") &&
			compare(first.Y, token.EQL, "changedContent", "nil") && compare(outer.Y, token.EQL, "changedTagIDs", "nil") &&
			ident(ret.Results[0], "existing") && ident(ret.Results[1], "nil")
	})
}

func repositoryGuard(f *ast.FuncDecl) *ast.IfStmt {
	return findIf(f, func(s *ast.IfStmt) bool {
		both, ok := s.Cond.(*ast.BinaryExpr)
		ret, returns := returnPair(s.Body)
		if !ok || both.Op != token.LAND || !returns ||
			!compare(both.X, token.NEQ, "expected", "nil") || !ident(ret.Results[1], "ErrVersionMismatch") {
			return false
		}
		not, ok := both.Y.(*ast.UnaryExpr)
		if !ok || not.Op != token.NOT {
			return false
		}
		call, ok := not.X.(*ast.CallExpr)
		if !ok || len(call.Args) != 1 || !selector(call.Fun, "stored", "Equal") {
			return false
		}
		arg, ok := call.Args[0].(*ast.StarExpr)
		return ok && ident(arg.X, "expected")
	})
}

func clockGuard(f *ast.FuncDecl) (bool, *ast.IfStmt) {
	// Support the original guard and a deliberate mutation with a distinct
	// observable equality case. Both map into the same operator bank.
	var incrementAtEquality bool
	stmt := findIf(f, func(s *ast.IfStmt) bool {
		cond := s.Cond
		neg, negated := cond.(*ast.UnaryExpr)
		if negated {
			if neg.Op != token.NOT {
				return false
			}
			cond = neg.X
		}
		call, ok := cond.(*ast.CallExpr)
		if !ok || len(call.Args) != 1 || !ident(call.Args[0], "stored") {
			return false
		}
		if !(negated && selector(call.Fun, "now", "After") || !negated && selector(call.Fun, "now", "Before")) {
			return false
		}
		if len(s.Body.List) != 1 {
			return false
		}
		set, ok := s.Body.List[0].(*ast.AssignStmt)
		if !ok || len(set.Lhs) != 1 || len(set.Rhs) != 1 || !ident(set.Lhs[0], "now") {
			return false
		}
		add, ok := set.Rhs[0].(*ast.CallExpr)
		if !ok || len(add.Args) != 1 || !selector(add.Fun, "stored", "Add") || !selector(add.Args[0], "time", "Millisecond") {
			return false
		}
		incrementAtEquality = negated
		return true
	})
	return incrementAtEquality, stmt
}

func compile(root string) Model {
	serviceGuards(function(root, "internal/service/service.go", "Update"))
	repository := function(root, "internal/repository/note_repo.go", "update")
	repositoryGuard(repository)
	incrementAtEquality, _ := clockGuard(repository)
	// Inputs: existing title ID, proposed title ID, read revision,
	// If-Match revision, clock, commit-time revision, 0, 1, 412.
	const eq, add, less, le, choose, copy = 0, 1, 2, 3, 4, 5
	b := builder{}
	etag := b.gate(eq, 2, 3, 6)
	unchanged := b.gate(eq, 0, 1, 6)
	stillCurrent := b.gate(eq, 2, 5, 6)
	needsTick := less
	if incrementAtEquality {
		needsTick = le
	}
	clockBehind := b.gate(needsTick, 4, 5, 6)
	advanced := b.gate(add, 5, 7, 6)
	newTime := b.gate(choose, clockBehind, advanced, 4)
	currentWrite := b.gate(choose, stillCurrent, 7, 6)
	changedWrite := b.gate(choose, unchanged, 6, currentWrite)
	write := b.gate(choose, etag, changedWrite, 6)
	staleCommit := b.gate(choose, stillCurrent, 6, 8)
	changedStatus := b.gate(choose, unchanged, 6, staleCommit)
	status := b.gate(choose, etag, changedStatus, 8)
	version := b.gate(choose, write, newTime, 2)
	title := b.gate(choose, write, 1, 0)
	b.model.Outputs = [][lanes]uint8{selectLane(status), selectLane(write), selectLane(version), selectLane(title)}
	for len(b.model.Gates) < cells {
		b.gate(copy, 6, 6, 6)
	}
	return b.model
}

func rewriteRepository(root string) {
	path := filepath.Join(root, "internal/repository/note_repo.go")
	files := token.NewFileSet()
	repository := parseFunction(files, path, "update")
	start := repositoryGuard(repository)
	_, end := clockGuard(repository)
	verified := false
	for i, stmt := range repository.Body.List {
		if stmt != start || i+2 >= len(repository.Body.List) || repository.Body.List[i+2] != end {
			continue
		}
		clock, ok := repository.Body.List[i+1].(*ast.AssignStmt)
		if ok && clock.Tok == token.DEFINE && len(clock.Lhs) == 1 && len(clock.Rhs) == 1 &&
			ident(clock.Lhs[0], "now") {
			verified = true
		}
	}
	if !verified {
		panic("unsupported repository revision block")
	}
	source, err := os.ReadFile(path)
	if err != nil {
		panic(err)
	}
	from, to := files.Position(start.Pos()).Offset, files.Position(end.End()).Offset
	const replacement = `now, err := compiledRevision(stored, expected, time.Now().UTC().Truncate(time.Millisecond))
	if err != nil {
		return model.Note{}, err
	}`
	rewritten := string(source[:from]) + replacement + string(source[to:])
	if err := os.WriteFile(path, []byte(rewritten), 0644); err != nil {
		panic(err)
	}
}

func main() {
	if len(os.Args) != 2 && (len(os.Args) != 3 || os.Args[2] != "--bind-repository") {
		fmt.Fprintln(os.Stderr, "usage: compile MYNOTES_SOURCE [--bind-repository]")
		os.Exit(2)
	}
	if err := json.NewEncoder(os.Stdout).Encode(compile(os.Args[1])); err != nil {
		panic(err)
	}
	if len(os.Args) == 3 {
		rewriteRepository(os.Args[1])
	}
}
