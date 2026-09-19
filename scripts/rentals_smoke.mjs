// Node 22+; start Flask and Chrome with --remote-debugging-port=9224 first.
// Uses Chrome's built-in debugging protocol: no frontend build or npm install.
import assert from "node:assert/strict";
import fs from "node:fs";

const debugUrl = process.env.CHROME_DEBUG_URL || "http://127.0.0.1:9224";
const pages = await fetch(`${debugUrl}/json`).then((r) => r.json());
const page = pages.find(
  (p) => p.type === "page" && p.url.includes("127.0.0.1:5002"),
);
assert(page, "Open http://127.0.0.1:5002 in the debug browser first");
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
try {
  await call("Runtime.enable");
  await call("Page.navigate", { url: "http://127.0.0.1:5002/rentals" });
  await waitFor("!document.getElementById('auth-button').disabled");
  assert(
    await evaluate("document.body.textContent.includes('Local test mode')"),
    "Only run against emulators",
  );
  async function login(name) {
    await evaluate(
      `(async()=>{const a=await import('https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js');const token=JSON.stringify({sub:'browser-${name}',email:'${name}@example.test',email_verified:true,name:'${name}',iss:'https://accounts.google.com',aud:'demo-safer-ride',iat:Math.floor(Date.now()/1000),exp:Math.floor(Date.now()/1000)+3600});await a.signInWithCredential(a.getAuth(),a.GoogleAuthProvider.credential(token));})()`,
    );
    await waitFor(
      `document.getElementById('account-name').textContent === '${name}'`,
    );
  }
  await login("owner");
  await evaluate("document.getElementById('register-button').click()");
  await waitFor("document.getElementById('bike-dialog').open");
  await evaluate(
    `(()=>{const f=document.getElementById('bike-form');Object.entries({brand:'Browser Trek',model:'FX smoke',colour:'Blue',serialNumber:'PRIVATE-BROWSER-SERIAL'}).forEach(([k,v])=>f.elements[k].value=v);document.getElementById('next-button').click()})()`,
  );
  assert(
    await evaluate(
      "!document.getElementById('bike-form').elements.published.checked",
    ),
    "Private by default",
  );
  await evaluate("document.getElementById('save-button').click()");
  await waitFor(
    "!document.getElementById('bike-dialog').open && document.getElementById('my-bikes').textContent.includes('Browser Trek')",
  );
  await evaluate(
    "[...document.querySelectorAll('#my-bikes button')].find(b=>b.textContent==='View / edit').click()",
  );
  await waitFor("document.getElementById('bike-dialog').open");
  assert(
    await evaluate(
      "document.getElementById('bike-form').elements.serialNumber.value==='PRIVATE-BROWSER-SERIAL'",
    ),
    "Private details persist",
  );
  await evaluate(
    `(()=>{document.getElementById('next-button').click();const f=document.getElementById('bike-form');f.elements.published.checked=true;f.elements.published.dispatchEvent(new Event('change'));f.elements.neighbourhood.value='The Annex';f.elements.rateHour.value='8.50';f.elements.rateDay.value='30';document.getElementById('save-button').click()})()`,
  );
  await waitFor(
    "!document.getElementById('bike-dialog').open && document.getElementById('listings').textContent.includes('Browser Trek')",
  );
  await login("renter");
  await evaluate(
    "document.getElementById('tab-browse').click();document.querySelector('#listings button').click()",
  );
  await waitFor("document.getElementById('request-dialog').open");
  await evaluate(
    "document.querySelector('#request-form button[type=submit]').click()",
  );
  await waitFor(
    "!document.getElementById('request-dialog').open && document.getElementById('outgoing').textContent.includes('Awaiting owner')",
  );
  await login("owner");
  await waitFor(
    "document.getElementById('incoming').textContent.includes('Accept request')",
  );
  await evaluate(
    "[...document.querySelectorAll('#incoming button')].find(b=>b.textContent==='Accept request').click()",
  );
  await waitFor(
    "document.getElementById('incoming').textContent.includes('Accepted') && !document.getElementById('listings').textContent.includes('Browser Trek')",
  );
  await login("renter");
  await waitFor(
    "document.getElementById('outgoing').textContent.includes('owner@example.test')",
  );
  await login("owner");
  await waitFor(
    "document.getElementById('incoming').textContent.includes('Mark returned')",
  );
  await evaluate(
    "[...document.querySelectorAll('#incoming button')].find(b=>b.textContent==='Mark returned').click()",
  );
  await waitFor(
    "document.getElementById('incoming').textContent.includes('Returned')",
  );
  await evaluate("document.getElementById('tab-browse').click()");
  await call("Emulation.setDeviceMetricsOverride", {
    width: 1440,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await screenshot("rentals-desktop");
  await call("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  assert(
    await evaluate("document.documentElement.scrollWidth === innerWidth"),
    "No mobile overflow",
  );
  await screenshot("rentals-mobile");
  await evaluate("document.getElementById('auth-button').click()");
  await waitFor(
    "document.getElementById('auth-button').textContent==='Sign in with Google'",
  );
  assert(
    await evaluate(
      "!document.getElementById('my-bikes').textContent.includes('Browser Trek') && !document.getElementById('incoming').textContent.includes('renter@example.test')",
    ),
    "Sign-out clears private UI",
  );
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  console.log(
    "PASS: private registration, reload/edit, publish, rental request, accept, contacts, return, mobile layout, sign-out privacy.",
  );
} finally {
  ws.close();
}
