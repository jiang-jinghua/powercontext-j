// Document parsing & chunking (txt / md / pdf), Chinese & English.
// Paragraph-aware sliding-window chunks with overlap.

export function splitIntoChunks(text, { targetSize = 400, overlap = 60 } = {}) {
  // Normalize line endings, split into paragraphs (blank-line separated)
  const normalized = text.replace(/\r\n?/g, '\n');
  const paragraphs = normalized
    .split(/\n\s*\n|\n/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);

  const chunks = [];
  let buf = '';

  const flush = () => {
    const t = buf.trim();
    if (t.length > 0) chunks.push(t);
    buf = '';
  };

  for (const para of paragraphs) {
    if (para.length > targetSize) {
      // long paragraph -> sliding window with overlap
      flush();
      let i = 0;
      while (i < para.length) {
        const end = Math.min(i + targetSize, para.length);
        chunks.push(para.slice(i, end).trim());
        if (end >= para.length) break;
        i = end - overlap;
      }
      continue;
    }
    if ((buf + '\n' + para).trim().length > targetSize) {
      // keep a small overlap from previous buffer
      const tail = buf.slice(Math.max(0, buf.length - overlap));
      flush();
      buf = (tail.trim() ? tail.trim() + '\n' : '') + para;
    } else {
      buf = buf ? buf + '\n' + para : para;
    }
  }
  flush();
  return chunks.filter((c) => c.length > 0);
}

/** Extract plain text from a File (txt/md/pdf) in the browser. */
export async function readDocumentText(file) {
  const name = file.name.toLowerCase();
  if (name.endsWith('.pdf')) {
    const pdfjs = await import('pdfjs-dist');
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      'pdfjs-dist/build/pdf.worker.min.mjs',
      import.meta.url
    ).toString();
    const buf = await file.arrayBuffer();
    const doc = await pdfjs.getDocument({ data: buf }).promise;
    const pages = [];
    for (let p = 1; p <= doc.numPages; p++) {
      const page = await doc.getPage(p);
      const content = await page.getTextContent();
      pages.push(content.items.map((it) => it.str).join(' '));
    }
    return pages.join('\n\n');
  }
  // txt / md / any plain text
  return await file.text();
}

/** Stable short id for a document based on name + size + simple content hash. */
export function makeDocId(fileName, text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `${fileName.replace(/[^\w.-]+/g, '_')}-${h.toString(16)}`;
}
