import subprocess
from pathlib import Path
 
import pytest
 
TESTS_DIR = Path(__file__).resolve().parent
PATTERNS = ["*.cjs"]
 
CJS_TEST_FILES = sorted({p for pattern in PATTERNS for p in TESTS_DIR.rglob(pattern)})
 
 
@pytest.mark.parametrize("cjs_file", CJS_TEST_FILES, ids=lambda p: p.name)
def test_cjs_file(cjs_file):
    result = subprocess.run(
        ["node", "--test", str(cjs_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr