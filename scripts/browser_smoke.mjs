// Node 22+; start Flask and Chrome with --remote-debugging-port=9224 first.
// Uses Chrome's built-in debugging protocol: no frontend build or npm install.
import assert from "node:assert/strict";
import fs from "node:fs";

const debugUrl = process.env.CHROME_DEBUG_URL || "http://127.0.0.1:9224";
const pages = await fetch(`${debugUrl}/json`).then((r) => r.json());
const page = pages.find(
  (p) => p.type === "page" && p.url.includes("127.0.0.1:5001"),
);
assert(page, "Open http://127.0.0.1:5001 in the debug browser first");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve) => {
  ws.onopen = resolve;
});
let sequence = 0;
const pending = new Map(),
  exceptions = [];
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const task = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) task.reject(message.error);
    else task.resolve(message.result);
  } else if (message.method === "Runtime.exceptionThrown")
    exceptions.push(message.params);
};
function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await call("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function waitFor(expression) {
  const start = Date.now();
  while (!(await evaluate(expression))) {
    assert(Date.now() - start < 15000, `Timed out: ${expression}`);
    await new Promise((resolve) => setTimeout(resolve, 80));
  }
}
async function screenshot(name) {
  await new Promise((resolve) => setTimeout(resolve, 400));
  fs.mkdirSync("artifacts", { recursive: true });
  const result = await call("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
  });
  fs.writeFileSync(`artifacts/${name}.png`, Buffer.from(result.data, "base64"));
}
const checks = [];
try {
  await call("Runtime.enable");
  await call("Emulation.setDeviceMetricsOverride", {
    width: 1440,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await call("Page.reload");
  await waitFor(
    "typeof collisionLayer !== 'undefined' && collisionLayer && state.points.length > 0",
  );
  assert(
    await evaluate(
      "$('empty').hidden === false && !state.origin && !state.destination",
    ),
  );
  assert(
    await evaluate(
      "collisionLayer.getLayers().length > 0 && document.documentElement.scrollWidth === innerWidth",
    ),
  );
  checks.push("Empty map, real collision layer, desktop bounds");
  await screenshot("desktop-empty");

  await evaluate(
    "map.fire('click',{latlng:L.latLng(43.6629,-79.3957)}); map.fire('click',{latlng:L.latLng(43.6487,-79.3715)});void 0;",
  );
  await waitFor("!$('results').hidden");
  assert(
    await evaluate("state.routes.length === 2 && state.markers.length === 2"),
  );
  const actual = await evaluate(
    "({direct:Number($('direct-count').textContent),safer:Number($('safer-count').textContent),delta:$('delta').textContent})",
  );
  assert(Number.isInteger(actual.direct) && Number.isInteger(actual.safer));
  if (actual.safer > actual.direct)
    assert(actual.delta.includes("more recorded"));
  checks.push("Two map clicks produce real route counts and honest comparison");
  await screenshot("desktop-route");

  await evaluate(`window.originalFetch=fetch;window.routeBodies=[];
    window.fetch=(url,options)=>{if(url==='/api/route')window.routeBodies.push(JSON.parse(options.body));return window.originalFetch(url,options);};
    document.querySelector('[data-level="1"]').click();`);
  await waitFor("window.routeBodies.length === 1 && !$('results').hidden");
  assert.equal(await evaluate("window.routeBodies.at(-1).level"), 1);
  await evaluate(
    "$('hour').value=22;$('hour').dispatchEvent(new Event('input'));",
  );
  await waitFor("window.routeBodies.length === 2 && !$('results').hidden");
  assert.equal(await evaluate("window.routeBodies.at(-1).hour"), 22);
  assert.equal(await evaluate("$('hour-display').textContent"), "10pm");
  checks.push("Confidence and hour controls refetch without submission");

  await evaluate(`window.fetch=async(url,options)=>{const response=await window.originalFetch(url,options);
    if(url==='/api/route'&&JSON.parse(options.body).hour===23)await new Promise(r=>setTimeout(r,650));return response;};
    state.hour=23;getRoute();`);
  await new Promise((resolve) => setTimeout(resolve, 100));
  await evaluate("state.hour=7;getRoute();");
  await waitFor(
    "!$('results').hidden && $('comparison-hour').textContent === 'At 7am'",
  );
  await new Promise((resolve) => setTimeout(resolve, 700));
  assert.equal(await evaluate("$('comparison-hour').textContent"), "At 7am");
  checks.push("Delayed responses cannot overwrite a newer selection");

  await evaluate(
    "window.fetch=window.originalFetch;map.fire('click',{latlng:L.latLng(43.665,-79.40)});void 0;",
  );
  assert(
    await evaluate(
      "state.origin && !state.destination && state.routes.length === 0 && !$('empty').hidden",
    ),
  );
  await evaluate(
    "$('origin').value='Union';$('origin').dispatchEvent(new Event('input'));",
  );
  await waitFor(
    "!$('origin-results').hidden && $('origin-results').querySelector('button')",
  );
  await evaluate("$('origin-results').querySelector('button').click();");
  assert(
    await evaluate(
      "state.origin && $('origin').value.includes('Union Station')",
    ),
  );
  checks.push("Third click resets and local place search selects coordinates");

  await evaluate(
    "window.fetch=(url,options)=>url==='/api/route'?Promise.reject(new TypeError('Offline test')):window.originalFetch(url,options);setPoint('destination',[43.6487,-79.3715]);",
  );
  await waitFor("!$('error').hidden");
  assert(
    await evaluate(
      "$('error').textContent.includes('Routing is unavailable') && collisionLayer.getLayers().length>0 && state.routes.length===0",
    ),
  );
  checks.push("Routing failure retains collision map and removes stale routes");

  await call("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  await evaluate("window.fetch=window.originalFetch;$('example').click();");
  await waitFor("!$('results').hidden");
  assert(
    await evaluate(
      "document.documentElement.scrollWidth === innerWidth && document.querySelector('.controls').classList.contains('collapsed')",
    ),
  );
  assert(
    await evaluate(
      "document.querySelector('.controls').getBoundingClientRect().bottom < document.querySelector('.comparison').getBoundingClientRect().top",
    ),
  );
  await screenshot("mobile-route");
  await evaluate("$('toggle').click();");
  assert(
    await evaluate("$('toggle').getAttribute('aria-expanded') === 'true'"),
  );
  checks.push("Mobile layout, readable comparison, expandable controls");
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log(
    JSON.stringify(
      { status: "passed", checks, realComparison: actual },
      null,
      2,
    ),
  );
} finally {
  await evaluate(
    "if(window.originalFetch)window.fetch=window.originalFetch",
  ).catch(() => {});
  ws.close();
}
