"""Stage the pinned native MyNotes and the second executable teacher."""
import argparse
import shutil
import subprocess
from pathlib import Path

PIN = "73ef4b7b37109a5209d9fbcfbd5d3799918071be"
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("source", type=Path)
p.add_argument("output", type=Path)
args = p.parse_args()
source = args.source.resolve()
commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
if commit != PIN:
    raise ValueError(f"Expected MyNotes {PIN}, found {commit}")
subprocess.run(["git", "-C", str(source), "diff", "--quiet", "HEAD", "--"], check=True)
if not (source / "web/static/app.js").is_file():
    raise ValueError("Build the native browser assets before staging")

def replace_one(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Source contract changed: " + old[:90])
    return text.replace(old, new)

args.output.mkdir()
for version in ("original", "review"):
    dest = args.output / version
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".git", "node_modules"))
    if version == "review":
        path = dest / "internal/service/service.go"
        service = path.read_text()
        service = replace_one(service, "if h.level < minLevel {", "if h.level > minLevel {")
        start = service.index("func (s *NoteService) Split(")
        before, split = service[:start], service[start:]
        split = replace_one(split, """	var tagSlugs []string
	if tag != nil && *tag != "" {
		tagSlugs = []string{*tag}
	}""", """	var tagSlugs []string
	for _, inherited := range existing.Tags {
		tagSlugs = append(tagSlugs, inherited.Slug)
	}
	if tag != nil && *tag != "" {
		tagSlugs = append(tagSlugs, *tag)
	}""")
        split = replace_one(split, """	if len(sections) == 0 {
		return nil, validationError("note has no headings to split on")
	}""", """	if len(sections) < 2 {
		return nil, validationError("review split requires at least two sections")
	}""")
        path.write_text(before + split)
    shutil.copyfile(Path(__file__).with_name("observe.go.in"), dest / "internal/service/holodeck_observe_test.go")
