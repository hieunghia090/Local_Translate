import { expect, test } from '@playwright/test';

const TITLE = 'Truyện so sánh e2e';

test('So sánh bản Hachimi và AI: đếm, tô, lọc, dùng bản Hachimi, nhớ chế độ (AC-6.13 → AC-6.17)', async ({ page, request }) => {
  const found = await (await request.get('/api/v1/books', { params: { q: TITLE } })).json();
  test.skip(found.items.length === 0, 'chưa có dữ liệu so sánh (E2E_SEED_COMPARE=0?)');
  const id: string = found.items[0].id;

  await page.goto(`/books/${id}`);
  await expect(page.getByTitle('Có bản Hachimi và AI · 2/4 câu khác nhau')).toBeVisible(); // Task 9

  await page.goto(`/books/${id}/chapters/1`);
  const views = page.getByRole('group', { name: 'Chế độ xem' });
  await views.getByRole('button', { name: 'So sánh' }).click();
  await expect(page.getByText('2/4 câu khác nhau')).toBeVisible();
  await expect(page.getByRole('group', { name: 'Hachimi câu 1' }).locator('.d-mt')).toHaveText(['Khi', 'chuẩn bị']);
  await expect(page.getByRole('group', { name: 'AI câu 1' }).locator('.d-ai')).toHaveText(['Lúc', 'sắp']);

  await page.getByLabel('Chỉ câu khác nhau').check();
  await expect(page.locator('.cmp-row[data-row]')).toHaveCount(2);
  await page.getByRole('button', { name: 'Dùng bản Hachimi cho câu 3' }).click();
  await expect(page.getByRole('group', { name: 'Hachimi câu 3' }).getByText('✓ Đang dùng')).toBeVisible();

  await page.reload();
  await expect(views.getByRole('button', { name: 'So sánh' })).toHaveAttribute('aria-pressed', 'true');
  await views.getByRole('button', { name: 'Bản chính' }).click();
  await expect(page.getByLabel('Bản dịch câu 3')).toHaveText('Nàng đi rồi.');
  await expect(page.locator('[data-row="3"] .edited-mark')).toBeVisible();

  await page.setViewportSize({ width: 400, height: 800 }); // NFR-5
  await views.getByRole('button', { name: 'So sánh' }).click();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
