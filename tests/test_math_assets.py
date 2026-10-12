"""LaTeX support: the pages load the vendored KaTeX and the shared renderer, and every file they need is served locally."""
import re

import pytest


@pytest.mark.parametrize('path', ['/', '/problem_creation.html'])
def test_pages_load_katex_and_renderer(admin, path):
    html = admin.get(path).get_data(as_text=True)
    for asset in ('vendor/katex/katex.min.css', 'vendor/katex/katex.min.js', 'js/markdown.js'):
        assert f'/static/{asset}' in html
    # katex and DOMPurify must be loaded before the renderer that uses them
    assert html.index('vendor/katex/katex.min.js') < html.index('js/markdown.js')
    assert html.index('vendor/dompurify/purify.min.js') < html.index('js/markdown.js')


def test_katex_assets_and_fonts_are_served(admin):
    css = admin.get('/static/vendor/katex/katex.min.css')
    assert css.status_code == 200
    fonts = set(re.findall(r'url\((fonts/[^)]+)\)', css.get_data(as_text=True)))
    assert len(fonts) >= 10
    for font in fonts:
        assert font.endswith('.woff2')
        assert admin.get(f'/static/vendor/katex/{font}').status_code == 200, font
    assert admin.get('/static/vendor/katex/katex.min.js').status_code == 200
    assert admin.get('/static/js/markdown.js').status_code == 200
