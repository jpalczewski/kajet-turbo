"""Kajet Turbo project board helper shared by the issue-filing, backlog-triage and
next-selection skills.

The board ids below are the single source of truth — skills call this script instead of
copying option ids around. Stdlib only; talks to GitHub through the `gh` CLI.

    python3 .claude/skills/_shared/board.py list --status Inbox
    python3 .claude/skills/_shared/board.py set 403 368 --status Next --priority P1
    python3 .claude/skills/_shared/board.py stale
    python3 .claude/skills/_shared/board.py tick-epic 377 [--apply]
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

OWNER = "jpalczewski"
REPO = "kajet-turbo"
PROJECT_NUMBER = 11
PROJECT_ID = "PVT_kwHOAEkkts4Bhu7F"
STATUS_FIELD = "PVTSSF_lAHOAEkkts4Bhu7Fzhgpl3s"
PRIORITY_FIELD = "PVTSSF_lAHOAEkkts4Bhu7Fzhgpmok"
# "Done" is set by the project's closed-issue workflow; never set it by hand.
STATUS_OPTIONS = {
    "Inbox": "f75ad846",
    "Backlog": "9350a0ef",
    "Next": "f077e336",
    "In Progress": "47fc9ee4",
    "Blocked": "4e38d989",
}
PRIORITY_OPTIONS = {"P0": "c655e612", "P1": "39a74ced", "P2": "a0bef9e8", "P3": "383f5b12"}
STATUS_ORDER = ["In Progress", "Next", "Blocked", "Backlog", "Inbox", "Done", None]
EPIC_LABEL = "🧭 epic"
TYPE_LABELS = {
    "🐛 bug": "bug",
    "✨ enhancement": "feat",
    "♻️ refactor": "refactor",
    "⚡ perf": "perf",
    "🧭 epic": "epic",
    "❓ question": "question",
}
BLOCKED_ON_HEADING = "## Blocked on"
UNTICKED = re.compile(r"^- \[ \] #(\d+)\b(.*)$", re.MULTILINE)
# Any open checkbox: issue refs, placeholder codes (T0, F1, ...) and acceptance criteria alike.
ANY_UNTICKED = re.compile(r"^- \[ \] ", re.MULTILINE)

ITEMS_QUERY = """
query($owner: String!, $number: Int!, $cursor: String) {
  user(login: $owner) {
    projectV2(number: $number) {
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          status: fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          priority: fieldValueByName(name: "Priority") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          content {
            ... on Issue {
              number title state body
              labels(first: 20) { nodes { name } }
              blockedBy(first: 20) { nodes { number state } }
              openPrs: closedByPullRequestsReferences(first: 5) { nodes { number } }
            }
          }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Item:
    item_id: str
    number: int
    title: str
    state: str
    body: str
    status: str | None
    priority: str | None
    labels: tuple[str, ...]
    open_blockers: tuple[int, ...]
    open_prs: tuple[int, ...]

    @property
    def kind(self) -> str:
        return next((TYPE_LABELS[name] for name in self.labels if name in TYPE_LABELS), "-")

    @property
    def is_epic(self) -> bool:
        return EPIC_LABEL in self.labels


@dataclass
class Findings:
    sections: dict[str, list[str]] = field(default_factory=dict)

    def add(self, section: str, line: str) -> None:
        self.sections.setdefault(section, []).append(line)


def refs(numbers: tuple[int, ...] | list[int]) -> str:
    return ", ".join(f"#{n}" for n in numbers) or "-"


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()}")
    return result.stdout


def graphql(query: str, **variables: str | int | None) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    return json.loads(gh(*args))["data"]


def fetch_items() -> list[Item]:
    items: list[Item] = []
    cursor: str | None = None
    while True:
        page = graphql(ITEMS_QUERY, owner=OWNER, number=PROJECT_NUMBER, cursor=cursor)
        conn = page["user"]["projectV2"]["items"]
        for node in conn["nodes"]:
            issue = node["content"]
            if not issue or "number" not in issue:  # draft items and PRs
                continue
            items.append(
                Item(
                    item_id=node["id"],
                    number=issue["number"],
                    title=issue["title"],
                    state=issue["state"],
                    body=issue["body"] or "",
                    status=(node["status"] or {}).get("name"),
                    priority=(node["priority"] or {}).get("name"),
                    labels=tuple(label["name"] for label in issue["labels"]["nodes"]),
                    open_blockers=tuple(
                        b["number"] for b in issue["blockedBy"]["nodes"] if b["state"] == "OPEN"
                    ),
                    open_prs=tuple(pr["number"] for pr in issue["openPrs"]["nodes"]),
                )
            )
        if not conn["pageInfo"]["hasNextPage"]:
            return items
        cursor = conn["pageInfo"]["endCursor"]


def issue_states(numbers: set[int]) -> dict[int, str]:
    """OPEN/CLOSED for arbitrary issue numbers, including ones not on the board."""
    if not numbers:
        return {}
    aliases = " ".join(f"i{n}: issue(number: {n}) {{ number state }}" for n in sorted(numbers))
    data = graphql(f'{{ repository(owner: "{OWNER}", name: "{REPO}") {{ {aliases} }} }}')
    return {v["number"]: v["state"] for v in data["repository"].values() if v}


def merged_pr_for(number: int) -> int | None:
    """The merged PR that closed an issue, via closing references or the close event."""
    data = graphql(
        f"""{{ repository(owner: "{OWNER}", name: "{REPO}") {{ issue(number: {number}) {{
          refs: closedByPullRequestsReferences(first: 5, includeClosedPrs: true) {{
            nodes {{ number merged }} }}
          closed: timelineItems(itemTypes: [CLOSED_EVENT], last: 1) {{
            nodes {{ ... on ClosedEvent {{ closer {{ ... on PullRequest {{ number }} }} }} }} }}
        }} }} }}"""
    )["repository"]["issue"]
    merged = [pr["number"] for pr in data["refs"]["nodes"] if pr["merged"]]
    if merged:
        return merged[0]
    events = data["closed"]["nodes"]
    closer = events[0].get("closer") if events else None
    return closer.get("number") if closer else None


def sort_key(item: Item) -> tuple[int, str, int]:
    return STATUS_ORDER.index(item.status), item.priority or "P9", item.number


def cmd_list(args: argparse.Namespace) -> None:
    items = [i for i in fetch_items() if i.state == "OPEN"]
    if args.status:
        items = [i for i in items if i.status in args.status]
    for item in sorted(items, key=sort_key):
        blockers = refs(item.open_blockers)
        print(
            f"#{item.number:<4} {item.status or '-':<11} {item.priority or '-':<2} "
            f"{item.kind:<8} blocked-by:{blockers:<10} {item.title[:80]}"
        )
    print(f"\n{len(items)} open issues")


def cmd_set(args: argparse.Namespace) -> None:
    by_number = {i.number: i for i in fetch_items()}
    for number in args.numbers:
        item = by_number.get(number)
        if item is None:
            sys.exit(f"#{number} is not on the board — gh issue edit {number} --add-project ...")
        base = ["project", "item-edit", "--id", item.item_id, "--project-id", PROJECT_ID]
        if args.status:
            option = STATUS_OPTIONS[args.status]
            gh(*base, "--field-id", STATUS_FIELD, "--single-select-option-id", option)
        if args.priority == "none":
            gh(*base, "--field-id", PRIORITY_FIELD, "--clear")
        elif args.priority:
            option = PRIORITY_OPTIONS[args.priority]
            gh(*base, "--field-id", PRIORITY_FIELD, "--single-select-option-id", option)
        status, priority = args.status or item.status, args.priority or item.priority
        print(f"#{number}: status={status} priority={priority}")


def check_board(items: list[Item], findings: Findings) -> None:
    on_board = {i.number for i in items}
    open_issues = json.loads(
        gh("issue", "list", "--state", "open", "--limit", "500", "--json", "number,title")
    )
    for issue in open_issues:
        if issue["number"] not in on_board:
            findings.add("Not on the board", f"#{issue['number']} {issue['title'][:80]}")

    for item in (i for i in items if i.state == "OPEN"):
        if item.status == "Blocked" and item.open_blockers:
            findings.add(
                "Blocked status used for an issue-to-issue block (native link carries it)",
                f"#{item.number} blocked by {refs(item.open_blockers)} -> Backlog",
            )
        if item.status == "Blocked" and BLOCKED_ON_HEADING not in item.body:
            findings.add(
                f"Blocked without a '{BLOCKED_ON_HEADING}' section naming the external wait",
                f"#{item.number} {item.title[:80]}",
            )
        if item.status == "Next" and item.open_blockers:
            findings.add(
                "In Next but blocked by an open issue (fine only as an ordered chain)",
                f"#{item.number} blocked by {refs(item.open_blockers)}",
            )
        if item.open_prs and item.status not in {"In Progress", "Done"}:
            findings.add(
                "Has an open closing PR but is not In Progress",
                f"#{item.number} PRs {refs(item.open_prs)} status={item.status}",
            )
        if item.is_epic and item.priority:
            findings.add("Epic carries a priority (epics stay unprioritized)", f"#{item.number}")
        untriaged = {None, "Inbox", "In Progress", "Done"}
        if item.status not in untriaged and not item.is_epic and not item.priority:
            findings.add("Triaged issue without a priority", f"#{item.number} {item.title[:80]}")


def check_prs(items: list[Item], findings: Findings) -> None:
    open_numbers = {i.number for i in items if i.state == "OPEN"}
    prs = json.loads(
        gh(
            "pr", "list", "--state", "open", "--json",
            "number,title,headRefName,author,closingIssuesReferences",
        )
    )  # fmt: skip
    for pr in prs:
        if pr["author"].get("is_bot") or pr["author"]["login"].startswith("app/"):
            continue
        closes = {ref["number"] for ref in pr["closingIssuesReferences"]}
        mentioned = {int(n) for n in re.findall(r"#(\d+)", pr["title"])}
        mentioned |= {
            int(n) for n in re.findall(r"(?:^|[-/])(\d{2,4})(?=$|[-/])", pr["headRefName"])
        }
        for number in sorted((mentioned & open_numbers) - closes):
            findings.add(
                "Open PR mentions an issue without a Closes line",
                f"PR #{pr['number']} -> #{number} (branch {pr['headRefName']})",
            )


def check_epics(items: list[Item], findings: Findings) -> None:
    epics = [i for i in items if i.is_epic and i.state == "OPEN"]
    children = {int(m.group(1)) for e in epics for m in UNTICKED.finditer(e.body)}
    states = issue_states(children)
    for epic in epics:
        unticked = [int(m.group(1)) for m in UNTICKED.finditer(epic.body)]
        closed = [n for n in unticked if states.get(n) == "CLOSED"]
        if closed:
            findings.add(
                "Epic has closed children still unticked (run tick-epic)",
                f"#{epic.number}: {refs(closed)}",
            )
        if closed and len(closed) == len(ANY_UNTICKED.findall(epic.body)):
            findings.add(
                "Epic whose every open checkbox is a closed issue (close candidate — ask first)",
                f"#{epic.number} {epic.title[:80]}",
            )


def cmd_stale(_: argparse.Namespace) -> None:
    items = fetch_items()
    findings = Findings()
    check_board(items, findings)
    check_prs(items, findings)
    check_epics(items, findings)
    if not findings.sections:
        print("No mechanical staleness found. Obsolete premises still need a code check.")
        return
    for section, lines in findings.sections.items():
        print(f"## {section}")
        for line in lines:
            print(f"- {line}")
        print()


def cmd_tick_epic(args: argparse.Namespace) -> None:
    body = json.loads(gh("issue", "view", str(args.epic), "--json", "body"))["body"]
    states = issue_states({int(m.group(1)) for m in UNTICKED.finditer(body)})

    def tick(match: re.Match[str]) -> str:
        number, rest = int(match.group(1)), match.group(2)
        if states.get(number) != "CLOSED":
            return match.group(0)
        pr = merged_pr_for(number)
        suffix = f" (#{pr})" if pr else "  <!-- no merged PR found: add the ref by hand -->"
        print(f"tick #{number}" + (f" (#{pr})" if pr else " — no PR found"))
        return f"- [x] #{number}{rest}{suffix}"

    new_body = UNTICKED.sub(tick, body)
    if new_body == body:
        print("Nothing to tick.")
        return
    if not args.apply:
        print("\nDry run — re-run with --apply to write the epic body.")
        return
    with NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(new_body)
    gh("issue", "edit", str(args.epic), "--body-file", fh.name)
    Path(fh.name).unlink()
    print(f"Updated #{args.epic}. Replace any 'no merged PR found' comment before moving on.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(required=True)

    p_list = sub.add_parser("list", help="open board issues, sorted by status and priority")
    p_list.add_argument("--status", nargs="*", choices=[*STATUS_OPTIONS, "Done"])
    p_list.set_defaults(func=cmd_list)

    p_set = sub.add_parser("set", help="set Status and/or Priority on board items")
    p_set.add_argument("numbers", nargs="+", type=int)
    p_set.add_argument("--status", choices=list(STATUS_OPTIONS))
    p_set.add_argument("--priority", choices=[*PRIORITY_OPTIONS, "none"])
    p_set.set_defaults(func=cmd_set)

    p_stale = sub.add_parser("stale", help="mechanical staleness report (read-only)")
    p_stale.set_defaults(func=cmd_stale)

    p_tick = sub.add_parser("tick-epic", help="tick closed children in an epic's checklist")
    p_tick.add_argument("epic", type=int)
    p_tick.add_argument("--apply", action="store_true")
    p_tick.set_defaults(func=cmd_tick_epic)

    args = parser.parse_args()
    if getattr(args, "func", None) is cmd_set and not (args.status or args.priority):
        parser.error("set needs --status and/or --priority")
    args.func(args)


if __name__ == "__main__":
    main()
