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

if (process.env.STRIPE_SMOKE_TEST !== '1') throw new Error('Set STRIPE_SMOKE_TEST=1; this creates sandbox Stripe accounts and emulator-only rentals.');
try {
  await call('Page.bringToFront');
  await call('Runtime.enable');
  await call('Page.navigate', {url:'http://127.0.0.1:5002/rentals'});
  await waitFor("!document.getElementById('auth-button').disabled");
  assert(await evaluate("document.body.textContent.includes('Local test mode')"));
  async function login(name) {
    await evaluate(`(async()=>{const a=await import('https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js');const token=JSON.stringify({sub:'stripe-smoke-${name}',email:'stripe-${name}@example.test',email_verified:true,name:'stripe-${name}',iss:'https://accounts.google.com',aud:'demo-bikebetter',iat:Math.floor(Date.now()/1000),exp:Math.floor(Date.now()/1000)+3600});await a.signInWithCredential(a.getAuth(),a.GoogleAuthProvider.credential(token));const f=await import('https://www.gstatic.com/firebasejs/12.19.0/firebase-firestore.js');const {createBikeStore}=await import('/static/bike-store.js');window.testStore=createBikeStore({db:f.getFirestore(),auth:a.getAuth(),firestore:f});window.testPost=async(path,body={})=>{const token=await a.getAuth().currentUser.getIdToken();const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify(body)});return {code:r.status,body:await r.json()};};})()`);
    await waitFor(`document.getElementById('account-name').textContent==='stripe-${name}'`);
  }
  await login('owner');
  const onboarding=await evaluate("testPost('/api/connect/onboarding')");
  assert.equal(onboarding.code,200,JSON.stringify(onboarding.body));
  assert.equal(new URL(onboarding.body.url).hostname,'connect.stripe.com');
  fs.writeFileSync('data/stripe-browser-onboarding.json',JSON.stringify(onboarding.body),{mode:0o600});
  const status=await evaluate("testPost('/api/connect/status')");
  assert.equal(status.code,200);assert.equal(status.body.exists,true);assert.equal(status.body.platformFeePercent,0);
  await evaluate("document.getElementById('tab-mine').click(); document.getElementById('connect-refresh').click()");
  await waitFor("document.querySelector('#connect-banner stripe-connect-notification-banner')");
  await screenshot('stripe-owner-setup');
  const bike=await evaluate(`testStore.saveBike({brand:'Stripe test',model:'Sandbox bike',type:'Hybrid',colour:'Blue',frameSize:'M',neighbourhood:'The Annex',description:'Emulator-only Stripe integration test',serialNumber:'',notes:'',rateHour:'8',rateDay:'30',published:true})`);
  await login('renter');
  const req=await evaluate(`testStore.requestRental(${JSON.stringify(bike)},{startDate:new Date(Date.now()+86400000).toISOString().slice(0,10),endDate:new Date(Date.now()+86400000*3).toISOString().slice(0,10),message:'Sandbox test'})`);
  await login('owner');await evaluate(`testStore.updateRequest(${JSON.stringify(req)},'accepted')`);
  await login('renter');
  const result=await evaluate(`testPost('/api/payments/checkout',{requestId:${JSON.stringify(req)}})`);
  if (!status.body.ready) {
    assert.equal(result.code,409,JSON.stringify(result.body));
    assert.match(result.body.error,/owner needs to finish/);
  } else {
    assert.equal(result.code,200,JSON.stringify(result.body));
    assert.equal(new URL(result.body.url).hostname,'checkout.stripe.com');
    const cancelled=await evaluate(`testPost('/api/payments/cancel',{requestId:${JSON.stringify(req)}})`);
    assert.equal(cancelled.body.status,'expired');
  }
  assert.equal(exceptions.length,0,JSON.stringify(exceptions));
  fs.writeFileSync('data/stripe-browser-rental.json',JSON.stringify({bikeId:bike,requestId:req}),{mode:0o600});
  console.log('PASS: Firebase-verified owner account, sandbox hosted onboarding, 0% commission, notification banner, accepted rental and readiness-gated Checkout.');
} finally { ws.close(); }
