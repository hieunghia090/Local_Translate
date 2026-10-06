import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 400, height: 800 } });

async function noHorizontalScroll(page: import('@playwright/test').Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test('các màn chính dùng được ở 400 px (NFR-5)', async ({ page, request }) => {
  const imp = await request.post('/api/v1/imports', {
    multipart: {
      mode: 'multi',
      'files[]': { name: '0001.txt', mimeType: 'text/plain', buffer: Buffer.from(`第1章 测试\n${'他走了很远的路，终于到了。'.repeat(30)}\n`) },
    },
  });
  const { import_id } = await imp.json();
  for (let i = 0; i < 50; i++) {
    const v = await (await request.get(`/api/v1/imports/${import_id}`)).json();
    if (v.status !== 'parsing') break;
    await new Promise((r) => setTimeout(r, 100));
  }
  const created = await request.post('/api/v1/books', {
    data: { title_zh: '窄屏测试', title_vi: 'Một tên truyện khá dài để thử chiều rộng màn hình hẹp', import_id, confirm_duplicate: true },
  });
  const { id } = await created.json();

  for (const url of ['/', '/books/new', `/books/${id}`, `/books/${id}/queue`, `/books/${id}/logs`, `/books/${id}/settings`, `/books/${id}/chapters/1`]) {
    await page.goto(url);
    await page.waitForLoadState('networkidle');
    await noHorizontalScroll(page);
  }
});
