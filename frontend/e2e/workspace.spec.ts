import { expect, test } from '@playwright/test';

test('xoá workspace phải gõ đúng tên truyện (AC-3.9); tab Xuất bản dịch báo số chương (BR-3.15)', async ({ page, request }) => {
  const title = 'Truyện thử xoá workspace';
  const imp = await request.post('/api/v1/imports', {
    multipart: {
      mode: 'multi',
      'files[]': { name: '0001.txt', mimeType: 'text/plain', buffer: Buffer.from(`第1章 测试\n${'他走了很远的路，终于到了。'.repeat(30)}\n`) },
    },
  });
  const { import_id } = await imp.json();
  await expect.poll(async () => (await (await request.get(`/api/v1/imports/${import_id}`)).json()).status).toBe('ready');
  const { id } = await (await request.post('/api/v1/books', {
    data: { title_zh: '删除测试', title_vi: title, import_id, confirm_duplicate: true },
  })).json();

  await page.goto(`/books/${id}/export`);
  await expect(page.getByRole('tab', { name: 'Xuất bản dịch', selected: true })).toBeVisible();
  await expect(page.getByText('Sẽ xuất 0 chương, bỏ qua 1')).toBeVisible();
  await expect(page.getByRole('button', { name: '⤓ Xuất file' })).toBeDisabled();

  await page.goto(`/books/${id}/settings`);
  const del = page.getByRole('button', { name: 'Xoá workspace' });
  const confirm = page.getByLabel(/Gõ lại tên truyện/);
  await expect(del).toBeDisabled();
  await confirm.fill('Truyện thử xoá');
  await expect(del).toBeDisabled();
  await confirm.fill(title);
  await expect(del).toBeEnabled();
  await del.click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('heading', { name: 'Truyện của tôi' })).toBeVisible();
  await expect(page.getByText(title)).toHaveCount(0);
  expect((await request.get(`/api/v1/books/${id}`)).status()).toBe(404);
});
