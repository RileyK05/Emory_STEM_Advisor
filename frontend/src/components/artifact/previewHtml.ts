const DROPPED_ELEMENTS =
  'script,meta,base,link,iframe,frame,object,embed,portal,form,animate,animatemotion,animatetransform,set,discard';
const DROPPED_ATTRIBUTES = [
  'href',
  'src',
  'srcset',
  'action',
  'formaction',
  'target',
  'download',
  'xlink:href',
  'srcdoc',
  // SMIL timing/values can rewrite attributes after sanitization.
  'attributename',
  'attributetype',
  'values',
  'to',
  'from',
  'by',
  'begin',
  'dur',
  'repeatcount',
];

/** Strip active content and navigation from untrusted HTML; the preview is static by design. */
export function sanitizePreviewHtml(html: string) {
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  parsed.querySelectorAll(DROPPED_ELEMENTS).forEach((node) => node.remove());
  for (const element of parsed.body.querySelectorAll('*')) {
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith('on') || DROPPED_ATTRIBUTES.includes(name)) element.removeAttribute(attribute.name);
    }
  }
  return parsed.body.innerHTML;
}

const PREVIEW_POLICY =
  "default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'none'; connect-src 'none'; worker-src 'none'; form-action 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'";

/** A complete, CSP-locked document for the sandboxed preview iframe. */
export function sandboxDocument(html: string) {
  return `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${PREVIEW_POLICY}"></head><body>${sanitizePreviewHtml(html)}</body></html>`;
}
