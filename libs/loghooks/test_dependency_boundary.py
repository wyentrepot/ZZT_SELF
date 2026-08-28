import ast
from pathlib import Path


def test_loghooks_production_has_no_sim_concentrator_import():
    root = Path(__file__).parent
    for path in root.rglob("*.py"):
        if path.name.startswith("test_") or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert all("sim_concentrator" not in name for name in names), f"forbidden import in {path}: {names}"
