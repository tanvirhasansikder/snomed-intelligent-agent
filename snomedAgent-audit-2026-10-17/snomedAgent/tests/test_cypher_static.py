"""
Static checks over every Cypher string in the project source.

1. A pure-Python check that no query puts a parameter in a variable-length
   relationship bound (e.g. ``[:IS_A*1..$max_depth]``), which Cypher rejects.
2. An optional check with Neo4j's official Cypher linter
   (npm package ``@neo4j-cypher/language-support``). It runs only when
   Node.js is installed and CYPHER_LINT_NODE_PATH points at a node_modules
   folder containing that package; otherwise it is skipped. A lint pass is
   not proof that a query behaves correctly on a live server.
"""

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP_PARTS = {".venv", "tests", "__pycache__", ".git", "node_modules"}
CYPHER_HINTS = ("MATCH ", "MERGE ", "CREATE CONSTRAINT", "UNWIND ")
VARIABLE_LENGTH_PARAMETER = re.compile(r"\*\s*\d*\s*\.\.\s*\$")


def iter_cypher_strings():
    for path in sorted(ROOT.rglob("*.py")):
        if SKIP_PARTS & set(path.relative_to(ROOT).parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # String pieces inside f-strings are fragments, not whole queries.
        fragment_ids = {
            id(part)
            for joined in ast.walk(tree) if isinstance(joined, ast.JoinedStr)
            for part in joined.values
        }
        # Docstrings describe queries in prose; they are not queries.
        for scope in ast.walk(tree):
            if isinstance(scope, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = scope.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    fragment_ids.add(id(body[0].value))
        for node in ast.walk(tree):
            if id(node) in fragment_ids:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if any(h in text for h in CYPHER_HINTS) and "<" not in text:
                    yield f"{path.relative_to(ROOT)}:{node.lineno}", text
            elif isinstance(node, ast.JoinedStr):
                # f-strings: check the literal fragments for the bound pattern
                literal = "".join(
                    v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
                if any(h in literal for h in CYPHER_HINTS):
                    yield f"{path.relative_to(ROOT)}:{node.lineno} (f-string)", literal


def test_project_contains_cypher_queries():
    assert len(list(iter_cypher_strings())) >= 10


def test_no_parameter_used_as_variable_length_bound():
    offenders = [loc for loc, q in iter_cypher_strings() if VARIABLE_LENGTH_PARAMETER.search(q)]
    assert offenders == []


def _lint_available():
    node = shutil.which("node")
    module_path = os.environ.get("CYPHER_LINT_NODE_PATH")
    if not node or not module_path:
        return None
    probe = subprocess.run(
        [node, "-e", "require('@neo4j-cypher/language-support')"],
        env={**os.environ, "NODE_PATH": module_path},
        capture_output=True,
    )
    return node if probe.returncode == 0 else None


LINT_SCRIPT = r"""
const {lintCypherQuery} = require('@neo4j-cypher/language-support');
const qs = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = qs.map(([loc, q]) => {
  const r = lintCypherQuery(q, {});
  const errs = (r.diagnostics ?? r).filter(d => d.severity === 1).map(d => d.message);
  return [loc, errs];
});
process.stdout.write(JSON.stringify(out));
"""


def test_all_static_queries_pass_official_cypher_linter():
    node = _lint_available()
    if node is None:
        pytest.skip("Neo4j Cypher linter not installed (set CYPHER_LINT_NODE_PATH)")

    # f-string fragments are incomplete queries; lint only plain strings.
    queries = [(loc, q) for loc, q in iter_cypher_strings() if "f-string" not in loc]
    proc = subprocess.run(
        [node, "-e", LINT_SCRIPT],
        input=json.dumps(queries),
        env={**os.environ, "NODE_PATH": os.environ["CYPHER_LINT_NODE_PATH"]},
        capture_output=True,
        text=True,
        check=True,
    )
    failures = {loc: errs for loc, errs in json.loads(proc.stdout) if errs}
    assert failures == {}


def test_rendered_traversal_queries_pass_official_cypher_linter():
    """Lint the depth-interpolated queries exactly as they are sent."""
    node = _lint_available()
    if node is None:
        pytest.skip("Neo4j Cypher linter not installed (set CYPHER_LINT_NODE_PATH)")

    from database.graph_repository import GraphRepository
    from tests.fakes import FakeDB

    db = FakeDB()
    repo = GraphRepository(db=db)
    repo.get_ancestors("1", 3)
    repo.get_descendants("1", 3)
    repo.find_upward_path("1", "2")
    repo.mark_expansion("1", "up", "complete")
    repo.mark_expansion("1", "down", "partial")
    repo.get_coverage("1")
    repo.save_children("1", [{"concept_id": "2", "name": "x"}])
    repo.ensure_constraints()
    queries =[(f"rendered-{i}", q) for i, (q, _) in enumerate(db.calls)]

    proc = subprocess.run(
        [node, "-e", LINT_SCRIPT],
        input=json.dumps(queries),
        env={**os.environ, "NODE_PATH": os.environ["CYPHER_LINT_NODE_PATH"]},
        capture_output=True,
        text=True,
        check=True,
    )
    failures = {loc: errs for loc, errs in json.loads(proc.stdout) if errs}
    assert failures == {}
