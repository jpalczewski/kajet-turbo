#!/usr/bin/env bash
# SubagentStop gate for the `implementer` subagent (wired in its frontmatter).
# Blocks the subagent from finishing while changed Python files fail ruff or
# the project fails ty. Must stay seconds-fast: pytest is the agent's job, not
# the gate's.
#
# Exit 0 lets the subagent stop; exit 2 sends stderr back to it as the reason
# it has to keep working.
set -uo pipefail

payload=$(cat)

# A previous block already forced one continuation; never block twice in a row,
# or an unfixable failure would loop the subagent.
if [[ $(jq -r '.stop_hook_active // false' <<<"$payload") == true ]]; then
  exit 0
fi

cwd=$(jq -r '.cwd // empty' <<<"$payload")
cd "${cwd:-.}" 2>/dev/null || exit 0
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$root" || exit 0

changed=()
while IFS= read -r f; do
  [[ -f $f ]] && changed+=("$f")
done < <({
  git diff --name-only HEAD -- '*.py'
  git ls-files --others --exclude-standard -- '*.py'
} | sort -u)

((${#changed[@]})) || exit 0

# Skip re-running when the tree is byte-identical to the last state that passed.
# Captured before the checks so a concurrent edit can't be recorded as passing.
stamp_file=$(git rev-parse --git-path kajet-implementer-gate)
fingerprint=$({
  git diff HEAD -- '*.py'
  git ls-files --others --exclude-standard -z -- '*.py' | xargs -0 cat 2>/dev/null
} | git hash-object --stdin)
if [[ -f $stamp_file && $(<"$stamp_file") == "$fingerprint" ]]; then
  exit 0
fi

report=""
if ! out=$(uv run ruff check "${changed[@]}" 2>&1); then
  report+=$'ruff check:\n'"$out"$'\n\n'
fi
if ! out=$(uv run ruff format --check "${changed[@]}" 2>&1); then
  report+=$'ruff format --check (run `uv run ruff format` on these files):\n'"$out"$'\n\n'
fi
if ! out=$(uv run ty check 2>&1); then
  report+=$'ty check:\n'"$out"$'\n\n'
fi

if [[ -n $report ]]; then
  {
    echo "Lint/type gate failed on your changes. Fix these before reporting back;"
    echo "if one is outside your scope, say so in your report under Open points."
    echo
    printf '%s' "$report" | head -c 8000
  } >&2
  exit 2
fi

printf '%s' "$fingerprint" >"$stamp_file"
exit 0
