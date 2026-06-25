from markdown_it import MarkdownIt

from kajet_turbo.markdown._tokens import extract_meta, walk_tokens


def test_walk_tokens_descends_into_inline_children():
    md = MarkdownIt("commonmark")
    tokens = md.parse("# heading\n\nparagraph *em*")
    types = [t.type for t in walk_tokens(tokens)]
    # inline children (text/em_open) only appear if we descended into the inline token
    assert "inline" in types
    assert "em_open" in types


def test_extract_meta_collects_meta_of_matching_type():
    md = MarkdownIt("commonmark")

    def rule(state, silent):
        if state.src[state.pos] != "@":
            return False
        if not silent:
            tok = state.push("at_token", "", 0)
            tok.meta = {"name": state.src[state.pos + 1 : state.pos + 4]}
        state.pos += 4
        return True

    md.inline.ruler.before("link", "at_token", rule)
    metas = list(extract_meta(md, "hello @bob world", "at_token"))
    assert metas == [{"name": "bob"}]


def test_extract_meta_ignores_code_spans():
    md = MarkdownIt("commonmark")

    def rule(state, silent):
        if state.src[state.pos] != "@":
            return False
        if not silent:
            tok = state.push("at_token", "", 0)
            tok.meta = {"name": "x"}
        state.pos += 1
        return True

    md.inline.ruler.before("link", "at_token", rule)
    assert list(extract_meta(md, "`@x`", "at_token")) == []
