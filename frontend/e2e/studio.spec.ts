import { test, expect } from '@playwright/test'

test('shows the screenplay review workspace', async ({ page }) => {
  await page.route('**/api/providers', (route) =>
    route.fulfill({
      json: [
        {
          id: 'mock',
          label: 'Prototype parser',
          configured: true,
          available: true,
          model: 'deterministic-v1',
          detail: 'No credits required',
        },
      ],
    }),
  )
  await page.route('**/api/projects', (route) => route.fulfill({ json: [] }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Script review' })).toBeVisible()
  await expect(page.getByText('3 open suggestions')).toBeVisible()
  await page.getByRole('button', { name: 'Accept' }).first().click()
  await expect(page.getByText('2 open suggestions')).toBeVisible()
})
