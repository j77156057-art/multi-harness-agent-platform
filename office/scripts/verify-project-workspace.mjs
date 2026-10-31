import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { requireOffice } from './lib/office-url.mjs';

const base = await requireOffice('verify-project-workspace.mjs');
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const projectName = `E2E-PROJECT-${Date.now()}`;

  await page.goto(`${base}/`);
  await page.waitForSelector('#aoLobby:not([hidden])');
  assert.equal(await page.locator('#aoWorkspace').getAttribute('hidden'), '');
  assert.equal(await page.locator('[data-empty]').isVisible(), true);
  assert.equal(await page.locator('.project-create-dialog').isVisible(), false);

  // The create form is a real modal step behind the only lobby entry. It must
  // not be permanently rendered as a display:flex overlay.
  await page.locator('[data-new-project]').click();
  await page.waitForSelector('.project-create-dialog[open]');
  assert.equal(await page.locator('.project-create-dialog').isVisible(), true);
  const memberValues = await page.locator('[data-project-members] input').evaluateAll(nodes => nodes.map(node => node.value));
  assert.equal(memberValues.length, 9);
  for (const administrator of ['codex-window-admin', 'qoder-window-admin', 'office-admin']) {
    assert.equal(memberValues.includes(administrator), false);
  }
  assert.equal(memberValues.includes('claude-code'), true);
  assert.equal(memberValues.includes('qoder'), true);

  await page.locator('[data-project-name]').fill(projectName);
  await page.locator('[data-confirm-create]').click();
  await page.waitForSelector('#phoneDialog[open]');
  await page.waitForSelector('#openingDialog[open]');
  assert.equal(await page.locator('body').getAttribute('data-project-phase'), 'phone');
  assert.equal(await page.locator('#aoWorkspace > .shell').isVisible(), false);
  assert.equal(await page.locator('#aoStartBtn').isVisible(), true);

  // Confirming the project opens the opening wizard directly; closing it leaves
  // the project phone, not a stale modal overlay.
  await page.locator('#openingCloseBtn').click();
  await page.waitForFunction(() => !document.querySelector('#openingDialog')?.open);
  assert.equal(await page.locator('#phoneDialog').evaluate(node => node.open), true);
  assert.equal(await page.locator('#openingDialog').isVisible(), false);

  await page.locator('#returnProjectsBtn').click();
  await page.waitForSelector('#aoLobby:not([hidden])');
  assert.equal(await page.locator('[data-history] .project-card').count(), 1);
  assert.equal(await page.locator('.project-create-dialog').isVisible(), false);
  assert.equal(await page.locator('#aoWorkspace').getAttribute('hidden'), '');
  assert.equal(await page.locator('body').getAttribute('data-project-phase'), 'lobby');

  // Reopening the saved project should reuse the project, not create a second
  // one, and must return to the phone phase without reopening the create form.
  await page.locator('[data-project-id="project-001"] .project-card-open').click();
  await page.waitForSelector('#phoneDialog[open]');
  assert.equal(await page.locator('[data-history] .project-card').count(), 1);
  assert.equal(await page.locator('body').getAttribute('data-project-phase'), 'phone');
  assert.equal(await page.locator('.project-create-dialog').isVisible(), false);

  // Project history is persisted per origin, not just in memory.
  await page.reload();
  await page.waitForSelector('#aoLobby:not([hidden])');
  assert.equal(await page.locator('[data-history] .project-card').count(), 1);
  assert.match(await page.locator('[data-history] .project-card').textContent(), new RegExp(projectName));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks: 20, result: 'PASS' }));
} finally {
  await browser.close();
}
