/**
 * So sánh bản Hachimi và bản AI theo từ (spec 06 mục 4a). Tiếng Việt tách âm tiết theo khoảng trắng;
 * dấu câu là token riêng. LCS tự viết, không dùng thư viện ngoài.
 */
export interface DiffPart { text: string; changed: boolean; }

/** Số ô tối đa của bảng LCS (sau khi cắt phần đầu và cuối giống nhau). Vượt thì tô cả phần giữa. */
export const MAX_CELLS = 250_000;
// Cùng tập ký tự với WS_CHARS ở backend (app/core/compare.py).
const WS_RUN = /[ \t\n\r\f\v 　]+/g;
const WS_ONLY = /^[ \t\n\r\f\v 　]+$/;
const TOKEN = /[\p{L}\p{M}\p{N}]+|[ \t\n\r\f\v 　]+|[^\p{L}\p{M}\p{N} \t\n\r\f\v 　]/gu;

/** Giống norm_cmp của server: NFC, gộp khoảng trắng, bỏ dấu cách hai đầu (BR-6.11). */
export function normCmp(text: string): string {
  return text.normalize('NFC').replace(WS_RUN, ' ').replace(/^ | $/g, '');
}

export function tokenize(text: string): string[] {
  return text.match(TOKEN) ?? [];
}

/** a, b: các token từ (đã NFC). Trả cờ "không thuộc LCS" cho từng token. */
function changedWords(a: string[], b: string[]): [boolean[], boolean[]] {
  const n = a.length;
  const m = b.length;
  let p = 0;
  while (p < n && p < m && a[p] === b[p]) p++;
  let s = 0;
  while (s < n - p && s < m - p && a[n - 1 - s] === b[m - 1 - s]) s++;
  const ca = new Array<boolean>(n).fill(false);
  const cb = new Array<boolean>(m).fill(false);
  for (let i = p; i < n - s; i++) ca[i] = true;
  for (let j = p; j < m - s; j++) cb[j] = true;
  const na = n - p - s;
  const nb = m - p - s;
  if (na === 0 || nb === 0 || na * nb > MAX_CELLS) return [ca, cb];
  const w = nb + 1;
  const dp = new Uint32Array((na + 1) * w); // dp[i][j] = LCS của a[p+i..] và b[p+j..]
  for (let i = na - 1; i >= 0; i--) {
    for (let j = nb - 1; j >= 0; j--) {
      dp[i * w + j] = a[p + i] === b[p + j] ? dp[(i + 1) * w + j + 1] + 1 : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
    }
  }
  let i = 0;
  let j = 0;
  while (i < na && j < nb) {
    if (a[p + i] === b[p + j]) { ca[p + i] = false; cb[p + j] = false; i++; j++; }
    else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) i++;
    else j++;
  }
  return [ca, cb];
}

function toParts(tokens: string[], words: number[], changed: boolean[]): DiffPart[] {
  const flag = new Array<boolean>(tokens.length).fill(false);
  words.forEach((t, k) => { flag[t] = changed[k]; });
  // Một chuỗi khoảng trắng là một token, nên hai bên nó là token từ (hoặc đầu / cuối câu).
  for (let t = 0; t < tokens.length; t++) {
    if (WS_ONLY.test(tokens[t])) flag[t] = t > 0 && t + 1 < tokens.length && flag[t - 1] && flag[t + 1];
  }
  const out: DiffPart[] = [];
  tokens.forEach((text, t) => {
    const last = out[out.length - 1];
    if (last && last.changed === flag[t]) last.text += text;
    else out.push({ text, changed: flag[t] });
  });
  return out;
}

export function diffTokens(a: string, b: string): { a: DiffPart[]; b: DiffPart[] } {
  const ta = tokenize(a);
  const tb = tokenize(b);
  const wa = ta.flatMap((t, i) => (WS_ONLY.test(t) ? [] : [i]));
  const wb = tb.flatMap((t, i) => (WS_ONLY.test(t) ? [] : [i]));
  const [ca, cb] = changedWords(wa.map((i) => ta[i].normalize('NFC')), wb.map((i) => tb[i].normalize('NFC')));
  return { a: toParts(ta, wa, ca), b: toParts(tb, wb, cb) };
}
