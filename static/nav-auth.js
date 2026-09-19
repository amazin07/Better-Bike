// Sign in and sign out for the shared navbar on the map page, which has no account code
// of its own. The Rentals page wires the same #auth-button itself in rentals.js, so the
// wording ("Sign in with Google", "Sign out", "Unavailable") matches on both pages.
// Failures never touch the map: the button just reports "Unavailable".
const button = document.getElementById("auth-button");
const accountName = document.getElementById("account-name");

// Errors go to the map's own notice line (index.html listens for this event).
function tell(message) {
  window.dispatchEvent(new CustomEvent("bikebetter:notice", { detail: message }));
}

async function start() {
  if (!button) return;
  let client;
  let user = null;
  let friendlyError = (error) =>
    error?.message || "Something went wrong. Please try again.";
  try {
    const module = await import("./firebase-client.js");
    friendlyError = module.friendlyError;
    client = await module.connectFirebase();
  } catch (error) {
    button.textContent = "Unavailable";
    button.title = friendlyError(error);
    return;
  }
  button.onclick = async () => {
    button.disabled = true;
    try {
      await (user ? client.signOut() : client.signIn());
    } catch (error) {
      tell(friendlyError(error));
    } finally {
      button.disabled = false;
    }
  };
  client.onAuth((current) => {
    user = current;
    button.disabled = false;
    button.textContent = user ? "Sign out" : "Sign in with Google";
    accountName.textContent = user?.displayName || user?.email || "";
  });
}

// Load Firebase after the page (and the map) has finished, so it never delays them.
if (document.readyState === "complete") start();
else window.addEventListener("load", start, { once: true });
