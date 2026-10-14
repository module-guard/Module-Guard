"""Export a stable superset contract (development routes included explicitly)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings
from app.main import create_app

output = (
    Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "openapi.json"
)
app = create_app(Settings(app_environment="development"))
output.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
app.state.session_factory.kw["bind"].dispose()
