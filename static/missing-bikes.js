const $ = (id) => document.getElementById(id);
let client,
  user,
  reports = [];
let errorMessage = (error) =>
  error.message || "Connection failed. Please reload and try again.";
const money = (cents) =>
  new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD" }).format(
    cents / 100,
  );

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
function empty(text) {
  $("missing").replaceChildren(node("p", text, "empty"));
}
async function busy(button, action) {
  button.disabled = true;
  try {
    await action();
  } catch (error) {
    notice(errorMessage(error), true);
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
function illustration() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 220 100");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("bike-art");
  svg.innerHTML =
    '<g fill="none" stroke="currentColor" stroke-width="3"><circle cx="51" cy="68" r="26"/><circle cx="169" cy="68" r="26"/><path d="M51 68 81 26l30 42H51l17-32h86l15 32-22-54h-18M71 26h24M111 68l17-32"/></g>';
  return svg;
}

function render() {
  $("missing").replaceChildren();
  if (!reports.length)
    return empty(
      "No bikes are reported missing right now. If yours is stolen, report it from the Rentals page.",
    );
  const sorted = [...reports].sort(
    (a, b) => (b.createdAt?.seconds || 0) - (a.createdAt?.seconds || 0),
  );
  for (const report of sorted) {
    const card = node("article", "", "card");
    if (report.photoDataUrl) {
      const img = node("img", "", "bike-photo");
      img.alt = report.title;
      img.src = report.photoDataUrl;
      img.onerror = () => img.replaceWith(illustration());
      card.append(img);
    } else {
      card.append(illustration());
    }
    card.append(
      node("span", "Reported missing", "badge missing"),
      node("h3", report.title),
      node(
        "p",
        [report.type, report.colour, report.neighbourhood].filter(Boolean).join(" · "),
        "meta",
      ),
      node("p", `Last seen: ${report.lastSeenLocation}`, "meta last-seen"),
    );
    if (report.description)
      card.append(node("p", report.description, "description"));
    if (report.rewardCents > 0)
      card.append(
        node("p", `Reward: ${money(report.rewardCents)} for its safe return`, "rates reward"),
      );
    card.append(node("p", `Tips: ${report.contactEmail}`, "meta"));

    const buttons = node("div", "", "actions");
    if (user && report.ownerUid === user.uid) {
      buttons.append(
        action("Mark recovered", async () => {
          await client.store.markRecovered(report.id);
          notice("Marked recovered. It will drop off the missing list.");
        }),
      );
      if (report.rewardCents > 0) {
        const pay = action(
          `Pay reward ${money(report.rewardCents)} · Test`,
          async () => {
            const result = await client.rewardAction(report.id);
            const url = new URL(result.url);
            if (url.protocol !== "https:" || url.hostname !== "checkout.stripe.com")
              throw new Error("Invalid checkout link.");
            location.assign(url.href);
          },
          true,
        );
        if (!client.payments?.enabled) {
          pay.disabled = true;
          pay.textContent = "Reward checkout unavailable";
        }
        buttons.append(pay);
      }
    }
    if (buttons.childElementCount) card.append(buttons);
    $("missing").append(card);
  }
}

$("auth-button").onclick = () =>
  busy($("auth-button"), () => (user ? client.signOut() : client.signIn()));

async function start() {
  try {
    const module = await import("./firebase-client.js");
    errorMessage = module.friendlyError;
    client = await module.connectFirebase();
    if (client.useEmulators)
      notice("Local test mode · Reports are stored in the Firebase emulators.");
    const params = new URLSearchParams(location.search);
    if (params.get("reward") === "paid") {
      notice("Reward paid through Stripe test checkout. No real money was charged.");
      history.replaceState(null, "", "/missing");
    } else if (params.get("reward") === "cancelled") {
      notice("Reward checkout cancelled. The report is unchanged.");
      history.replaceState(null, "", "/missing");
    }
    client.store.watchMissing(
      (rows) => {
        reports = rows;
        render();
      },
      (error) => empty(errorMessage(error)),
    );
    client.onAuth((current) => {
      user = current;
      $("auth-button").disabled = false;
      $("auth-button").textContent = user ? "Sign out" : "Sign in with Google";
      $("account-name").textContent = user?.displayName || user?.email || "";
      render();
    });
  } catch (error) {
    notice(errorMessage(error), true);
    empty("Missing-bike services could not connect. Reload this page to try again.");
    $("auth-button").textContent = "Unavailable";
  }
}
start();
