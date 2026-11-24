# SPDX-License-Identifier: Apache-2.0
"""gates/migration-safety.sh's judgement: expand/contract migration safety (docs/adr/0123).

    python3 -m core.gates.migration <base> <head> <effective-policy.env>

Reads the added and modified files of `git diff --no-renames --name-status
base head` (in $WORKLOAD, default the current repository) that match a
`RISK_HIGH_MIGRATIONS` glob of the effective policy or a default structural
migration glob, and flags the operations the previous application revision
cannot survive during a canary or after a rollback: dropping or renaming a
table or column, truncating, changing a column type, and adding or tightening
NOT NULL without a default. Comments and string literals never trigger a rule:
SQL is tokenized first, Python migrations are read as a syntax tree.

Unsafe operations are allowed only for a declared contract cleanup, a
`Contract-After: <ref>` line or a `## Contract migration` section in
$TICKET_FILE or in a commit message of base..head, whose <ref> is a commit
that is an ancestor of the base or a migration that exists at the base.
"""

import ast
import fnmatch
import os
import re
from collections import namedtuple

from .. import risk
from . import PIPE, NoGo, main, proc, say

Finding = namedtuple("Finding", "line rule text")

DEFAULT_GLOBS = ("*migrations/*.sql", "*migrations/*.py", "*db/migrate/*", "*prisma/migrations/*.sql",
                 "*flyway/*.sql", "*liquibase/*.sql")

WHY = {
    "drop-table": "the previous revision still reads or writes this object",
    "drop-column": "the previous revision still selects or inserts this column",
    "rename": "the previous revision still uses the old name",
    "truncate": "data the previous revision relies on is gone",
    "alter-type": "narrowing cannot be ruled out; only a change to unbounded text is provably widening",
    "add-not-null": "the previous revision's INSERT omits the column and fails: add it nullable or with a DEFAULT",
    "set-not-null": "the previous revision may still write NULL: tighten in a later contract step",
}

# --- SQL: tokenizing -------------------------------------------------------


def _strip(text, line_marker="--", block=True, strings=True):
    """TEXT without comments and, if STRINGS, without string contents; same line count.

    Single-quoted literals keep their quotes and lose their content (`''` and
    `E'..\\'..'` escapes honoured); double-quoted and backtick identifiers keep
    their shape, not their name. A block comment nests, as in PostgreSQL.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith(line_marker, i):
            while i < n and text[i] != "\n":
                i += 1
        elif block and text.startswith("/*", i):
            depth, i = 1, i + 2
            out.append(" ")
            while i < n and depth:
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    out.append("\n" if text[i] == "\n" else "")
                    i += 1
        elif strings and c == "'":
            escaped = i > 0 and text[i - 1] in "eE" and (i < 2 or not (text[i - 2].isalnum() or text[i - 2] == "_"))
            out.append("'")
            i += 1
            while i < n:
                if escaped and text[i] == "\\":
                    out.append(" " if text[i + 1:i + 2] != "\n" else "\n")
                    i += 2
                elif text[i] == "'":
                    if text[i + 1:i + 2] == "'":
                        out.append("  ")
                        i += 2
                        continue
                    break
                else:
                    out.append("\n" if text[i] == "\n" else " ")
                    i += 1
            out.append("'")
            i += 1
        elif strings and c in "\"`":
            out.append(c)
            i += 1
            while i < n and text[i] != c:
                out.append("\n" if text[i] == "\n" else "q")
                i += 1
            out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_sql(text):
    """SQL TEXT with comments removed and literals blanked (docs/adr/0123 §4)."""
    return _strip(text)


# --- SQL: rules ------------------------------------------------------------

_NAME = r"""[\w."`\[\]$]+"""
_CREATE = re.compile(rf"\bCREATE\s+(?:(?:GLOBAL|LOCAL)\s+)?(TEMP(?:ORARY)?\s+)?(?:UNLOGGED\s+)?TABLE\s+"
                     rf"(IF\s+NOT\s+EXISTS\s+)?({_NAME})", re.I)
_DROP = re.compile(r"\bDROP\s+(TEMP(?:ORARY)?\s+)?(TABLE|VIEW|MATERIALIZED\s+VIEW|SCHEMA|DATABASE)\s+"
                   r"(IF\s+EXISTS\s+)?(.*)", re.I)
_RENAME_TABLE = re.compile(r"\bRENAME\s+TABLE\b", re.I)
_TRUNCATE = re.compile(r"(?:^|\b(?:BEGIN|THEN|ELSE|LOOP)\s|\$\w*\$\s*)TRUNCATE\b(?!\s*\()\s*(?:TABLE\s+)?(?:ONLY\s+)?(.*)",
                       re.I)
_ALTER = re.compile(rf"\bALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?({_NAME})\s*(.*)", re.I)
_DROP_KEEP = ("CONSTRAINT|INDEX|KEY|PRIMARY|FOREIGN|CHECK|UNIQUE|PARTITION|DEFAULT|NOT|IDENTITY|EXPRESSION|"
              "TRIGGER|RULE|PERIOD|SYSTEM|CLUSTERING")
_C_DROP = re.compile(rf"DROP\s+(?:COLUMN\b|(?!(?:{_DROP_KEEP})\b)[\w\"`\[])", re.I)
_C_RENAME = re.compile(r"RENAME\b(?!\s+CONSTRAINT\b)", re.I)
_C_CHANGE = re.compile(rf"CHANGE\s+(?:COLUMN\s+)?({_NAME})\s+({_NAME})\s*(.*)", re.I)
_C_ALTER = re.compile(rf"ALTER\s+(?:COLUMN\s+)?{_NAME}\s+(.*)", re.I)
_C_MODIFY = re.compile(rf"MODIFY\s+(?:COLUMN\s+)?{_NAME}\s+(.*)", re.I)
_C_ADD = re.compile(r"ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
                    r"(?!(?:CONSTRAINT|PRIMARY|FOREIGN|CHECK|UNIQUE|INDEX|KEY|PARTITION|EXCLUDE|FULLTEXT|SPATIAL)\b)\S", re.I)
_NOT_NULL = re.compile(r"\bNOT\s+NULL\b", re.I)
_HAS_DEFAULT = re.compile(r"\bDEFAULT\b|\bAUTO_INCREMENT\b|\b(?:small|big)?serial\b|\bGENERATED\b|\bIDENTITY\b", re.I)
_UNBOUNDED_TEXT = re.compile(r"(?:text|mediumtext|longtext|varchar|character\s+varying)\b(?!\s*[(\[])", re.I)
_TEMP_NAME = re.compile(r"^(?:tmp|temp)(?:_|$)|_(?:tmp|temp)(?:_|$)", re.I)


def _norm(name):
    return re.sub(r'["`\[\]]', "", name).lower().split(".")[-1]


def _names(rest):
    """The leading table names of a comma list ("a, b CASCADE")."""
    return [_norm(part.split()[0]) for part in rest.split(",") if part.split()]


def _clauses(text):
    """TEXT split at its top-level commas."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return [c for c in out if c]


def _statements(stripped):
    """(line of the first character, text) of every `;`-separated statement."""
    pos = 0
    for chunk in stripped.split(";"):
        lead = len(chunk) - len(chunk.lstrip())
        if chunk.strip():
            yield stripped.count("\n", 0, pos + lead) + 1, " ".join(chunk.split())
        pos += len(chunk) + 1


def _alter_clause_rules(clause):
    """The rule ids one ALTER TABLE clause breaks."""
    if _C_DROP.match(clause):
        return ["drop-column"]
    if _C_RENAME.match(clause):
        return ["rename"]
    found = []
    typed = None
    change = _C_CHANGE.match(clause)
    if change:
        if _norm(change.group(1)) != _norm(change.group(2)):
            return ["rename"]
        typed = change.group(3)
    alter = _C_ALTER.match(clause)
    if alter:
        rest = alter.group(1)
        kind = re.match(r"(?:SET\s+DATA\s+)?TYPE\s+(.*)", rest, re.I)
        if kind:
            typed = kind.group(1)
        elif re.match(r"SET\s+NOT\s+NULL\b", rest, re.I):
            return ["set-not-null"]
        elif not re.match(r"(?:SET|DROP|ADD|RESTART|RESET)\b", rest, re.I):
            typed = rest
    modify = _C_MODIFY.match(clause)
    if modify:
        typed = modify.group(1)
    if typed is not None:
        if not _UNBOUNDED_TEXT.match(typed):
            found.append("alter-type")
        if _NOT_NULL.search(typed) and not _HAS_DEFAULT.search(typed):
            found.append("set-not-null")
        return found
    if _C_ADD.match(clause) and _NOT_NULL.search(clause) and not _HAS_DEFAULT.search(clause):
        return ["add-not-null"]
    return []


def _scan_stripped(stripped):
    findings, fresh, temp = [], set(), set()
    for line, stmt in _statements(stripped):
        def add(rule):
            findings.append(Finding(line, rule, stmt[:120]))

        drop = _DROP.search(stmt)
        if drop:
            temporary, kind, if_exists, rest = drop.groups()
            names = _names(rest)
            safe = bool(temporary) or (kind.upper() in ("TABLE", "VIEW") and all(
                n in temp or n in fresh or (if_exists and _TEMP_NAME.search(n)) for n in names))
            if not safe:
                add("drop-table")
        if _RENAME_TABLE.search(stmt):
            add("rename")
        trunc = _TRUNCATE.search(stmt)
        if trunc and not all(n in fresh for n in _names(trunc.group(1))):
            add("truncate")
        alter = _ALTER.search(stmt)
        if alter and _norm(alter.group(1)) not in fresh:
            for clause in _clauses(alter.group(2)):
                for rule in _alter_clause_rules(clause):
                    add(rule)
        create = _CREATE.search(stmt)
        if create:
            temporary, if_not_exists, name = create.groups()
            (temp if temporary else fresh if not if_not_exists else set()).add(_norm(name))
    return findings


def scan_sql(text):
    """The Findings of SQL TEXT, in statement order (docs/adr/0123 §3)."""
    return _scan_stripped(strip_sql(text))


# --- Python and Ruby migrations ---------------------------------------------

_EXECUTE = {"execute", "executescript", "exec_driver_sql", "RunSQL"}
_TEXT_TYPES = {"Text", "UnicodeText", "TEXT"}


def _call_name(node):
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None


def _const(node):
    return node.value if isinstance(node, ast.Constant) else None


def _kw(node, name):
    return next((k.value for k in node.keywords if k.arg == name), None)


def _arg(node, index, name):
    if len(node.args) > index:
        return node.args[index]
    return _kw(node, name)


def _is_false(node):
    return isinstance(node, ast.Constant) and node.value is False


def _is_true(node):
    return isinstance(node, ast.Constant) and node.value is True


def _upgrade_nodes(tree):
    """Every node of TREE outside a `downgrade` function (the undo path)."""
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "downgrade":
            continue
        yield node
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def scan_python(source):
    """The Findings of a Python migration (Alembic, Django, raw execute)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise NoGo(f"a Python migration does not parse (line {exc.lineno}): {exc.msg}") from None
    nodes = [n for n in _upgrade_nodes(tree) if isinstance(n, ast.Call)]
    fresh = set()
    for n in nodes:
        name = _call_name(n)
        if name == "create_table" and isinstance(_const(_arg(n, 0, "table_name")), str):
            fresh.add(_const(_arg(n, 0, "table_name")).lower())
        elif name == "CreateModel" and isinstance(_const(_arg(n, 0, "name")), str):
            fresh.add(_const(_arg(n, 0, "name")).lower())
    findings = []
    for n in nodes:
        name = _call_name(n)
        add = lambda rule: findings.append(Finding(n.lineno, rule, f"{name}(...)"))  # noqa: E731
        table = _const(_arg(n, 0, "table_name"))
        model = _const(_kw(n, "model_name"))
        if name in ("drop_column", "drop_table", "drop_view", "alter_column", "add_column", "rename_table"):
            if isinstance(table, str) and table.lower() in fresh:
                continue
        if name in ("RemoveField", "AddField", "AlterField", "DeleteModel"):
            owner = model if name != "DeleteModel" else _const(_arg(n, 0, "name"))
            if isinstance(owner, str) and owner.lower() in fresh:
                continue
        if name == "drop_column" or name == "RemoveField":
            add("drop-column")
        elif name in ("drop_table", "drop_view", "DeleteModel"):
            add("drop-table")
        elif name in ("rename_table", "RenameField", "RenameModel"):
            add("rename")
        elif name == "alter_column":
            if _kw(n, "new_column_name") is not None:
                add("rename")
            type_ = _kw(n, "type_")
            if type_ is not None:
                t = type_.func if isinstance(type_, ast.Call) else type_
                tname = t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", None)
                if tname not in _TEXT_TYPES:
                    add("alter-type")
            if _is_false(_kw(n, "nullable")) and _kw(n, "server_default") is None:
                add("set-not-null")
        elif name == "add_column":
            col = _arg(n, 1, "column")
            if isinstance(col, ast.Call) and _is_false(_kw(col, "nullable")) and _kw(col, "server_default") is None:
                add("add-not-null")
        elif name == "AddField":
            field = _arg(n, 2, "field")
            if isinstance(field, ast.Call) and _call_name(field) != "ManyToManyField" and not (
                    _is_true(_kw(field, "null")) or _kw(field, "default") is not None
                    or _kw(field, "db_default") is not None):
                add("add-not-null")
        elif name == "AlterField":
            field = _arg(n, 2, "field")
            if isinstance(field, ast.Call) and _is_false(_kw(field, "null")) and _kw(field, "default") is None:
                add("set-not-null")
        elif name in _EXECUTE:
            # RunSQL's second argument and reverse_sql undo the first: not judged.
            skip = {1} if name == "RunSQL" else set()
            parts = [a for i, a in enumerate(n.args) if i not in skip]
            parts += [k.value for k in n.keywords if k.arg not in ("reverse_sql", "state_operations", "hints")]
            for part in parts:
                for sub in ast.walk(part):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        findings += [Finding(sub.lineno + f.line - 1, f.rule, f.text) for f in scan_sql(sub.value)]
    return sorted(set(findings), key=lambda f: (f.line, f.rule))


_RB_RULES = (
    (re.compile(r"\b(?:remove_columns?|remove_reference|remove_belongs_to)\b"), "drop-column"),
    (re.compile(r"\b(?:drop_table|drop_join_table)\b"), "drop-table"),
    (re.compile(r"\b(?:rename_column|rename_table)\b"), "rename"),
    (re.compile(r"\bchange_column\b(?!_)"), "alter-type"),
    (re.compile(r"\bchange_column_null\b[^\n]*\bfalse\b"), "set-not-null"),
    (re.compile(r"\badd_column\b(?=[^\n]*\bnull:\s*false\b)(?![^\n]*\bdefault:)"), "add-not-null"),
)


def scan_ruby(source):
    """The Findings of a Rails migration's `up`/`change` path."""
    kept, skip_until = [], None
    for line in _strip(source, "#", block=False, strings=False).split("\n"):
        if skip_until is not None:
            if re.match(rf"{skip_until}end\b", line):
                skip_until = None
            kept.append("")
            continue
        down = re.match(r"(\s*)def\s+down\b", line)
        if down:
            skip_until = down.group(1)
            kept.append("")
            continue
        kept.append(line)
    stripped = "\n".join(kept)
    findings = [Finding(i, rule, line.strip()[:120]) for i, line in enumerate(kept, 1)
                for pattern, rule in _RB_RULES if pattern.search(line)]
    findings += _scan_stripped(stripped)
    return sorted(set(findings), key=lambda f: (f.line, f.rule))


# --- declaration of a contract cleanup ----------------------------------------

_DECL_LINE = re.compile(r"^\s*Contract-After:[ \t]*(.*\S)[ \t]*$", re.I | re.M)
_DECL_HEAD = re.compile(r"^#{2,}\s*Contract migration\b.*$", re.I | re.M)
_ANY_HEAD = re.compile(r"^#{1,6}\s", re.M)


def declarations(text):
    """The contract declarations in TEXT: each `Contract-After:` value and each
    `## Contract migration` section body (docs/adr/0123 §5)."""
    found = _DECL_LINE.findall(text)
    for head in _DECL_HEAD.finditer(text):
        body = text[head.end():]
        nxt = _ANY_HEAD.search(body)
        body = body[:nxt.start()] if nxt else body
        if body.strip():
            found.append(body.strip())
    return found


def _git(workload, *args):
    return proc(["git", "-C", workload, *args], stdout=PIPE, stderr=PIPE)


def _tokens(declared):
    for tok in re.split(r"[\s,;()\[\]<>\"'`]+", declared):
        tok = tok.rstrip(".:")
        if len(tok) >= 3:
            yield tok


def _resolve(workload, base, declared, migration_paths):
    """The first reference in DECLARED that exists before BASE, or None."""
    for tok in _tokens(declared):
        if re.fullmatch(r"[0-9a-fA-F]{7,40}", tok):
            if (_git(workload, "rev-parse", "--verify", "--quiet", f"{tok}^{{commit}}").returncode == 0
                    and _git(workload, "merge-base", "--is-ancestor", tok, base).returncode == 0):
                return tok
        for path in migration_paths:
            stem = os.path.splitext(os.path.basename(path))[0]
            if path == tok or path.endswith("/" + tok) or stem == tok:
                return tok
    return None


def _accepted_contract(workload, base, head, ticket_file, migration_globs):
    """(ref, declared texts): ref is the accepted reference or None."""
    texts = []
    if ticket_file:
        try:
            with open(ticket_file, encoding="utf-8", errors="replace") as fh:
                texts.append(fh.read())
        except OSError as exc:
            raise NoGo(f"TICKET_FILE cannot be read: {exc}") from None
    log = _git(workload, "log", "--format=%B%x00", f"{base}..{head}")
    if log.returncode != 0:
        log = _git(workload, "log", "--format=%B%x00", head)
    texts += log.stdout.decode("utf-8", "replace").split("\x00")
    declared = [d for t in texts for d in declarations(t)]
    if not declared:
        return None, []
    tree = _git(workload, "ls-tree", "-r", "--name-only", "-z", base).stdout.decode("utf-8", "replace")
    prior = [p for p in tree.split("\x00") if p and _matches(p, migration_globs)]
    for d in declared:
        ref = _resolve(workload, base, d, prior)
        if ref:
            return ref, declared
    return None, declared


# --- the judgement ------------------------------------------------------------


def _matches(path, globs):
    return any(fnmatch.fnmatch(path, g) for g in globs)


def _changed(workload, base, head):
    got = _git(workload, "diff", "--no-renames", "--name-status", "-z", base, head)
    if got.returncode != 0:
        raise NoGo(f"git diff {base} {head} failed", got.stderr.decode("utf-8", "replace"))
    parts = got.stdout.decode("utf-8", "replace").split("\x00")
    return [path for status, path in zip(parts[0::2], parts[1::2]) if status[:1] in ("A", "M", "T")]


def judge(argv, env=None):
    env = os.environ if env is None else env
    base, head, policy_file = argv
    workload = env.get("WORKLOAD") or "."
    try:
        with open(policy_file) as fh:
            policy = risk.parse_policy(fh.read())
    except OSError as exc:
        raise NoGo(f"the effective policy cannot be read: {exc}") from None
    globs = [g for g in policy.get("RISK_HIGH_MIGRATIONS", "").split(":") if g] + list(DEFAULT_GLOBS)

    files = [p for p in _changed(workload, base, head) if _matches(p, globs)]
    findings, analyzed = [], 0
    scanners = {".sql": scan_sql, ".py": scan_python, ".rb": scan_ruby}
    for path in files:
        scan = scanners.get(os.path.splitext(path)[1].lower())
        if scan is None:
            say(f"migration-safety: {path}: not analyzed (no analyzer for this file type)")
            continue
        got = _git(workload, "show", f"{head}:{path}")
        if got.returncode != 0:
            raise NoGo(f"{path} cannot be read at {head}")
        try:
            found = scan(got.stdout.decode("utf-8", "replace"))
        except NoGo as no:
            raise NoGo(f"{path}: {no.reason}") from None
        analyzed += 1
        findings += [(path, f) for f in found]
    if not files:
        say("migration-safety: no migration files in the range")
        return "no migration files changed"
    if not findings:
        say(f"migration-safety: {analyzed} migration file(s) judged, no rollback-unsafe operation")
        return f"{analyzed} migration file(s), expand-safe"

    ref, declared = _accepted_contract(workload, base, head, env.get("TICKET_FILE"), globs)
    lines = [f"{p}:{f.line}: {f.rule}: {f.text}" for p, f in findings]
    if ref:
        for line in lines:
            say(f"migration-safety: allowed (contract after {ref}): {line}")
        return f"{analyzed} migration file(s), contract cleanup allowed (contract after {ref})"
    for p, f in findings:
        say(f"migration-safety: {p}:{f.line}: {f.rule}: {WHY[f.rule]}")
    first_path, first = findings[0]
    more = f" (+{len(findings) - 1} more)" if len(findings) > 1 else ""
    if declared:
        raise NoGo("contract declaration has no reference to a prior expand migration or commit that exists "
                   f"before the base; first unsafe operation {first_path}:{first.line} [{first.rule}]{more}",
                   *[f"declared: {' '.join(d.split())[:200]}" for d in declared], *lines)
    raise NoGo(f"rollback-unsafe migration {first_path}:{first.line} [{first.rule}] {WHY[first.rule]}{more}",
               *lines,
               "fix: ship the expand now and the contract in a later PR that declares "
               "`Contract-After: <prior expand migration or commit>`")


if __name__ == "__main__":
    raise SystemExit(main(judge))
