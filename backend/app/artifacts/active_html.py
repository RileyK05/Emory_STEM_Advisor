"""Self-contained inert HTML; untrusted scripts stay stored but cannot execute."""
import html

from app.artifacts.evidence import source_legend
from app.artifacts.inert_html import render_inert_markup

OUTER_POLICY = "frame-src 'self'; object-src 'none'; base-uri 'self'"
INNER_POLICY = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    "script-src 'none'; connect-src 'none'; worker-src 'none'; "
    "form-action 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'"
)


def render_active_html(spec):
    # Chromium tests showed frame-src does not stop child self-navigation.
    # Inert allowlisted markup removes those capabilities before rendering.
    inner = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="' + INNER_POLICY + '">'
        '</head><body>' + render_inert_markup(spec.html) + '</body></html>'
    )
    legend = ""
    if spec.evidence:
        legend = '<section aria-label="Sources"><h2>Sources</h2><ul>' + "".join(
            "<li>" + html.escape(f"{element}: {source} · {locator} ({chunk_id})") + "</li>"
            for element, source, locator, chunk_id in source_legend(spec)
        ) + "</ul></section>"
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="' + OUTER_POLICY + '">'
        '<title>' + html.escape(spec.title) + '</title>'
        '<style>body{margin:0;font-family:system-ui,sans-serif}'
        'iframe{width:100%;height:90vh;border:0}section{padding:1rem}</style>'
        '</head><body><iframe title="Interactive artifact" sandbox="" '
        'referrerpolicy="no-referrer" srcdoc="' + html.escape(inner, quote=True) + '"></iframe>'
        + legend + '</body></html>'
    )
