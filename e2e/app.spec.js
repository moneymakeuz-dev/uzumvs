import { expect, test } from '@playwright/test';
import { mkdir, readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const mailDir = fileURLToPath(new URL('../var/e2e/mail/', import.meta.url));
const screenDir = fileURLToPath(new URL('../test-results/screens/', import.meta.url));
const photo = fileURLToPath(new URL('./fixtures/mug.png', import.meta.url));
const password = 'e2e-local-password-42';

function decodeMail(raw) {
  return raw.replace(/=\r?\n/g, '').replace(/=([0-9A-F]{2})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
}

async function verificationLink(email) {
  let link = '';
  await expect.poll(async () => {
    for (const name of await readdir(mailDir).catch(() => [])) {
      const text = decodeMail(await readFile(mailDir + name, 'utf8'));
      const match = text.includes(`To: ${email}`) && text.match(/https?:\/\/\S+\/auth\/verify-email\?token=[\w-]+/);
      if (match) link = match[0];
    }
    return link;
  }, { timeout: 15_000 }).not.toBe('');
  return new URL(link).pathname + new URL(link).search;
}

async function confirmDialog(page) {
  await page.locator('#confirm-dialog button[value="confirm"]').click();
}

async function noHorizontalOverflow(page) {
  const result = await page.evaluate(() => {
    const overflow = document.documentElement.scrollWidth - window.innerWidth;
    const widest = [...document.querySelectorAll('body *')].filter(node => node.getBoundingClientRect().right > window.innerWidth + 1)
      .map(node => `${node.tagName.toLowerCase()}.${[...node.classList].join('.')}#${node.id}`).slice(0, 5);
    return { overflow, widest };
  });
  expect(result.overflow, `${page.url()} @ ${page.viewportSize().width}px: ${result.widest.join(', ')}`).toBeLessThanOrEqual(1);
}

async function screenshot(page, name) {
  await mkdir(screenDir, { recursive: true });
  await page.screenshot({ path: `${screenDir}${test.info().project.name}-${name}.png`, fullPage: true });
}

async function registerAndLogin(page) {
  const email = `seller-${test.info().project.name}-${Date.now()}@example.com`;
  await page.goto('/auth/register');
  await page.getByLabel('Email manzil').fill(email);
  await page.getByLabel('Parol', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Hisob yaratish' }).click();
  await expect(page.getByText('tasdiqlash havolasi yuboriladi')).toBeVisible();
  await page.goto(await verificationLink(email));
  await page.getByRole('button', { name: 'Emailni tasdiqlash' }).click();
  await expect(page.getByText('Email tasdiqlandi')).toBeVisible();
  await page.getByLabel('Email manzil').fill(email);
  await page.getByLabel('Parol', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Kirish' }).click();
  await expect(page).toHaveURL(/\/cards\/new$/);
  return email;
}

async function createCard(page) {
  const submit = page.getByRole('button', { name: 'Kartochka yaratish' });
  await expect(submit).toBeDisabled();
  await page.locator('#images').setInputFiles(photo);
  await page.getByLabel('Tasdiqlangan faktlar').fill('Oq keramik chashka, 350 ml.');
  await page.locator('#consent').check();
  await screenshot(page, 'new-card');
  await submit.click();
  await expect(page).toHaveURL(/\/cards\/[0-9a-f-]{36}$/);
  await expect(page.locator('#card-heading')).toHaveText('Sinov mahsulot kartochkasi', { timeout: 30_000 });
}

test('seller completes the card lifecycle', async ({ page, context }) => {
  await registerAndLogin(page);
  await noHorizontalOverflow(page);
  await createCard(page);
  await expect(page.getByText('3 ta maydonni tekshiring')).toBeVisible();
  await screenshot(page, 'editor-review');
  for (const box of await page.getByLabel('Tekshirdim').all()) await box.check();
  await page.locator('#field-title').fill('Oq keramik chashka 350 ml');
  await page.getByRole('button', { name: 'Saqlash' }).click();
  await expect(page.locator('#card-version')).toHaveText('v3');
  await expect(page.getByText("Tekshiruv talab qiladigan maydon yo'q.")).toBeVisible();

  const other = await context.newPage();
  await other.goto(page.url());
  await other.locator('#field-short_description').fill('Boshqa oynadagi qisqa tavsif.');
  await other.getByRole('button', { name: 'Saqlash' }).click();
  await expect(other.locator('#card-version')).toHaveText('v4');
  await other.close();
  await page.locator('#field-description').fill('Oq keramik chashka. Hajmi 350 ml.');
  await page.getByRole('button', { name: 'Saqlash' }).click();
  await expect(page.getByText("Kartochka boshqa oynada o'zgargan")).toBeVisible();
  await expect(page.locator('#field-description')).toHaveValue('Oq keramik chashka. Hajmi 350 ml.');
  await page.getByRole('button', { name: "Mening o'zgarishlarimni saqlash" }).click();
  await expect(page.locator('#card-version')).toHaveText('v5');
  await expect(page.getByText('1 ta maydonni tekshiring')).toBeVisible();
  await page.getByLabel('Tekshirdim').check();
  await page.getByRole('button', { name: 'Saqlash' }).click();
  await expect(page.locator('#card-version')).toHaveText('v6');

  await page.getByRole('button', { name: 'Sarlavha: qayta yaratish' }).click();
  await confirmDialog(page);
  await expect(page.locator('#card-heading')).toHaveText('Oq keramik chashka 350 ml (sinov)', { timeout: 30_000 });
  await expect(page.locator('#field-description')).toHaveValue('Oq keramik chashka. Hajmi 350 ml.');
  await page.getByRole('tab', { name: 'Русский' }).click();
  await expect(page.locator('#field-title')).toHaveValue('Тестовая карточка товара');

  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'XLSX' }).click()]);
  expect(download.suggestedFilename()).toBe('karto-cards.xlsx');
  await noHorizontalOverflow(page);
  await screenshot(page, 'editor-ready');

  await page.goto('/cards');
  await page.getByLabel('Kartochka qidirish').fill('keramik');
  await expect(page.locator('.product-card')).toHaveCount(1);
  await page.locator('[data-select-card]').check();
  await expect(page.getByText('1 ta tanlangan')).toBeVisible();
  const [csv] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'CSV' }).click()]);
  expect(csv.suggestedFilename()).toBe('karto-cards.csv');
  await screenshot(page, 'history');

  await page.locator('.product-card a').first().click();
  await page.getByRole('button', { name: "Kartochkani o'chirish" }).click();
  await confirmDialog(page);
  await expect(page).toHaveURL(/\/cards$/);
  await expect(page.getByText("Hali kartochka yo'q")).toBeVisible();
  await page.goto('/profile');
  await expect(page.locator('.quota-stat strong').first()).toHaveText(/19\s*\/\s*20/);
  await expect(page.locator('.quota-stat strong').nth(1)).toHaveText(/39\s*\/\s*40/);
  await screenshot(page, 'profile');
});

test('layouts fit required widths', async ({ page }) => {
  test.skip(test.info().project.name !== 'desktop', 'Width matrix runs once');
  await page.goto('/auth/login');
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await noHorizontalOverflow(page);
    if ([390, 1440].includes(width)) await screenshot(page, `login-${width}`);
  }
  await registerAndLogin(page);
  await createCard(page);
  const cardUrl = page.url();
  for (const path of ['/cards/new', '/cards', '/profile', new URL(cardUrl).pathname]) {
    await page.goto(path);
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await noHorizontalOverflow(page);
      if (path === new URL(cardUrl).pathname) await screenshot(page, `card-${width}`);
    }
  }
});

test('unauthenticated users are redirected and guarded', async ({ page, request }) => {
  await page.goto('/cards');
  await expect(page).toHaveURL(/\/auth\/login$/);
  const response = await request.post('/api/exports', { data: { card_ids: ['00000000-0000-0000-0000-000000000000'], format: 'csv' } });
  expect([401, 403]).toContain(response.status());
});
