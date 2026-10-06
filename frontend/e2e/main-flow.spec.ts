import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SOURCE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../大宋有种--35466');
// Chương 1: idx 0 tiêu đề, 1 "=====", 2 "Nguồn: …", 3 dòng trống, 4 câu chữ Hán đầu tiên.
const EDIT_IDX = 4;

test.skip(!fs.existsSync(SOURCE), 'thiếu thư mục 大宋有种--35466');

test('tạo truyện từ thư mục 大宋有种, dịch 3 chương, sửa 1 câu, đánh dấu đã soát', async ({ page }) => {
  test.setTimeout(240_000);
  const files = fs.readdirSync(SOURCE).sort().map((f) => path.join(SOURCE, f));

  await page.goto('/');
  // Không giả định thư viện trống: spec khác có thể đã tạo truyện trên cùng DB e2e.
  await expect(page.getByRole('heading', { name: 'Truyện của tôi' })).toBeVisible();
  await expect(page.getByText('Đại Tống Hữu Chủng')).toHaveCount(0);
  await page.getByRole('link', { name: '＋ Tạo truyện' }).first().click();

  // Review Focus 3: một request duy nhất cho cả thư mục
  const uploads: string[] = [];
  page.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/api/v1/imports')) uploads.push(r.url()); });
  await page.getByRole('button', { name: 'Nhiều file, mỗi file 1 chương' }).click();
  await page.getByLabel('Chọn file nguồn').setInputFiles(files);
  await expect(page.getByText(/Phát hiện \d+ chương/)).toBeVisible({ timeout: 90_000 });
  expect(uploads).toHaveLength(1);
  await page.getByLabel('Tên gốc (Trung) *').fill('大宋有种');
  await page.getByLabel('Tên tiếng Việt').fill('Đại Tống Hữu Chủng');
  await page.getByRole('button', { name: 'Tạo workspace' }).click();
  await expect(page).toHaveURL(/\/books\/[0-9a-f-]+/, { timeout: 60_000 });
  await expect(page.getByRole('heading', { name: 'Đại Tống Hữu Chủng' })).toBeVisible();

  for (const n of [1, 2, 3]) await page.getByLabel(`Chọn chương ${n}`, { exact: true }).check();
  await page.getByRole('button', { name: '▶ Dịch', exact: true }).click();
  const rowOf = (n: number) => page.getByRole('row').filter({ has: page.getByLabel(`Chọn chương ${n}`, { exact: true }) });
  for (const n of [1, 2, 3]) {
    await expect(rowOf(n).getByText(/^(Đã dịch|Cần soát)$/)).toBeVisible({ timeout: 150_000 });
  }
  await expect(rowOf(1).getByText('HachimiMT-60')).toBeVisible();

  await rowOf(1).getByRole('link').first().click();
  await expect(page).toHaveURL(/\/chapters\/1$/);
  const cell = page.getByLabel(`Bản dịch câu ${EDIT_IDX}`, { exact: true });
  await cell.click();
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
  await page.keyboard.type('Câu này tôi đã sửa tay.');
  const saved = page.waitForResponse((r) => r.url().includes('/api/v1/segments/') && r.request().method() === 'PATCH');
  await page.getByText('Lịch sử').click(); // rời ô → lưu
  expect((await saved).ok()).toBeTruthy();

  await page.reload();
  await expect(page.getByLabel(`Bản dịch câu ${EDIT_IDX}`, { exact: true })).toHaveText('Câu này tôi đã sửa tay.');
  await expect(page.getByTitle('Đã sửa tay').first()).toBeVisible();
  await expect(page.getByText('Sửa tay').first()).toBeVisible(); // lịch sử có revision sửa tay (AC-6.2)

  await page.getByRole('button', { name: '✓ Đánh dấu đã soát' }).click();
  await expect(page.locator('.chip', { hasText: 'Đã soát' })).toBeVisible();
});
