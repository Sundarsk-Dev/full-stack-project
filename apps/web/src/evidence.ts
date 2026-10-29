export function evidenceSlices(text: string, start: number, end: number, quote: string) {
  const points = Array.from(text);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > points.length) return null;
  const matched = points.slice(start, end).join('');
  if (matched !== quote) return null;
  return { before: points.slice(0, start).join(''), quote: matched, after: points.slice(end).join('') };
}
