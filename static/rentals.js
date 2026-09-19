const $ = (id) => document.getElementById(id);
let client,
  user,
  privateListeners = [],
  pendingRegistration = false,
  editing = null,
  requestedBike = null;
const money = (cents) =>
  new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD" }).format(
    cents / 100,
  );
const date = (stamp) =>
  stamp
    .toDate()
    .toLocaleDateString("en-CA", {
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
  $(id).replaceChildren();
  if (!bikes.length)
    return empty(
      id,
      own
        ? "No bikes registered yet. Register your first bike to keep its details together."
        : "No bikes are listed right now. Have a spare? Register it and choose to publish a rental listing.",
    );
  for (const bike of bikes) {
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
          : "Available to request",
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
    const buttons = node("div", "", "actions");
    if (own) {
      buttons.append(action("View / edit", () => openBike(bike)));
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
  }
}
function renderRequests(id, requests, incoming) {
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
    if (incoming && request.status === "accepted")
      buttons.append(change("Mark returned", "completed"));
    card.append(buttons);
    $(id).append(card);
  }
}
const form = $("bike-form");
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
  const details = bike ? await client.store.privateDetails(bike.id) : null;
  if (user?.uid !== uid) return;
  editing = bike?.id || null;
  form.reset();
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
$("auth-button").onclick = () =>
  busy($("auth-button"), () => (user ? client.signOut() : client.signIn()));
async function start() {
  try {
    const module = await import("./firebase-client.js");
    errorMessage = module.friendlyError;
    client = await module.connectFirebase();
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
      user = current;
      $("auth-button").disabled = false;
      $("register-button").disabled = false;
      $("auth-button").textContent = user ? "Sign out" : "Sign in with Google";
      $("account-name").textContent = user?.displayName || user?.email || "";
      for (const dialog of document.querySelectorAll("dialog[open]"))
        dialog.close();
      form.reset();
      $("request-form").reset();
      editing = requestedBike = null;
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
        const guarded = (fn) => (data) => {
          if (user?.uid === uid) fn(data);
        };
        try {
          privateListeners.push(
            client.store.watchMyBikes(
              guarded((bikes) => renderBikes("my-bikes", bikes, true)),
              (error) => empty("my-bikes", errorMessage(error)),
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
        } catch (error) {
          notice(errorMessage(error), true);
        }
        if (pendingRegistration || location.hash === "#register") {
          pendingRegistration = false;
          history.replaceState(null, "", "/rentals");
          openBike().catch((error) => notice(errorMessage(error), true));
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
