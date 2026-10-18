import os
import tempfile

# Must run before paper_v2.config is imported: keep tests away from the real paper_v2_state/.
os.environ["PAPER_V2_STATE_DIR"] = tempfile.mkdtemp(prefix="paper_v2_test_state_")
