"""
Use python pytest_integration.py to run every .py test in this folder   
"""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent / "tests"


def resolve(name: str) -> str:
    """Turn a short name like 'create_partner' into a real test file path."""
    candidates = [
        Path(name),
        TESTS_DIR / name,
        TESTS_DIR / f"{name}.py",
        TESTS_DIR / f"test_{name}.py",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    print(f"No test file found for '{name}'. Try: python run_tests.py --list")
    sys.exit(2)


def list_tests() -> None:
    files = sorted(
        p for p in TESTS_DIR.glob("*.py") if p.name != "__init__.py"
    )
    print("Available test files:")
    for path in files:
        print(f"  {path.stem.removeprefix('test_')}  ->  tests/{path.name}")


def main(argv: list[str]) -> int:
    if "--list" in argv:
        list_tests()
        return 0

    # Leading arguments (before the first flag) are test names.
    split = next((i for i, a in enumerate(argv) if a.startswith("-")), len(argv))
    names, extra = argv[:split], argv[split:]

    targets = [resolve(n) for n in names] or [str(TESTS_DIR)]

    return pytest.main(
        [
            "-o",
            "python_files=test_*.py pytesting.py",
            "-ra",
            *targets,
            *extra,
        ]
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))