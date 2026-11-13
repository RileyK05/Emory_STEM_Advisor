"""The one look every rendered artifact shares: preview, HTML export and pages.

Emory navy and gold on a quiet surface, readable type, card sections, styled
tables and checklists, and a dark variant. The stylesheet is server-owned and
static, so it sits outside the inert-markup sanitizer, which never passes a
model's own <style>. It is placed in the body because the preview keeps only
the body of the document it is given.
"""

from __future__ import annotations

import html

THEME_CSS = """
.artifact-doc{--ink:#14213D;--muted:#5B6474;--line:#DCE3EE;--surface:#FFFFFF;--page:#F5F7FB;
--navy:#012169;--gold:#F2A900;--gold-ink:#7A5600;--soft:#EEF3FB;--ok:#2E7D32;
font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);
background:var(--page);margin:0;padding:24px 16px 40px}
@media (prefers-color-scheme:dark){.artifact-doc{--ink:#E7ECF5;--muted:#A3ADBF;--line:#2B3550;
--surface:#161D2E;--page:#0E1320;--navy:#9DBBFF;--gold:#F2C14E;--gold-ink:#F2C14E;--soft:#1C2540}}
.artifact-doc *{box-sizing:border-box}
.artifact-doc .doc-wrap{max-width:820px;margin:0 auto}
.artifact-doc .doc-header{border-top:6px solid var(--navy);background:var(--surface);
border-radius:14px;padding:22px 26px 18px;margin-bottom:18px;
box-shadow:0 1px 2px rgba(1,33,105,.08)}
.artifact-doc .doc-kicker{color:var(--gold-ink);font-size:12px;font-weight:700;
letter-spacing:.08em;text-transform:uppercase;margin:0 0 4px}
.artifact-doc h1{font-size:28px;line-height:1.2;margin:0;color:var(--navy)}
.artifact-doc .doc-sub{color:var(--muted);margin:6px 0 0;font-size:14px}
.artifact-doc .card{background:var(--surface);border:1px solid var(--line);border-radius:14px;
padding:18px 22px;margin:0 0 14px}
.artifact-doc h2{font-size:19px;margin:0 0 10px;color:var(--navy);display:flex;
align-items:center;gap:10px}
.artifact-doc h2::before{content:"";width:6px;height:20px;border-radius:3px;background:var(--gold)}
.artifact-doc h3{font-size:16px;margin:16px 0 6px}
.artifact-doc p{margin:0 0 10px}
.artifact-doc ul,.artifact-doc ol{margin:0 0 10px;padding-left:22px}
.artifact-doc li{margin:3px 0}
.artifact-doc ul.checklist{list-style:none;padding-left:0}
.artifact-doc ul.checklist li{display:flex;gap:10px;align-items:flex-start;padding:7px 10px;
border-radius:8px}
.artifact-doc ul.checklist li:nth-child(odd){background:var(--soft)}
.artifact-doc ul.checklist li::before{content:"";flex:none;width:16px;height:16px;margin-top:4px;
border:2px solid var(--navy);border-radius:4px}
.artifact-doc table{width:100%;border-collapse:separate;border-spacing:0;margin:6px 0 12px;
font-size:14px;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.artifact-doc th{background:var(--navy);color:#fff;text-align:left;padding:9px 12px;
font-weight:600}
@media (prefers-color-scheme:dark){.artifact-doc th{color:#0E1320}}
.artifact-doc td{padding:8px 12px;border-top:1px solid var(--line);vertical-align:top}
.artifact-doc tbody tr:nth-child(even) td{background:var(--soft)}
.artifact-doc blockquote,.artifact-doc .quote{margin:0 0 10px;padding:10px 14px;
border-left:4px solid var(--gold);background:var(--soft);border-radius:0 10px 10px 0}
.artifact-doc pre{background:var(--soft);padding:12px;border-radius:10px;overflow:auto}
.artifact-doc figure{margin:4px 0 12px}
.artifact-doc figure h3{margin-top:4px}
.artifact-doc .viz{background:#FFFFFF;border:1px solid #DCE3EE;border-radius:12px;padding:8px;
overflow-x:auto}
.artifact-doc figcaption{color:var(--muted);font-size:13px;margin-top:8px}
.artifact-doc details{margin-top:8px;font-size:14px}
.artifact-doc summary{cursor:pointer;color:var(--navy);font-weight:600}
.artifact-doc .sources{font-size:13px;color:var(--muted)}
.artifact-doc .sources h2{font-size:15px}
.artifact-doc code{background:var(--soft);padding:1px 5px;border-radius:5px}
"""


def themed_page(body: str, *, title: str, kicker: str | None = None,
                subtitle: str | None = None) -> str:
    """A body fragment in the shared look, with its header band."""
    header = ('<header class="doc-header">'
              + (f'<p class="doc-kicker">{html.escape(kicker)}</p>' if kicker else "")
              + f"<h1>{html.escape(title)}</h1>"
              + (f'<p class="doc-sub">{html.escape(subtitle)}</p>' if subtitle else "")
              + "</header>")
    return (f"<style>{THEME_CSS}</style>"
            f'<div class="artifact-doc"><div class="doc-wrap">{header}{body}</div></div>')
