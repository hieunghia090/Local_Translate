import { expect, test, type Page } from '@playwright/test';

const PAGE_MS = 1000; // NFR-2: tải trang
const QUERY_MS = 200; // NFR-2: lọc, tìm. Đo thời gian request trong trình duyệt, không tính debounce của ô tìm.
const BIG = 'Truyện đo hiệu năng 2000 chương';

async function requestMs(page: Page, action: () => Promise<unknown>, match: (url: URL) => boolean): Promise<number> {
  const waiting = page.waitForResponse((r) => match(new URL(r.url())));
  await action();
  const resp = await waiting;
  await resp.finished();
  return resp.request().timing().responseEnd;
}

test('NFR-2: thư viện 50 truyện, workspace 2.000 chương tải dưới 1 giây; lọc và tìm dưới 200 ms', async ({ page, request }) => {
  const found = await (await request.get('/api/v1/books', { params: { q: BIG } })).json();
  test.skip(found.items.length === 0, 'chưa có dữ liệu hiệu năng (E2E_SEED_PERF=0?)');
  const id: string = found.items[0].id;

  await page.goto('/'); // lần đầu: trình duyệt nạp và biên dịch bundle
  await expect(page.getByText(BIG)).toBeVisible();

  let t0 = Date.now();
  await page.goto('/');
  await expect(page.getByText(BIG)).toBeVisible();
  await expect(page.getByText('Truyện đo hiệu năng 049')).toBeVisible();
  const libraryMs = Date.now() - t0;
  const searchMs = await requestMs(page, () => page.getByLabel('Tìm truyện').fill('hiệu năng 049'),
    (u) => u.pathname === '/api/v1/books' && u.searchParams.get('q') === 'hiệu năng 049');

  t0 = Date.now();
  await page.goto(`/books/${id}`);
  await expect(page.getByLabel('Chọn chương 50', { exact: true })).toBeVisible();
  const workspaceMs = Date.now() - t0;

  const filterMs = await requestMs(page, () => page.getByRole('button', { name: /^Lỗi · / }).click(),
    (u) => u.pathname.endsWith('/chapters') && u.searchParams.get('status') === 'error');
  await expect(page.getByLabel('Chọn chương 9', { exact: true })).toBeVisible();
  const chapterSearchMs = await requestMs(page, () => page.getByLabel('Tìm chương').fill('1999'),
    (u) => u.pathname.endsWith('/chapters') && u.searchParams.get('q') === '1999');
  await expect(page.getByLabel('Chọn chương 1999', { exact: true })).toBeVisible();

  console.log(`NFR-2 e2e · thư viện ${libraryMs} ms · workspace ${workspaceMs} ms · tìm truyện ${searchMs.toFixed(0)} ms`
    + ` · lọc Lỗi ${filterMs.toFixed(0)} ms · tìm chương ${chapterSearchMs.toFixed(0)} ms`);
  expect(libraryMs).toBeLessThan(PAGE_MS);
  expect(workspaceMs).toBeLessThan(PAGE_MS);
  for (const ms of [searchMs, filterMs, chapterSearchMs]) expect(ms).toBeLessThan(QUERY_MS);
});
