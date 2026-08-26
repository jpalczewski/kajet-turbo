import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "kajet_turbo"
SESSION_ALLOWED = {
    SRC / "db.py",  # schema/bootstrap infrastructure, not a runtime repository operation
    SRC / "repositories" / "__init__.py",  # the one shared session factory
}
REPOSITORY_EXCEPTIONS = {"DbRepository", "GitRepository"}


def _python_files():
    yield from SRC.rglob("*.py")


def test_runtime_sessions_are_centralized_in_db_repository():
    violations = []
    for path in _python_files():
        if path in SESSION_ALLOWED:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Session"
            ):
                violations.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert violations == []


def test_services_do_not_reach_into_repository_engines():
    violations = []
    for path in (SRC / "services").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "_engine":
                violations.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert violations == []


def test_db_repositories_inherit_the_common_base():
    violations = []
    roots = [SRC / "repositories", SRC / "embedding" / "cache.py"]
    paths = (roots[1], *roots[0].rglob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not node.name.endswith("Repository"):
                continue
            if node.name in REPOSITORY_EXCEPTIONS:
                continue
            bases = {base.id for base in node.bases if isinstance(base, ast.Name)}
            if "DbRepository" not in bases:
                violations.append(f"{path.relative_to(SRC)}:{node.lineno}:{node.name}")
                continue
            names = {
                target.id
                for statement in node.body
                if isinstance(statement, ast.Assign)
                for target in statement.targets
                if isinstance(target, ast.Name)
            }
            if "repository_name" not in names:
                violations.append(
                    f"{path.relative_to(SRC)}:{node.lineno}:{node.name}:repository_name"
                )
    assert violations == []


def test_repository_info_logs_use_the_common_contract():
    violations = []
    base = SRC / "repositories" / "__init__.py"
    git = SRC / "repositories" / "git.py"
    for path in (SRC / "repositories").rglob("*.py"):
        if path in (base, git):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "info"
            ):
                violations.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert violations == []
