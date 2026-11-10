"""compose's Markdown to Telegram HTML: ``**bold**``, ``_italic_`` and ``*italic*``, and nothing else is markup."""
import re
import unittest

from journal import compose

_TAGS = re.compile(r"<(/?)(b|i)>")


def balanced(markup: str) -> bool:
    stack = []
    for m in _TAGS.finditer(markup):
        if not m.group(1):
            stack.append(m.group(2))
        elif not stack or stack.pop() != m.group(2):
            return False
    return not stack


class StarItalicTest(unittest.TestCase):
    def test_a_starred_quote_is_italic(self):
        self.assertEqual(compose.md_to_html('He said *"a slow week"* and moved on.'),
                         'He said <i>"a slow week"</i> and moved on.')
        self.assertEqual(compose.md_to_html('*"a slow week"*.'), '<i>"a slow week"</i>.')
        self.assertEqual(compose.md_to_html("*a*"), "<i>a</i>")

    def test_bold_still_wins(self):
        self.assertEqual(compose.md_to_html("**bold** and *it*"), "<b>bold</b> and <i>it</i>")
        self.assertEqual(compose.md_to_html("*it* and **bold**"), "<i>it</i> and <b>bold</b>")
        self.assertEqual(compose.md_to_html("**bold**"), "<b>bold</b>")
        self.assertEqual(compose.md_to_html("**a *b* c**"), "<b>a <i>b</i> c</b>")
        self.assertEqual(compose.md_to_html("*a **b** c*"), "<i>a <b>b</b> c</i>")

    def test_a_lone_star_stays_literal(self):
        for text in ("5 * 3", "5 * 3 and 4 * 2", "2*3*4", "snake*case*word", "a * b", "*", "**", "* item", "end *"):
            self.assertEqual(compose.md_to_html(text), text)

    def test_a_star_bullet_stays_literal_in_a_multi_line_text(self):
        self.assertEqual(compose.md_to_html("* one\n* two"), "* one\n* two")
        self.assertEqual(compose.md_to_html("* one *two*"), "* one <i>two</i>")

    def test_italic_does_not_run_across_lines(self):
        self.assertEqual(compose.md_to_html("*one\ntwo*"), "*one\ntwo*")

    def test_text_is_escaped_inside_and_outside(self):
        self.assertEqual(compose.md_to_html("*a < b & c*"), "<i>a &lt; b &amp; c</i>")
        self.assertEqual(compose.md_to_html("<b>x</b> *y*"), "&lt;b&gt;x&lt;/b&gt; <i>y</i>")

    def test_the_other_markers_are_as_before(self):
        self.assertEqual(compose.md_to_html("_a_ and snake_case_word"), "<i>a</i> and snake_case_word")
        self.assertEqual(compose.md_to_html("*_a_*"), "<i><i>a</i></i>")

    def test_an_italic_that_would_cross_a_tag_stays_literal(self):
        out = compose.md_to_html("*a **b* c**")
        self.assertEqual(out, "*a <b>b* c</b>")
        for text in ("*a **b* c**", "*a _b* c_", "**a *b** c*", "*a **b** c* _d_ **e *f* g**"):
            self.assertTrue(balanced(compose.md_to_html(text)), text)

    def test_block_html_renders_star_italic_and_keeps_bullets(self):
        out = compose.md_block_html('> *"a quote"*\n* first *point*\n- second\n*Whole line italic*\n5 * 3')
        self.assertEqual(out, '<blockquote><i>"a quote"</i></blockquote>\n• first <i>point</i>\n• second\n'
                              '<i>Whole line italic</i>\n5 * 3')

    def test_a_reflection_and_annotations_render_star_italic(self):
        self.assertIn('• you wrote <i>"tired but fine"</i>', compose.annotation_html("j", '- you wrote *"tired but fine"*'))
        self.assertIn('<i>"tired but fine"</i>', compose.reflection_html("Week", '*"tired but fine"*'))

    def test_the_plain_text_forms_lose_the_stars(self):
        self.assertEqual(compose._md_plain('He said *"slow"* and **loud**, 5 * 3, _x_'), 'He said "slow" and loud, 5 * 3, x')
        markup = compose.md_to_html('He said *"slow"* and **loud**.')
        self.assertEqual(compose.plain(markup), 'He said "slow" and loud.')


if __name__ == "__main__":
    unittest.main()
