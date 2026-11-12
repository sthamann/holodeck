// Translate the versioned policy from the native Go AST.
// Common workspace operations are supplied by the existing typed reference graph.
package main

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"strconv"
)

type policy struct {
	Deepest bool   `json:"deepest"`
	Minimum int    `json:"minimum"`
	Inherit bool   `json:"inherit"`
	Refusal string `json:"refusal"`
}

func name(expr ast.Expr, expected string) bool {
	value, ok := expr.(*ast.Ident)
	return ok && value.Name == expected
}

func selectors(expr ast.Expr, object, field string) bool {
	value, ok := expr.(*ast.SelectorExpr)
	return ok && name(value.X, object) && value.Sel.Name == field
}

func main() {
	if len(os.Args) != 2 {
		panic("usage: go run source-policy.go PATH_TO_NATIVE_SERVICE_GO")
	}
	file, err := parser.ParseFile(token.NewFileSet(), os.Args[1], nil, 0)
	if err != nil {
		panic(err)
	}
	var result policy
	var sawLevel, sawMinimum bool
	for _, declaration := range file.Decls {
		f, ok := declaration.(*ast.FuncDecl)
		if !ok {
			continue
		}
		switch f.Name.Name {
		case "splitByHeadings":
			ast.Inspect(f.Body, func(node ast.Node) bool {
				condition, ok := node.(*ast.BinaryExpr)
				if ok && condition.Op != token.EQL && selectors(condition.X, "h", "level") && name(condition.Y, "minLevel") {
					if sawLevel || (condition.Op != token.LSS && condition.Op != token.GTR) {
						panic("unsupported depth rule")
					}
					sawLevel, result.Deepest = true, condition.Op == token.GTR
				}
				return true
			})
		case "Split":
			for _, statement := range f.Body.List {
				if loop, ok := statement.(*ast.RangeStmt); ok && selectors(loop.X, "existing", "Tags") {
					result.Inherit = true
				}
				branch, ok := statement.(*ast.IfStmt)
				if !ok {
					continue
				}
				condition, ok := branch.Cond.(*ast.BinaryExpr)
				if !ok || (condition.Op != token.EQL && condition.Op != token.LSS) {
					continue
				}
				length, ok := condition.X.(*ast.CallExpr)
				if !ok || !name(length.Fun, "len") || len(length.Args) != 1 || !name(length.Args[0], "sections") {
					continue
				}
				bound, ok := condition.Y.(*ast.BasicLit)
				if !ok || len(branch.Body.List) != 1 || sawMinimum {
					panic("unsupported minimum rule")
				}
				value, err := strconv.Atoi(bound.Value)
				if err != nil {
					panic(err)
				}
				if condition.Op == token.EQL {
					if value != 0 {
						panic("only a zero-count equality translates to a minimum")
					}
					value = 1
				}
				ret, ok := branch.Body.List[0].(*ast.ReturnStmt)
				if !ok || len(ret.Results) != 2 {
					panic("unsupported refusal")
				}
				call, ok := ret.Results[1].(*ast.CallExpr)
				if !ok || !name(call.Fun, "validationError") || len(call.Args) != 1 {
					panic("unsupported refusal")
				}
				message, ok := call.Args[0].(*ast.BasicLit)
				if !ok {
					panic("unsupported refusal message")
				}
				result.Refusal, err = strconv.Unquote(message.Value)
				if err != nil {
					panic(err)
				}
				result.Minimum, sawMinimum = value, true
			}
		}
	}
	if !sawLevel || !sawMinimum {
		panic("native policy did not match the supported source shape")
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		panic(err)
	}
}
