#!/usr/bin/env bash
# Generate an isolated compiler workspace from a pinned, built MyNotes tree.
set -euo pipefail
source_dir=$(cd "${1:?usage: build.sh MYNOTES_SOURCE NEW_OUTPUT_DIR}" && pwd)
out=${2:?new output directory required}
profile=${3:-source}
case "$profile" in source|weights) ;; *) echo "expected source or weights" >&2; exit 2;; esac
root=$(cd "$(dirname "$0")" && pwd)
pin=73ef4b7b37109a5209d9fbcfbd5d3799918071be
test "$(git -C "$source_dir" rev-parse HEAD)" = "$pin"
git -C "$source_dir" diff --quiet HEAD --
test -f "$source_dir/internal/api/oas_server_gen.go"
test -f "$source_dir/web/static/app.js"
mkdir "$out"
out=$(cd "$out" && pwd)
guest="$out/source"
mkdir -p "$guest/cmd/deck-workspace" "$guest/internal/deckwire" "$out/sqlport/internal/wire"
cp "$source_dir/go.mod" "$source_dir/go.sum" "$guest/"
cp -R "$source_dir/internal/." "$guest/internal/"
cp -R "$root/sqlport/." "$out/sqlport/"
cp "$root/wire/wire.go" "$out/sqlport/internal/wire/wire.go"
cp "$root/wire/wire.go" "$guest/internal/deckwire/wire.go"
cp "$root/guest.go.in" "$guest/cmd/deck-workspace/main.go"
cp -R "$source_dir/web/static" "$out/static"
if [[ "$profile" == weights ]]; then
  go run "$root/../compile.go" "$guest" --bind-repository >"$guest/internal/repository/deck_revision.json"
  cp "$root/revision.go.in" "$guest/internal/repository/deck_revision.go"
fi
(
  cd "$guest"
  go mod edit -replace "modernc.org/sqlite=$out/sqlport"
  GOOS=wasip1 GOARCH=wasm CGO_ENABLED=0 go build -trimpath -buildmode=c-shared -o "$out/workspace.wasm" ./cmd/deck-workspace
)
(
  cd "$root"
  go build -trimpath -o "$out/host" ./cmd/host
)
