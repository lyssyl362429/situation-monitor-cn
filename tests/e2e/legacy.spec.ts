import { test, expect } from '@playwright/test';

test.describe('Situation Monitor - Legacy App', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/legacy.html');
		await page.waitForSelector('[data-testid="app-status"]');
	});

	test('legacy app loads correctly', async ({ page }) => {
		await expect(page).toHaveTitle('Situation Monitor');
		await expect(page.locator('[data-testid="app-title"]')).toHaveText('Situation Monitor');
		await expect(page.locator('[data-testid="app-status"]')).toBeVisible();
		await expect(page.locator('[data-testid="panel-politics"]')).toBeVisible();
		await expect(page.locator('[data-testid="panel-tech"]')).toBeVisible();
		await expect(page.locator('[data-testid="panel-finance"]')).toBeVisible();
	});

	test('legacy app can refresh', async ({ page }) => {
		await expect(page.locator('[data-testid="refresh-btn"]')).toBeVisible();
	});
});
