#!/bin/sh
# Check a sketch: compile it, run 60 frames on the VM, and test that every frame is a picture.
#   scripts/check.sh piece.pde                 prints OK ... (exit 0) or FAIL ... with the line (exit 1)
#   scripts/check.sh piece.pde --sheet s.png   also saves 8 frames side by side as a PNG
# Any other option goes straight to the checker (--frames N, --json, --png last.png, --vm PATH).
# The checker is the repository's command line: the `immanence` command when it is installed,
# else `python3 -m immanence` (set PYTHONPATH to the repository's python/ folder), or whatever
# $SKETCH_CHECKER says.
set -eu
if [ $# -lt 1 ]; then
  echo "usage: $0 piece.pde [--sheet sheet.png] [--frames N] [--json]" >&2
  exit 2
fi
if [ -n "${SKETCH_CHECKER:-}" ]; then
  CHECKER=$SKETCH_CHECKER
elif command -v immanence >/dev/null 2>&1; then
  CHECKER=immanence
else
  CHECKER="python3 -m immanence"
fi
f=$1
shift
out=$($CHECKER check "$f" "$@") && rc=0 || rc=$?
printf '%s\n' "$out"
# On a failure with a line number, quote that line so the fix is easy to find.
line=$(printf '%s\n' "$out" | sed -n 's/.* line \([0-9][0-9]*\):.*/\1/p' | head -n 1)
if [ "$rc" -ne 0 ] && [ -n "$line" ] && [ -f "$f" ]; then
  printf '  line %s: %s\n' "$line" "$(sed -n "${line}p" "$f" | sed 's/^[[:space:]]*//')"
fi
exit "$rc"
