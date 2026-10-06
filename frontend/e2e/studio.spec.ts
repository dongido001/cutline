import { test, expect } from '@playwright/test'

test('shows an empty studio until a project is created', async ({ page }) => {
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
  await page.route('**/api/image-providers', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/video-provider', (route) =>
    route.fulfill({
      json: {
        id: 'mock',
        label: 'Prototype',
        configured: true,
        available: true,
        model: 'local',
        detail: '',
      },
    }),
  )
  await page.route('**/api/projects', (route) => route.fulfill({ json: [] }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Script review' })).toBeVisible()
  await expect(page.getByText('Create a project')).toBeVisible()
  await expect(page.getByText('3 open suggestions')).toHaveCount(0)
})
