import { expect, test } from '@playwright/test';

const BODY = '赵楷走了很远的路，终于到了东京。'.repeat(15);

test('thêm term từ chữ bôi đen, dịch lại và thấy tô sáng (AC-6.5)', async ({ page, request }) => {
  test.setTimeout(180_000);
  const imp = await request.post('/api/v1/imports', {
    multipart: { mode: 'multi', 'files[]': { name: '0001.txt', mimeType: 'text/plain', buffer: Buffer.from(`第1章 赵楷\n${BODY}\n赵楷笑了。\n`) } },
  });
  const { import_id } = await imp.json();
  await expect.poll(async () => (await (await request.get(`/api/v1/imports/${import_id}`)).json()).status).toBe('ready');
  const { id } = await (await request.post('/api/v1/books', {
    data: { title_zh: '词汇测试', title_vi: 'Thử glossary', import_id, confirm_duplicate: true },
  })).json();
  await request.post(`/api/v1/books/${id}/chapters/bulk`, { data: { action: 'translate', filter: { status: ['todo'] } } });
  try {
    await page.goto(`/books/${id}/chapters/1`);
    await expect(page.locator('.chip', { hasText: /Đã dịch|Cần soát/ })).toBeVisible({ timeout: 120_000 });

    const src = page.locator('.cmp-row .src').filter({ hasText: '赵楷笑了' }).first();
    await src.evaluate((el) => {
      const text = el.firstChild ?? el;
      const range = document.createRange();
      range.setStart(text, 0);
      range.setEnd(text, 2);
      const sel = window.getSelection()!;
      sel.removeAllRanges();
      sel.addRange(range);
      el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    });
    await page.getByRole('button', { name: '＋ Thêm vào glossary' }).click();
    await expect(page.getByLabel('Nguồn Trung')).toHaveValue('赵楷');
    await page.getByLabel('Đích Việt').fill('Triệu Khải');
    await page.getByRole('button', { name: 'Lưu thuật ngữ' }).click();
    // Lưu xong, app hỏi "Dịch lại chương này để áp thuật ngữ mới?" (BR-6.6): bấm nút trong hộp thoại.
    await page.getByRole('dialog', { name: /Dịch lại chương này/ }).getByRole('button', { name: 'Dịch lại chương' }).click();
    const keep = page.getByRole('button', { name: 'Giữ câu đã sửa tay' });
    if (await keep.isVisible().catch(() => false)) await keep.click();
    await expect(page.locator('.cmp-row .dst mark', { hasText: 'Triệu Khải' }).first()).toBeVisible({ timeout: 120_000 });
    expect(await page.locator('.cmp-row .dst mark', { hasText: 'Triệu Khải' }).count()).toBeGreaterThanOrEqual(2);
  } finally {
    // main-flow.spec.ts đòi thư viện rỗng: dọn truyện của test này.
    await request.delete(`/api/v1/books/${id}`, { params: { confirm: 'Thử glossary' } });
  }
});
