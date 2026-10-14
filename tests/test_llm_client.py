import pytest
from unittest.mock import patch, MagicMock
import requests

from tools import llm_client


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # chat() checks for a provider API key before calling _call_api. Set a dummy
    # key + provider/model so the mocked _call_api is reached regardless of
    # whether a .env exists (passes in CI and in a keyless local clone). Also
    # ensure demo mode is off so the real code path under test runs.
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "test-model")
    monkeypatch.delenv("DEMO_MODE", raising=False)


@patch("tools.llm_client._call_api")
def test_llm_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": "Hello response"}}]}
    mock_post.return_value = mock_resp
    
    res = llm_client.ask("Sys", "User")
    assert res == "Hello response"

@patch("tools.llm_client.time.sleep")
@patch("tools.llm_client._call_api")
def test_llm_rate_limit_rotation(mock_post, mock_sleep):
    # First response: 429 rate limit
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"Retry-After": "5"}
    
    # Second response: 200 success
    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"choices": [{"message": {"content": "Success after retry"}}]}
    
    mock_post.side_effect = [mock_resp_429, mock_resp_200]
    
    res = llm_client.ask("Sys", "User")
    assert res == "Success after retry"
    mock_sleep.assert_called_once_with(7) # Retry-After (5) + 2

@patch("tools.llm_client._call_api")
def test_llm_immediate_server_error(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.raise_for_status.side_effect = requests.HTTPError("Server Error")
    mock_post.return_value = mock_resp
    
    with pytest.raises(requests.HTTPError):
        llm_client.ask("Sys", "User")
