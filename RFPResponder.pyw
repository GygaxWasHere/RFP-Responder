"""Double-click to start Milestone RFP Responder (no console window)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from responder.__main__ import main  # noqa: E402

main()
