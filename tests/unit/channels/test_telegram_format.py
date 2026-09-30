"""Telegram rendering preserves text and produces independently valid chunks."""

import html
from html.parser import HTMLParser

from app.channels.telegram_format import (
    plain_text,
    render_markdown,
    split_html,
    utf16_len,
)


class TagChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.tags = []

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "i", "s", "code", "pre", "a", "blockquote"}
        self.stack.append(tag)
        self.tags.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


def assert_valid(text):
    checker = TagChecker()
    checker.feed(text)
    assert not checker.stack
    return checker


def test_rich_markdown_and_untrusted_html():
    source = '# Title\n\n**bold _nested_** and `a < b` [link](https://example.com/?a=1&b=2)\n\n- first\n- second\n\n> quote\n\n```python\nprint("<tag>")\n```\n\n<script>unsafe</script>'
    rendered = render_markdown(source)
    checker = assert_valid(rendered)
    assert {"b", "i", "code", "pre", "a", "blockquote"} <= set(checker.tags)
    assert "<script>" not in rendered
    assert "<script>unsafe</script>" in plain_text(rendered)
    assert 'print("<tag>")' in plain_text(rendered)
    assert "• first" in plain_text(rendered)


def test_tables_are_readable_on_mobile():
    rendered = render_markdown(
        "| Service | State |\n| --- | --- |\n| api | **up** |\n| db | down |"
    )
    assert "Service: api" in plain_text(rendered)
    assert "State: up" in plain_text(rendered)
    assert "Service: db" in plain_text(rendered)
    assert_valid(rendered)


def test_long_unicode_code_and_links_split_without_loss():
    text = '😀 & < > " x\n' * 1400
    source = (
        "```python\n"
        + text
        + "```\n\n["
        + ("nested **bold** " * 1000)
        + "](https://example.com)"
    )
    rendered = render_markdown(source)
    chunks = split_html(rendered)
    assert len(chunks) > 2
    for chunk in chunks:
        assert utf16_len(chunk) <= 3900
        assert_valid(chunk)
    assert "".join(plain_text(c) for c in chunks) == plain_text(rendered)
    assert text in plain_text(rendered)


def test_unclosed_code_is_rendered_and_unsafe_link_stays_text():
    for source in [
        "```\n<oops>& 😀",
        "[label](javascript:alert(1))",
        "[**nested** label](/relative)",
        "**half",
    ]:
        rendered = render_markdown(source)
        assert_valid(rendered)
        assert 'href="javascript:' not in rendered
    assert html.unescape(plain_text(render_markdown("```\n<oops>& 😀"))) == "<oops>& 😀"


def test_telegram_forbidden_entity_nesting_is_flattened_without_losing_text():
    source = "**bold `code` end** [link `code`](https://example.com)\n\n> outside\n> > inside\n> `quoted code`\n> ```\n> fenced code\n> ```"
    rendered = render_markdown(source)
    assert_valid(rendered)
    assert "<b>bold </b><code>code</code><b> end</b>" in rendered
    assert rendered.count("<blockquote>") == 1
    quote = rendered.split("<blockquote>")[1].split("</blockquote>")[0]
    assert "<code>" not in quote and "<pre>" not in quote
    assert "fenced code" in quote and "quoted code" in quote
