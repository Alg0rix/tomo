from app.runtime.memory.vault.write import near_duplicate


def test_duplicate_normalization_and_long_substrings():
    assert near_duplicate(["User prefers short answers."], " user PREFERS   short answers. ")
    assert near_duplicate(["User prefers short answers. Especially in chat."], "User prefers short answers.")
    assert near_duplicate(["host server alpha"], "host") is None
    assert near_duplicate(["Port is 8000."], "Port is 9000.") is None
