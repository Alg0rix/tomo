"""HtmlToMarkdown unit tests."""

from __future__ import annotations

from app.runtime.html_md import HtmlToMarkdown


def test_heading_setext_default() -> None:
    td = HtmlToMarkdown()
    assert td.convert("<h1>Level One Heading</h1>") == (
        "Level One Heading\n================="
    )


def test_heading_atx_option() -> None:
    td = HtmlToMarkdown({"headingStyle": "atx"})
    assert td.convert("<h1>Level One Heading with ATX</h1>") == (
        "# Level One Heading with ATX"
    )


def test_emphasis_and_strong() -> None:
    td = HtmlToMarkdown()
    assert td.convert("<p><em>em element</em></p>") == "_em element_"
    assert td.convert("<p><strong>strong element</strong></p>") == "**strong element**"


def test_inline_code_and_backticks() -> None:
    td = HtmlToMarkdown()
    assert td.convert("<p><code>code element</code></p>") == "`code element`"
    assert (
        td.convert("<p><code>There is a literal backtick (`) here</code></p>")
        == "``There is a literal backtick (`) here``"
    )


def test_link_inlined() -> None:
    td = HtmlToMarkdown()
    out = td.convert('<p><a href="http://example.com">An image</a></p>')
    assert out == "[An image](http://example.com)"


def test_image() -> None:
    td = HtmlToMarkdown()
    out = td.convert('<p><img src="http://example.com/logo.png" alt="logo"></p>')
    assert out == "![logo](http://example.com/logo.png)"


def test_list_unordered() -> None:
    td = HtmlToMarkdown()
    html = "<ul><li>Item 1</li><li>Item 2</li></ul>"
    out = td.convert(html)
    assert "* Item 1" in out
    assert "* Item 2" in out


def test_fenced_code_block() -> None:
    td = HtmlToMarkdown({"codeBlockStyle": "fenced"})
    html = '<pre><code class="language-js">const x = 1;\n</code></pre>'
    out = td.convert(html)
    assert out.startswith("```js")
    assert "const x = 1;" in out
    assert out.endswith("```")


def test_blockquote() -> None:
    td = HtmlToMarkdown()
    out = td.convert("<blockquote><p>Quoted</p></blockquote>")
    assert out == "> Quoted"


def test_hr() -> None:
    td = HtmlToMarkdown()
    assert td.convert("<hr>") == "* * *"


def test_escape_markdown() -> None:
    td = HtmlToMarkdown()
    assert td.escape("1. Hello") == r"1\. Hello"
    assert td.escape("# Not a heading") == r"\# Not a heading"


def test_remove_and_keep() -> None:
    td = HtmlToMarkdown()
    td.remove("del")
    assert td.convert("<p>Hello <del>world</del></p>") == "Hello"
    td2 = HtmlToMarkdown()
    td2.keep(["del"])
    out = td2.convert("<p>Hello <del>world</del></p>")
    assert "<del>world</del>" in out


def test_add_rule_strikethrough() -> None:
    td = HtmlToMarkdown()
    td.add_rule(
        "strikethrough",
        {
            "filter": ["del", "s", "strike"],
            "replacement": lambda content, node=None, options=None: f"~{content}~",
        },
    )
    assert td.convert("<p>Hello <del>world</del></p>") == "Hello ~world~"


def test_empty_string() -> None:
    assert HtmlToMarkdown().convert("") == ""
