import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const [templatePath, shopId] = process.argv.slice(2);
const email = process.env.UZUM_SELLER_EMAIL;
const password = process.env.UZUM_SELLER_PASSWORD;
const profilePath = process.env.UZUM_BROWSER_PROFILE;

function result(status, code) {
  process.stdout.write(`${JSON.stringify({ status, code })}\n`);
}

if (!templatePath || !shopId || !email || !password || !profilePath) {
  result('failed', 'configuration');
  process.exit(2);
}

let context;
try {
  await mkdir(profilePath, { recursive: true });
  context = await chromium.launchPersistentContext(path.resolve(profilePath), {
    headless: false,
    acceptDownloads: false,
    viewport: { width: 1440, height: 1000 },
  });
  const page = context.pages()[0] ?? await context.newPage();
  const productsUrl = `https://seller.uzum.uz/seller/${encodeURIComponent(shopId)}/products/all`;
  await page.goto(productsUrl, { waitUntil: 'domcontentloaded', timeout: 45_000 });

  if (page.url().includes('/showcaptcha')) {
    await page.waitForURL(url => !url.href.includes('/showcaptcha'), { timeout: 180_000 });
  }
  if (page.url().includes('/seller/signin')) {
    await page.getByRole('textbox').first().fill(email);
    await page.locator('input[type="password"]').fill(password);
    await page.getByRole('button', { name: 'Войти' }).click();
    await page.waitForURL(url => !url.href.includes('/seller/signin') && !url.href.includes('/showcaptcha'), {
      timeout: 180_000,
    });
  }

  await page.goto(productsUrl, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await page.getByText('Мои товары', { exact: true }).waitFor({ state: 'visible', timeout: 60_000 });
  const createLink = page.getByRole('link', { name: 'Создать карточку товара' });
  await createLink.locator('xpath=..').getByRole('button').click();
  await page.getByText('Загрузить из файла', { exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor({ state: 'visible', timeout: 15_000 });
  const fileInput = dialog.locator('input[type="file"]');
  if (await fileInput.count()) {
    await fileInput.first().setInputFiles(path.resolve(templatePath));
  } else {
    const chooserPromise = page.waitForEvent('filechooser');
    await dialog.getByRole('button', { name: 'Загрузить', exact: true }).first().click();
    await (await chooserPromise).setFiles(path.resolve(templatePath));
  }
  const submit = dialog.getByRole('button', { name: 'Загрузить', exact: true }).last();
  await submit.waitFor({ state: 'visible', timeout: 15_000 });
  await submit.waitFor({ state: 'attached' });
  if (!(await submit.isEnabled())) {
    result('failed', 'upload_disabled');
    process.exitCode = 1;
  } else {
    const accepted = page.waitForResponse(response => {
      const request = response.request();
      const contentType = request.headers()['content-type'] ?? '';
      return request.method() === 'POST' && contentType.includes('multipart/form-data')
        && new URL(response.url()).hostname.endsWith('uzum.uz');
    }, { timeout: 120_000 });
    await submit.click();
    const response = await accepted;
    if (response.ok()) result('accepted');
    else {
      result('failed', 'upload_rejected');
      process.exitCode = 1;
    }
  }
} catch {
  result('failed', 'auth_timeout');
  process.exitCode = 1;
} finally {
  if (context) await context.close();
}