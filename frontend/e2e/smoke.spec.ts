import { expect, test } from '@playwright/test';

test('mở được app và API khoẻ', async ({ page, request }) => {
  const health = await request.get('/api/v1/health');
  expect(health.ok()).toBeTruthy();
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'Local Translate' })).toBeVisible();
});
