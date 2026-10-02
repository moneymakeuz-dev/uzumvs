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
let stage = 'start';
const dryRun = process.env.UZUM_DRY_RUN === '1';
try {
  await mkdir(profilePath, { recursive: true });
  context = await chromium.launchPersistentContext(path.resolve(profilePath), {
    headless: false,
    acceptDownloads: false,
    viewport: { width: 1440, height: 1000 },
  });
  const page = context.pages()[0] ?? await context.newPage();
  stage = 'open_products';
  const productsUrl = `https://seller.uzum.uz/seller/${encodeURIComponent(shopId)}/products/all`;
  await page.goto(productsUrl, { waitUntil: 'domcontentloaded', timeout: 45_000 });

  if (page.url().includes('/showcaptcha')) {
    await page.waitForURL(url => !url.href.includes('/showcaptcha'), { timeout: 180_000 });
  }
  stage = 'login';
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
  stage = 'open_menu';
  await createLink.locator('xpath=..').getByRole('button').last().click();
  await page.getByText('Загрузить из файла', { exact: true }).click();
  stage = 'file_dialog';
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
  if (dryRun) {
    result('dry_run_ready');
  } else if (!(await submit.isEnabled())) {
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
} catch (error) {
  if (context && process.env.UZUM_DEBUG_DIR) {
    const page = context.pages()[0];
    await mkdir(process.env.UZUM_DEBUG_DIR, { recursive: true });
    await page?.screenshot({ path: path.join(process.env.UZUM_DEBUG_DIR, `${stage}.png`) }).catch(() => {});
  }
  process.stderr.write(`stage=${stage} ${String(error?.message ?? error).split('\n')[0]}\n`);
  result('failed', stage === 'login' || stage === 'open_products' ? 'auth_timeout' : 'ui_changed');
  process.exitCode = 1;
} finally {
  if (context) await context.close();
}