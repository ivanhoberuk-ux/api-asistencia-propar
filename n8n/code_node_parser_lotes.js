const REQUIRED_COLUMNS = [
  'Manzana',
  'Lote',
  'Norte (medida en metros)',
  'Linda Norte (quién linda al norte)',
  'Sur (medida en metros)',
  'Linda Sur (quién linda al sur)',
  'Este (medida en metros)',
  'Linda Este (quién linda al este)',
  'Oeste (medida en metros)',
  'Linda Oeste (quién linda al oeste)',
  'Superficie m2',
  'Comentarios',
];

function normalizeText(txt = '') {
  return txt
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function parseNumber(raw = '') {
  if (!raw) return '';
  const cleaned = raw.replace(/[^\d,.-]/g, '').replace(/\.(?=\d{3}(\D|$))/g, '');
  if (!cleaned) return '';
  if (cleaned.includes(',') && cleaned.includes('.')) return cleaned.replace(/\./g, '').replace(',', '.');
  if (cleaned.includes(',')) return cleaned.replace(',', '.');
  return cleaned;
}

function toLineText(result) {
  const pages = result?.analyzeResult?.pages || [];
  return pages
    .map((p) => (p.lines || []).map((l) => l.content || '').join('\n'))
    .join('\n');
}

function splitByRegexKeepingTag(text, regex) {
  const out = [];
  const matches = [...text.matchAll(regex)];
  if (!matches.length) return out;

  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].index;
    const end = i + 1 < matches.length ? matches[i + 1].index : text.length;
    out.push(text.slice(start, end).trim());
  }
  return out;
}

function extractManzana(block) {
  const m = block.match(/MANZANA\s+([A-Z0-9IVXLCDM-]+)/i);
  return m ? m[1].toUpperCase() : '';
}

function extractLote(block) {
  const m = block.match(/LOTE\s*([A-Z0-9-]+)/i);
  return m ? m[1].toUpperCase() : '';
}

function extractSuperficie(block) {
  const m = block.match(/SUPERFICIE\s*[:\-]?\s*([0-9][0-9.,]*)\s*(m2|m\.2|m²|metros?\s+cuadrados?)/i);
  return m ? parseNumber(m[1]) : '';
}

function getOrientationData(block, orient) {
  const txt = normalizeText(block);
  const upper = txt.toUpperCase();

  const mMeasure1 = upper.match(new RegExp(`(?:AL|DEL|SOBRE|CONTRAFRENTE\\s+AL|COSTADO\\s+AL)?\\s*${orient}[^\\n.]{0,120}?MIDE\\s*([0-9][0-9.,]*)\\s*(?:M|MTS|MTS\\.|METROS?)`, 'i'));
  const mMeasure2 = upper.match(new RegExp(`MIDE\\s*([0-9][0-9.,]*)\\s*(?:M|MTS|MTS\\.|METROS?)[^\\n.]{0,120}?${orient}`, 'i'));
  const measure = parseNumber((mMeasure1 && mMeasure1[1]) || (mMeasure2 && mMeasure2[1]) || '');

  const l1 = txt.match(new RegExp(`${orient}[^\\n.]{0,180}?LINDA\\s+CON\\s+([^.;\\n]+)`, 'i'));
  const l2 = txt.match(new RegExp(`LINDA\\s+CON\\s+([^.;\\n]+)[^\\n.]{0,120}?${orient}`, 'i'));
  const lindero = (l1 && l1[1]) || (l2 && l2[1]) || '';

  return {
    measure,
    lindero: lindero.trim(),
  };
}

function ensureColumns(row) {
  const ordered = {};
  for (const col of REQUIRED_COLUMNS) {
    ordered[col] = row[col] ?? '';
  }
  return ordered;
}

const input = items[0].json;
const fullText = toLineText(input);
const normalizedFullText = normalizeText(fullText);

const manzanaBlocks = splitByRegexKeepingTag(normalizedFullText, /MANZANA\s+[A-Z0-9IVXLCDM-]+/gi);

const rows = [];
const seen = new Set();

if (!manzanaBlocks.length) {
  const row = ensureColumns({
    Comentarios: 'Manzana no detectada; no se pudo segmentar el documento en lotes',
  });
  rows.push(row);
} else {
  for (const manzanaBlock of manzanaBlocks) {
    const manzana = extractManzana(manzanaBlock);
    const loteBlocks = splitByRegexKeepingTag(manzanaBlock, /LOTE\s*[A-Z0-9-]+/gi);

    if (!loteBlocks.length) {
      rows.push(ensureColumns({
        Manzana: manzana,
        Comentarios: 'No se detectaron lotes dentro de la manzana',
      }));
      continue;
    }

    for (const loteBlock of loteBlocks) {
      const lote = extractLote(loteBlock);
      const norte = getOrientationData(loteBlock, 'NORTE');
      const sur = getOrientationData(loteBlock, 'SUR');
      const este = getOrientationData(loteBlock, 'ESTE');
      const oeste = getOrientationData(loteBlock, 'OESTE');
      const superficie = extractSuperficie(loteBlock);

      const comments = [];
      if (!manzana) comments.push('Manzana no detectada');
      if (!lote) comments.push('Lote no detectado');
      if (!norte.measure) comments.push('Norte no especificado');
      if (!norte.lindero) comments.push('Linda Norte no detectada');
      if (!sur.measure) comments.push('Sur no especificado');
      if (!sur.lindero) comments.push('Linda Sur no detectada');
      if (!este.measure) comments.push('Este no especificado');
      if (!este.lindero) comments.push('Linda Este no detectada');
      if (!oeste.measure) comments.push('Oeste no especificado');
      if (!oeste.lindero) comments.push('Linda Oeste no detectada');
      if (!superficie) comments.push('Superficie no detectada');

      const dedupeKey = `${manzana}::${lote}`;
      if (manzana && lote && seen.has(dedupeKey)) continue;
      if (manzana && lote) seen.add(dedupeKey);

      rows.push(ensureColumns({
        Manzana: manzana,
        Lote: lote,
        'Norte (medida en metros)': norte.measure,
        'Linda Norte (quién linda al norte)': norte.lindero,
        'Sur (medida en metros)': sur.measure,
        'Linda Sur (quién linda al sur)': sur.lindero,
        'Este (medida en metros)': este.measure,
        'Linda Este (quién linda al este)': este.lindero,
        'Oeste (medida en metros)': oeste.measure,
        'Linda Oeste (quién linda al oeste)': oeste.lindero,
        'Superficie m2': superficie,
        Comentarios: comments.join('; '),
      }));
    }
  }
}

return rows.map((r) => ({ json: r }));
