import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';

const output = 'screenshots/anchor-fixes';
await fs.mkdir(output, {recursive: true});
const browser = await chromium.launch({headless: true});
const errors = [], badResponses = [], samples = [];
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 960}});
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('response', response => { if (response.status() >= 400) badResponses.push(response.url()); });
  await page.goto(process.env.OFFICE_PREVIEW_URL || 'http://127.0.0.1:5197/');
  await page.waitForFunction(() => document.querySelector('#office').classList.contains('godot-active'), {}, {timeout: 45000});
  const frame = page.frames().find(frame => frame.url().includes('/godot/'));
  await frame.waitForFunction(() => window.officeState?.actors?.length === 6);
  await page.locator('#pauseBtn').click();
  for (const state of ['idle', 'snack', 'sleeping', 'working', 'blocked']) {
    await frame.evaluate(state => window.officeUpdate(JSON.stringify({pose: {codex: state}, paused: false, lowMotion: false})), state);
    await page.waitForTimeout(250);
    const snapshot = await frame.evaluate(() => window.officeState);
    assert(snapshot.actors.every(actor => Math.abs(actor.realFootY - .040) < .0001));
    samples.push({state, actor: snapshot.actors[1]});
  }
  await frame.evaluate(() => window.officeUpdate(JSON.stringify({pose: {codex: 'done', opencode: 'helping'}, paused: false, lowMotion: false})));
  const scales = [], frames = new Set();
  for (let sample = 0; sample < 48; sample++) {
    await page.waitForTimeout(120);
    const snapshot = await frame.evaluate(() => window.officeState);
    const actor = snapshot.actors[1];
    assert.equal(actor.poseKey, 'codex-stretch-atlas');
    assert.equal(snapshot.actors[2].poseKey, 'opencode-standing');
    assert(snapshot.actors.every(actor => Math.abs(actor.realFootY - .040) < .0001));
    assert(snapshot.actors.every(actor => actor.anchorVerified === false && actor.scaleVerified === false));
    scales.push(actor.pixelSize); frames.add(actor.frame);
    if (sample === 18) await page.screenshot({path: `${output}/desktop-done-helping.png`, fullPage: true});
  }
  assert(frames.size > 10, 'stretch atlas must actually advance');
  assert(Math.max(...scales) - Math.min(...scales) < 1e-8, 'family scale must stay fixed');
  await frame.evaluate(() => window.officeUpdate(JSON.stringify({lowMotion: true, pose: {codex: 'done', opencode: 'idle'}})));
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({width, height: 844});
    await page.waitForTimeout(350);
    await page.screenshot({path: `${output}/done-${width}.png`, fullPage: true});
    const png = await frame.locator('#canvas').screenshot();
    const colors = await page.evaluate(async bytes => {
      const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], {type: 'image/png'}));
      const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height;
      const context = canvas.getContext('2d'); context.drawImage(bitmap, 0, 0);
      const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
      const values = new Set();
      for (let i = 0; i < data.length; i += 160) values.add(`${data[i]},${data[i + 1]},${data[i + 2]}`);
      return values.size;
    }, [...png]);
    assert(colors > 150, `${width}px canvas blank`);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  assert.deepEqual(errors, []); assert.deepEqual(badResponses, []);
  const report = {result: 'passed', scope: 'metadata geometry and rendered animation; not semantic foot/chair/head-scale acceptance', samples, atlasFramesObserved: frames.size, pixelSizeSpread: Math.max(...scales) - Math.min(...scales), errors, badResponses};
  await fs.writeFile(`${output}/verification.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally { await browser.close(); }
