import os
import tempfile
import pytest
from pathlib import Path

# Override board path for testing before importing kanban
TEST_BOARD = Path(tempfile.gettempdir()) / "test_board.json"
os.environ["KANBAN_BOARD_PATH"] = str(TEST_BOARD)

from tools import kanban  # noqa: E402

@pytest.fixture(autouse=True)
def cleanup():
    if TEST_BOARD.exists():
        TEST_BOARD.unlink()
    yield
    if TEST_BOARD.exists():
        TEST_BOARD.unlink()

def test_add_card():
    card = kanban.add_card("Test task", "marketing_manager")
    assert card["title"] == "Test task"
    assert card["skill"] == "marketing_manager"
    assert card["column"] == "Backlog"

def test_move_card():
    card = kanban.add_card("Test move", "ads_manager")
    kanban.move(card["id"], "In Progress")
    
    board = kanban._load()
    updated_card = next(c for c in board["cards"] if c["id"] == card["id"])
    assert updated_card["column"] == "In Progress"

def test_has_open_cards():
    assert not kanban.has_open_cards()
    kanban.add_card("Open Task", "influencer_outreach")
    assert kanban.has_open_cards()

def test_seed_default_board():
    kanban.seed_default_board()
    board = kanban._load()
    assert len(board["cards"]) == 8
    assert "Competitor research:" in board["cards"][0]["title"]
