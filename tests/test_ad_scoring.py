from unittest.mock import patch

# Mock LLM for script scoring tests
@patch("skills.ads_manager.scripts.score_ad_scripts.ask")
def test_score_script(mock_ask):
    mock_ask.return_value = """{
      "hook": 8,
      "pain": 9,
      "mechanism": 7,
      "proof": 6,
      "cta": 8,
      "total": 38,
      "verdict": "Ready to shoot",
      "top_improvement": "Add more community testimonials."
    }"""
    from skills.ads_manager.scripts.score_ad_scripts import score_script
    
    res = score_script("Mock script text about retail trading.")
    
    assert res["hook"] == 8
    assert res["pain"] == 9
    assert res["mechanism"] == 7
    assert res["proof"] == 6
    assert res["cta"] == 8
    assert res["total"] == 38
    assert res["verdict"] == "Ready to shoot"
    assert "testimonials" in res["top_improvement"]
