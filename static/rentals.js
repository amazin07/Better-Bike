import { preparePhoto } from "./bike-photo.js";
import { isDemoRecord } from "./bike-store.js";
const $ = (id) => document.getElementById(id);
let selectedPhoto,
  photoGeneration = 0,
  photoBusy = false;
const photoObservers = new Map();
const requestRows = { incoming: [], outgoing: [] };
const paymentRows = { incoming: new Map(), outgoing: new Map() };
const checkoutReturn = new URLSearchParams(location.search);
let returnHandled = false;
function rentalDays(rental) {
  return Math.round((rental.endAt.toDate() - rental.startAt.toDate()) / 86400000) + 1;
}
let client,
  user,
  privateListeners = [],
  pendingRegistration = false,
  editing = null,
  requestedBike = null,
  reportedBike = null;
// bikeId -> its open "missing" theft report, so owned bikes show recovery controls.
const myReports = new Map();
let myBikes = [];
const money = (cents) =>
  new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD" }).format(
    cents / 100,
  );
const date = (stamp) =>
  stamp.toDate().toLocaleDateString("en-CA", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
let errorMessage = (error) =>
  error.message || "Connection failed. Please reload and try again.";
function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
  $("notice").className = error ? "error" : "";
}
function empty(id, text) {
  photoObservers.get(id)?.disconnect();
  $(id).replaceChildren(node("p", text, "empty"));
}
async function busy(button, action, errorTarget) {
  button.disabled = true;
  try {
    await action();
  } catch (error) {
    if (errorTarget) $(errorTarget).textContent = errorMessage(error);
    else notice(errorMessage(error), true);
  } finally {
    button.disabled = false;
  }
}
function action(label, fn, primary = false) {
  const button = node("button", label, primary ? "primary" : "");
  button.type = "button";
  button.onclick = () => busy(button, fn);
  return button;
}
function tab(name) {
  for (const el of document.querySelectorAll("[data-tab]")) {
    const active = el.dataset.tab === name;
    el.setAttribute("aria-selected", active);
    el.tabIndex = active ? 0 : -1;
    $(el.dataset.tab).hidden = !active;
  }
}
for (const el of document.querySelectorAll("[data-tab]")) {
  el.onclick = () => tab(el.dataset.tab);
  el.onkeydown = (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll("[data-tab]")];
    const i = tabs.indexOf(el);
    const next =
      event.key === "Home"
        ? tabs[0]
        : event.key === "End"
          ? tabs.at(-1)
          : tabs[(i + (event.key === "ArrowRight" ? 1 : 2)) % 3];
    tab(next.dataset.tab);
    next.focus();
  };
}
for (const button of document.querySelectorAll("[data-close]"))
  button.onclick = () => button.closest("dialog").close();
function illustration() {
  // Static illustration; no listing data is interpreted as HTML.
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 220 100");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("bike-art");
  svg.innerHTML =
    '<g fill="none" stroke="currentColor" stroke-width="3"><circle cx="51" cy="68" r="26"/><circle cx="169" cy="68" r="26"/><path d="M51 68 81 26l30 42H51l17-32h86l15 32-22-54h-18M71 26h24M111 68l17-32"/></g>';
  return svg;
}
function renderBikes(id, bikes, own = false) {
  photoObservers.get(id)?.disconnect();
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries)
      if (entry.isIntersecting) {
        observer.unobserve(entry.target);
        const card = entry.target,
          ownerUid = user?.uid;
        client.store
          .photo(card.dataset.bikeId)
          .then((src) => {
            if (!src || !card.isConnected || (own && user?.uid !== ownerUid))
              return;
            const img = node("img", "", "bike-photo");
            img.alt = card.dataset.photoAlt;
            img.src = src;
            img.onerror = () => img.replaceWith(illustration());
            card.firstElementChild.replaceWith(img);
          })
          .catch(() => {
            if (card.isConnected)
              card.prepend(
                node("p", "Photo unavailable. Try reloading.", "hint"),
              );
          });
      }
  });
  photoObservers.set(id, observer);
  $(id).replaceChildren();
  if (!bikes.length)
    return empty(
      id,
      own
        ? "No bikes registered yet. Register your first bike to keep its details together."
        : "No bikes are listed right now. Have a spare? Register it and choose to publish a rental listing.",
    );
  for (const bike of bikes) {
    const demo = isDemoRecord(bike);
    const card = node("article", "", "card");
    card.append(
      illustration(),
      node(
        "span",
        own
          ? bike.activeRequestId
            ? "Rental in progress"
            : bike.published
              ? "Listed for rent"
              : "Private record"
          : demo ? "Demo listing" : "Available to request",
        "badge",
      ),
      node("h3", `${bike.brand} ${bike.model}`),
      node(
        "p",
        [bike.type, bike.colour, bike.frameSize].filter(Boolean).join(" · "),
        "meta",
      ),
    );
    if (bike.neighbourhood) card.append(node("p", bike.neighbourhood, "meta"));
    if (bike.published)
      card.append(
        node(
          "p",
          `${money(bike.rateHourCents)} / hour · ${money(bike.rateDayCents)} / day`,
          "rates",
        ),
      );
    if (bike.description)
      card.append(node("p", bike.description, "description"));
    const report = own ? myReports.get(bike.id) : null;
    if (report)
      card.append(
        node(
          "p",
          report.rewardCents > 0
            ? `Reported missing · ${money(report.rewardCents)} reward on the public feed`
            : "Reported missing on the public feed",
          "meta",
        ),
      );
    const buttons = node("div", "", "actions");
    if (own) {
      buttons.append(action("View / edit", () => openBike(bike)));
      if (report)
        buttons.append(
          action("Mark recovered", async () => {
            await client.store.markRecovered(report.id);
            notice("Marked recovered. It is off the Missing bikes page.");
          }),
        );
      else
        buttons.append(
          action("Report stolen", () => openReport(bike)),
        );
      if (bike.published)
        buttons.append(
          action("Unpublish", async () => {
            await client.store.unpublish(bike.id);
            notice(
              "Listing removed. Your bike record is still private in My bikes.",
            );
          }),
        );
      if (!bike.activeRequestId)
        buttons.append(
          action("Remove", async () => {
            if (
              confirm(
                `Remove ${bike.brand} ${bike.model} and its private registration details? Existing rental requests remain in your history.`,
              )
            ) {
              await client.store.removeBike(bike.id);
              notice("Bike registration removed.");
            }
          }),
        );
    } else if (demo) {
      const preview = node("button", "Sample · Not bookable");
      preview.type = "button";
      preview.disabled = true;
      buttons.append(preview);
    } else if (user?.uid === bike.ownerUid)
      buttons.append(action("Your bike", () => tab("mine")));
    else
      buttons.append(
        action(
          user ? "Request this bike" : "Sign in to request",
          async () => {
            if (!user) {
              await client.signIn();
              return;
            }
            openRequest(bike);
          },
          true,
        ),
      );
    card.append(buttons);
    $(id).append(card);
    if (bike.photoVersion) {
      card.dataset.bikeId = bike.id;
      card.dataset.photoAlt = `${bike.brand} ${bike.model}`;
      observer.observe(card);
    }
  }
}
function renderRequests(id, requests, incoming) {
  requestRows[id] = requests;
  $(id).replaceChildren();
  if (!requests.length)
    return empty(
      id,
      incoming
        ? "No one has requested your bikes yet."
        : "You haven’t requested a bike yet.",
    );
  requests.sort(
    (a, b) => (b.createdAt?.seconds || 0) - (a.createdAt?.seconds || 0),
  );
  for (const request of requests) {
    const card = node("article", "", "card");
    card.append(
      node(
        "span",
        {
          pending: "Awaiting owner",
          accepted: "Accepted",
          declined: "Declined",
          cancelled: "Cancelled",
          completed: "Returned",
        }[request.status],
        "badge",
      ),
      node("h3", request.bikeTitle),
      node("p", `${date(request.startAt)} – ${date(request.endAt)}`, "meta"),
      node(
        "p",
        `${money(request.rateHourCents)} / hour · ${money(request.rateDayCents)} / day`,
        "meta",
      ),
    );
    if (incoming)
      card.append(
        node("p", `${request.renterName} · ${request.renterEmail}`, "meta"),
      );
    else if (request.ownerEmail)
      card.append(node("p", `Contact owner: ${request.ownerEmail}`, "meta"));
    if (request.message) card.append(node("p", request.message, "description"));
    const payment = paymentRows[id].get(request.id);
    const pendingPayment = ['creating', 'open', 'processing'].includes(payment?.status);
    const days = rentalDays(request);
    card.append(node('p', `${days} ${days === 1 ? 'day' : 'days'} × ${money(request.rateDayCents)} = ${money(days * request.rateDayCents)} CAD`, 'rates'));
    if (payment) card.append(node('p', {
      paid: 'Test payment confirmed · No real money charged',
      creating: 'Preparing test checkout', open: 'Test checkout in progress',
      processing: 'Waiting for Stripe confirmation', failed: 'Test payment failed · You can retry', expired: 'Test checkout cancelled or expired · Unpaid',
    }[payment.status] || 'Payment status unavailable', 'meta'));
    const buttons = node("div", "", "actions");
    const change = (label, state) =>
      action(label, async () => {
        await client.store.updateRequest(request.id, state);
        notice(
          state === "accepted"
            ? "Request accepted. Your email is now shared with the renter. Contact them to arrange pickup."
            : "Rental request updated.",
        );
      });
    if (request.status === "pending") {
      if (incoming)
        buttons.append(
          change("Accept request", "accepted"),
          change("Decline", "declined"),
        );
      else buttons.append(change("Cancel request", "cancelled"));
    }
    if (request.status === 'accepted') {
      if (!incoming && payment?.status !== 'paid') {
        const pay = action(pendingPayment ? 'Resume test checkout' : `Pay ${money(days * request.rateDayCents)} · Test`, async () => {
          const result = await client.paymentAction('checkout', request.id);
          if (result.status === 'paid') { notice('Test payment confirmed. No real money was charged.'); return; }
          if (result.status === 'processing') { notice('Stripe is still confirming this payment. Check again shortly.'); return; }
          const url = new URL(result.url);
          if (url.protocol !== 'https:' || url.hostname !== 'checkout.stripe.com') throw new Error('Invalid checkout link.');
          location.assign(url.href);
        }, true);
        pay.disabled = !client.payments?.enabled || payment?.status === 'processing';
        if (!client.payments?.enabled) pay.textContent = 'Test checkout unavailable';
        buttons.append(pay);
      }
      if (pendingPayment) {
        buttons.append(action('Check payment', async () => {
          const result = await client.paymentAction('refresh', request.id);
          notice(result.status === 'paid' ? 'Test payment confirmed. No real money was charged.' : `Payment status: ${result.status}.`);
        }));
        if (payment.status !== 'processing') buttons.append(action('Cancel checkout', async () => {
          const result = await client.paymentAction('cancel', request.id);
          notice(result.status === 'paid' ? 'Payment completed before cancellation. Test payment confirmed.' : result.status === 'processing' ? 'Stripe is still confirming payment. Check again shortly.' : 'Checkout closed. The rental remains accepted; you can retry payment.');
        }));
      }
      if (incoming) {
        const returned = change('Mark returned', 'completed');
        returned.disabled = pendingPayment;
        if (pendingPayment) card.append(node('p', 'Finish or cancel checkout before marking this bike returned.', 'hint'));
        buttons.append(returned);
      }
    }
    card.append(buttons);
    $(id).append(card);
  }
}
const form = $("bike-form");
function photoPreview(src = "") {
  $("photo-preview").hidden = !src;
  if (src) $("photo-preview").src = src;
  else $("photo-preview").removeAttribute("src");
  $("remove-photo").hidden = !src;
}
function resetPhoto() {
  photoGeneration++;
  selectedPhoto = undefined;
  photoBusy = false;
  $("bike-photo").value = "";
  $("photo-status").textContent = "";
  $("next-button").disabled = false;
  photoPreview();
}
$("bike-photo").onchange = async () => {
  const file = $("bike-photo").files[0];
  if (!file) return;
  const generation = ++photoGeneration;
  photoBusy = true;
  $("next-button").disabled = true;
  $("photo-status").textContent = "Preparing photo…";
  try {
    const src = await preparePhoto(file);
    if (generation !== photoGeneration) return;
    selectedPhoto = src;
    photoPreview(src);
    $("photo-status").textContent =
      "Photo ready. Save your bike to keep this change.";
  } catch (error) {
    if (generation === photoGeneration) {
      $("photo-status").textContent = error.message;
      $("bike-photo").value = "";
    }
  } finally {
    if (generation === photoGeneration) {
      photoBusy = false;
      $("next-button").disabled = false;
    }
  }
};
$("remove-photo").onclick = () => {
  resetPhoto();
  selectedPhoto = null;
  $("photo-status").textContent = "Photo will be removed when you save.";
};
$("bike-dialog").addEventListener("close", resetPhoto);
function listingFields() {
  const published = form.elements.published.checked;
  $("listing-fields").hidden = !published;
  for (const name of ["neighbourhood", "rateHour", "rateDay"])
    form.elements[name].required = published;
  $("save-button").textContent = published
    ? "Save & publish listing"
    : "Save private bike";
}
form.elements.published.onchange = listingFields;
function step(second) {
  $("details-step").hidden = second;
  $("details-step").disabled = second;
  $("listing-step").hidden = !second;
  $("listing-step").disabled = !second;
  $("back-button").hidden = !second;
  $("next-button").hidden = second;
  $("save-button").hidden = !second;
  $("step-label").textContent = second
    ? "2 of 2 · Optional rental listing"
    : "1 of 2 · Bike details";
  $("bike-error").textContent = "";
}
async function openBike(bike = null) {
  if (!user) {
    pendingRegistration = true;
    try {
      await client.signIn();
    } catch (error) {
      pendingRegistration = false;
      throw error;
    }
    return;
  }
  const uid = user.uid;
  const [details, photo] = bike
    ? await Promise.all([
        client.store.privateDetails(bike.id),
        bike.photoVersion ? client.store.photo(bike.id) : "",
      ])
    : [null, ""];
  if (user?.uid !== uid) return;
  editing = bike?.id || null;
  form.reset();
  resetPhoto();
  photoPreview(photo);
  if (bike) {
    for (const [key, value] of Object.entries({
      ...bike,
      ...details,
      rateHour: (bike.rateHourCents / 100).toFixed(2),
      rateDay: (bike.rateDayCents / 100).toFixed(2),
    })) {
      const field = form.elements.namedItem(key);
      if (field) {
        if (field.type === "checkbox") field.checked = value;
        else field.value = value;
      }
    }
  }
  $("bike-title").textContent = bike
    ? "Your bike details"
    : "Register your bike";
  step(false);
  listingFields();
  $("bike-dialog").showModal();
  form.elements.brand.focus();
}
$("register-button").onclick = () =>
  busy($("register-button"), () => openBike());
$("next-button").onclick = () => {
  if (photoBusy) return;
  if (form.reportValidity()) {
    step(true);
    form.elements.published.focus();
  }
};
$("back-button").onclick = () => {
  step(false);
  form.elements.brand.focus();
};
form.onsubmit = (event) => {
  event.preventDefault();
  if (photoBusy) return;
  if ($("listing-step").hidden) {
    $("next-button").click();
    return;
  }
  $("bike-error").textContent = "";
  busy(
    $("save-button"),
    async () => {
      const input = {};
      for (const field of form.elements)
        if (field.name)
          input[field.name] =
            field.type === "checkbox" ? field.checked : field.value;
      input.photoDataUrl = selectedPhoto;
      await client.store.saveBike(input, editing);
      $("bike-dialog").close();
      tab("mine");
      notice(
        input.published
          ? "Bike saved and published. You can review requests here."
          : "Bike saved privately. Only you can view this registration.",
      );
    },
    "bike-error",
  );
};
function openRequest(bike) {
  requestedBike = bike.id;
  $("request-form").reset();
  $("request-error").textContent = "";
  $("request-bike").textContent =
    `${bike.brand} ${bike.model} · ${money(bike.rateHourCents)} / hour · ${money(bike.rateDayCents)} / day`;
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  for (const name of ["startDate", "endDate"]) {
    $("request-form").elements[name].min = today;
    $("request-form").elements[name].value = today;
  }
  const quote = () => {
    const start = new Date($('request-form').elements.startDate.value + 'T00:00:00Z');
    const end = new Date($('request-form').elements.endDate.value + 'T00:00:00Z');
    const days = Math.round((end - start) / 86400000) + 1;
    $('request-total').textContent = days >= 1 && days <= 31
      ? `${days} ${days === 1 ? 'day' : 'days'} × ${money(bike.rateDayCents)} = ${money(days * bike.rateDayCents)} CAD · Test checkout after owner acceptance`
      : 'Choose an end date on or after the start, within 30 days.';
  };
  $('request-form').elements.startDate.onchange = quote;
  $('request-form').elements.endDate.onchange = quote;
  quote();
  $("request-dialog").showModal();
}
$("request-form").onsubmit = (event) => {
  event.preventDefault();
  $("request-error").textContent = "";
  busy(
    event.submitter,
    async () => {
      await client.store.requestRental(
        requestedBike,
        Object.fromEntries(new FormData(event.target)),
      );
      $("request-dialog").close();
      tab("requests");
      notice("Request sent. Check Rental requests for the owner’s response.");
    },
    "request-error",
  );
};
function openReport(bike) {
  reportedBike = bike.id;
  $("report-form").reset();
  $("report-error").textContent = "";
  $("report-bike").textContent = `${bike.brand} ${bike.model}`;
  $("report-form").elements.contactEmail.value = user?.email || "";
  $("report-dialog").showModal();
  $("report-form").elements.lastSeenLocation.focus();
}
$("report-form").onsubmit = (event) => {
  event.preventDefault();
  $("report-error").textContent = "";
  busy(
    event.submitter,
    async () => {
      const data = Object.fromEntries(new FormData(event.target));
      const rewardText = String(data.reward || "").trim();
      let rewardCents = 0;
      if (rewardText) {
        if (!/^\d+(?:\.\d{1,2})?$/.test(rewardText))
          throw new Error("Reward must be a CAD amount like 50 or 50.00.");
        rewardCents = Math.round(Number(rewardText) * 100);
        if (rewardCents > 100000)
          throw new Error("Reward must be at most $1,000.");
      }
      await client.store.reportStolen(reportedBike, {
        lastSeenLocation: data.lastSeenLocation,
        description: data.description,
        contactEmail: data.contactEmail,
        rewardCents,
      });
      $("report-dialog").close();
      notice("Reported. It is now on the public Missing bikes page.");
    },
    "report-error",
  );
};
$("auth-button").onclick = () =>
  busy($("auth-button"), () => (user ? client.signOut() : client.signIn()));
let connectInstance = null;
let connectGeneration = 0;
function resetConnect() {
  connectGeneration += 1;
  connectInstance?.logout().catch(() => {});
  connectInstance = null;
  $('connect-banner').replaceChildren();
  $('connect-panel').hidden = true;
}
async function refreshConnect() {
  const uid = user?.uid, generation = connectGeneration;
  if (!uid || !client.payments?.connectEnabled) return;
  $('connect-panel').hidden = false;
  const status = await client.connectAction('status');
  if (user?.uid !== uid || connectGeneration !== generation) return;
  $('connect-status').textContent = status.ready
    ? 'Ready to receive test rental payments. BikeBetter keeps 0%.'
    : status.exists ? 'Finish Stripe’s test setup before renters can pay for your bikes.'
      : 'Set up Stripe when you want to test renting out a bike. Registration does not require it.';
  $('connect-setup').textContent = status.exists ? 'Continue Stripe setup' : 'Set up test payments';
  $('connect-dashboard').hidden = !status.exists;
  if (status.exists && client.payments.publishableKey && !connectInstance) {
    const { loadConnectAndInitialize } = await import('/static/stripe-connect-loader.js');
    if (user?.uid !== uid || connectGeneration !== generation || connectInstance) return;
    connectInstance = loadConnectAndInitialize({
      publishableKey: client.payments.publishableKey,
      fetchClientSecret: async () => {
        if (user?.uid !== uid || connectGeneration !== generation) throw new Error('Sign in again to view Stripe.');
        const session = await client.connectAction('banner');
        if (user?.uid !== uid || connectGeneration !== generation) throw new Error('Account changed.');
        return session.clientSecret;
      },
      appearance: { variables: { colorPrimary: '#14467d', fontFamily: 'IBM Plex Sans, sans-serif' } },
    });
    const banner = connectInstance.create('notification-banner');
    banner.setOnLoadError(() => { if (user?.uid === uid) notice('Stripe’s reminder panel could not load. Use Continue Stripe setup to check your details.', true); });
    $('connect-banner').append(banner);
  }
}
async function ownerAction(action) {
  const uid = user?.uid;
  const result = await client.connectAction(action);
  if (user?.uid !== uid) return;
  const url = new URL(result.url);
  if (url.protocol !== 'https:' || !(url.hostname === 'stripe.com' || url.hostname.endsWith('.stripe.com'))) throw new Error('Stripe returned an invalid setup link.');
  location.assign(url.href);
}
for (const [id, action] of [['connect-setup', 'onboarding'], ['connect-dashboard', 'dashboard'], ['connect-refresh', 'status']]) {
  $(id).addEventListener('click', async () => {
    $(id).disabled = true;
    try { if (action === 'status') await refreshConnect(); else await ownerAction(action); }
    catch (error) { notice(errorMessage(error), true); }
    finally { $(id).disabled = false; }
  });
}

async function start() {
  try {
    const module = await import("./firebase-client.js");
    errorMessage = module.friendlyError;
    client = await module.connectFirebase();
    $('payment-info').textContent = client.payments?.enabled
      ? 'Stripe test checkout · 0% BikeBetter commission. No real money moves. Pay the daily total after the owner accepts and finishes Stripe setup.'
      : 'Stripe test checkout is not connected yet. You can still send and manage rental requests.';
    if (client.useEmulators)
      notice("Local test mode · Changes are stored in the Firebase emulators.");
    let listings = [];
    client.store.watchListings(
      (bikes) => {
        listings = bikes;
        renderBikes("listings", bikes);
      },
      (error) => empty("listings", errorMessage(error)),
    );
    client.onAuth((current) => {
      for (const unsubscribe of privateListeners) unsubscribe();
      privateListeners = [];
      for (const direction of ['incoming', 'outgoing']) {
        requestRows[direction] = []; paymentRows[direction].clear();
      }
      resetConnect();
      user = current;
      $("auth-button").disabled = false;
      $("register-button").disabled = false;
      $("auth-button").textContent = user ? "Sign out" : "Sign in with Google";
      $("account-name").textContent = user?.displayName || user?.email || "";
      for (const dialog of document.querySelectorAll("dialog[open]"))
        dialog.close();
      form.reset();
      resetPhoto();
      $("request-form").reset();
      $("report-form").reset();
      editing = requestedBike = reportedBike = null;
      myReports.clear();
      myBikes = [];
      renderBikes("listings", listings);
      for (const id of ["my-bikes", "incoming", "outgoing"])
        empty(
          id,
          user
            ? "Loading…"
            : "Sign in with Google to view your bikes and requests.",
        );
      if (user) {
        const uid = user.uid;
        refreshConnect().catch(error => { if (user?.uid === uid) notice(errorMessage(error), true); });
        if (!returnHandled && checkoutReturn.has('connect')) {
          returnHandled = true; tab('mine');
          history.replaceState(null, '', '/rentals');
          notice(checkoutReturn.get('connect') === 'refresh'
            ? 'That setup link expired. Select Continue Stripe setup for a fresh link.'
            : 'Welcome back. Checking your Stripe setup status…');
        }
        const guarded = (fn) => (data) => {
          if (user?.uid === uid) fn(data);
        };
        try {
          privateListeners.push(
            client.store.watchMyBikes(
              guarded((bikes) => {
                myBikes = bikes;
                renderBikes("my-bikes", bikes, true);
              }),
              (error) => empty("my-bikes", errorMessage(error)),
            ),
          );
          privateListeners.push(
            client.store.watchMyReports(
              guarded((rows) => {
                myReports.clear();
                for (const row of rows)
                  if (row.status === "missing") myReports.set(row.bikeId, row);
                renderBikes("my-bikes", myBikes, true);
              }),
              (error) => notice(errorMessage(error), true),
            ),
          );
          for (const direction of ["incoming", "outgoing"])
            privateListeners.push(
              client.store.watchRequests(
                direction,
                guarded((data) =>
                  renderRequests(direction, data, direction === "incoming"),
                ),
                (error) => empty(direction, errorMessage(error)),
              ),
            );
          for (const direction of ['incoming', 'outgoing']) privateListeners.push(
            client.store.watchPayments(direction, guarded(data => {
              paymentRows[direction] = new Map(data.map(row => [row.id, row]));
              renderRequests(direction, requestRows[direction], direction === 'incoming');
            }), error => notice(errorMessage(error), true)),
          );
        } catch (error) {
          notice(errorMessage(error), true);
        }
        if (pendingRegistration || location.hash === "#register") {
          pendingRegistration = false;
          history.replaceState(null, "", "/rentals");
          openBike().catch((error) => notice(errorMessage(error), true));
        }
        if (!returnHandled && checkoutReturn.has('checkout') && checkoutReturn.has('request_id')) {
          returnHandled = true; tab('requests');
          history.replaceState(null, '', '/rentals');
          notice('Checking your payment with Stripe…');
          client.paymentAction(checkoutReturn.get('checkout') === 'cancelled' ? 'cancel' : 'refresh', checkoutReturn.get('request_id'))
            .then(result => notice(result.status === 'paid' ? 'Test payment confirmed. No real money was charged.' : `Payment status: ${result.status}. You can retry from Rental requests.`))
            .catch(error => notice(errorMessage(error), true));
        }
      }
    });
  } catch (error) {
    notice(errorMessage(error), true);
    empty(
      "listings",
      "Bike services could not connect. Reload this page to try again.",
    );
    $("auth-button").textContent = "Unavailable";
  }
}
start();
