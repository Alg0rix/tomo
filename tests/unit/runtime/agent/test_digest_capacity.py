from app.runtime.agent.learning.prompts import system_prompt


def test_review_guides_vault_writes_without_storage_quotas():
    prompt = system_prompt(review_memory=True, review_skills=True)
    assert "user/profile" in prompt
    assert "no character quota" in prompt
    assert "target=" not in prompt
    assert "`remember`" not in prompt
    assert "hard char limit" not in prompt
