// Shared by the no-build browser UI and emulator integration tests.
import { validPhoto } from "./bike-photo.js";
export const BIKE_TYPES = [
  "Hybrid",
  "Road",
  "City",
  "Mountain",
  "Electric",
  "Cargo",
  "Other",
];

function clean(value, label, max, required = false) {
  const result = String(value ?? "").trim();
  if ((required && !result) || result.length > max)
    throw new Error(
      `${label} ${required ? "is required and " : ""}must be at most ${max} characters.`,
    );
  return result;
}

export function bikeInput(input) {
  const published = input.published === true;
  const money = (value, label) => {
    const text = String(value ?? "").trim();
    if (!text && !published) return 0;
    if (!/^\d+(?:\.\d{1,2})?$/.test(text))
      throw new Error(
        `${label} must be a CAD amount with at most two decimal places.`,
      );
    const cents = Math.round(Number(text) * 100);
    if (cents > 100000 || (published && cents <= 0))
      throw new Error(
        `${label} must be between $0.01 and $1,000 for a listing.`,
      );
    return cents;
  };
  if (!BIKE_TYPES.includes(input.type)) throw new Error("Choose a bike type.");
  return {
    public: {
      brand: clean(input.brand, "Brand", 60, true),
      model: clean(input.model, "Model", 80, true),
      type: input.type,
      colour: clean(input.colour, "Colour", 40, true),
      frameSize: clean(input.frameSize, "Frame size", 40),
      neighbourhood: clean(input.neighbourhood, "Neighbourhood", 80, published),
      description: clean(input.description, "Description", 1000),
      rateHourCents: money(input.rateHour, "Hourly rate"),
      rateDayCents: money(input.rateDay, "Daily rate"),
      published,
    },
    private: {
      serialNumber: clean(input.serialNumber, "Serial number", 80),
      notes: clean(input.notes, "Private notes", 1000),
    },
  };
}

export function createBikeStore({ db, auth, firestore: f }) {
  const user = () => {
    const u = auth.currentUser;
    if (!u || !u.emailVerified || !u.email)
      throw new Error("Sign in with your verified Google account first.");
    return u;
  };
  const bikeRef = (id) => f.doc(db, "bikes", id);
  const requestRef = (id) => f.doc(db, "rentalRequests", id);
  const reportRef = (id) => f.doc(db, "theftReports", id);
  const rows = (snap) =>
    snap.docs.map((doc) => ({ id: doc.id, ...doc.data() }));
  const watch = (constraints, success, error, collection = "bikes") =>
    f.onSnapshot(
      f.query(f.collection(db, collection), ...constraints, f.limit(100)),
      (snap) => success(rows(snap)),
      error,
    );
  return {
    watchListings: (success, error) =>
      watch(
        [f.where("published", "==", true), f.where("available", "==", true)],
        success,
        error,
      ),
    watchMyBikes: (success, error) =>
      watch([f.where("ownerUid", "==", user().uid)], success, error),
    watchRequests: (direction, success, error) =>
      watch(
        [
          f.where(
            direction === "incoming" ? "ownerUid" : "renterUid",
            "==",
            user().uid,
          ),
        ],
        success,
        error,
        "rentalRequests",
      ),
    watchPayments: (direction, success, error) => watch([
      f.where(direction === 'incoming' ? 'ownerUid' : 'renterUid', '==', user().uid),
    ], success, error, 'rentalPayments'),

    // Public: anyone can watch the missing-bike feed without signing in.
    watchMissing: (success, error) =>
      watch([f.where("status", "==", "missing")], success, error, "theftReports"),
    watchMyReports: (success, error) =>
      watch([f.where("ownerUid", "==", user().uid)], success, error, "theftReports"),

    async reportStolen(bikeId, { lastSeenLocation, description, contactEmail, rewardCents } = {}) {
      const u = user();
      const location = clean(lastSeenLocation, "Last seen location", 120, true);
      const desc = clean(description, "Description", 1000);
      const email = clean(contactEmail || u.email, "Contact email", 254, true);
      const reward =
        Number.isInteger(rewardCents) && rewardCents >= 0 && rewardCents <= 100000
          ? rewardCents
          : 0;
      const bikeSnap = await f.getDoc(bikeRef(bikeId));
      if (!bikeSnap.exists() || bikeSnap.data().ownerUid !== u.uid)
        throw new Error("This bike is not in your account.");
      const bike = bikeSnap.data();
      let photoDataUrl = "";
      if (bike.photoVersion) {
        try {
          photoDataUrl = await this.photo(bikeId);
        } catch {
          photoDataUrl = "";
        }
      }
      const ref = f.doc(f.collection(db, "theftReports"));
      await f.setDoc(ref, {
        bikeId,
        ownerUid: u.uid,
        title: `${bike.brand} ${bike.model}`,
        colour: bike.colour || "",
        type: bike.type || "",
        neighbourhood: bike.neighbourhood || "",
        lastSeenLocation: location,
        description: desc,
        contactEmail: email,
        rewardCents: reward,
        status: "missing",
        photoDataUrl:
          photoDataUrl && photoDataUrl.length <= 220000 ? photoDataUrl : "",
        createdAt: f.serverTimestamp(),
        updatedAt: f.serverTimestamp(),
      });
      // A stolen bike should not stay rentable: pull any live listing.
      if (bike.published)
        await f.updateDoc(bikeRef(bikeId), {
          published: false,
          updatedAt: f.serverTimestamp(),
        });
      return ref.id;
    },

    async markRecovered(reportId) {
      const u = user();
      await f.runTransaction(db, async (tx) => {
        const snap = await tx.get(reportRef(reportId));
        if (!snap.exists() || snap.data().ownerUid !== u.uid)
          throw new Error("This report is not in your account.");
        tx.update(snap.ref, {
          status: "recovered",
          updatedAt: f.serverTimestamp(),
        });
      });
    },

    async privateDetails(id) {
      user();
      const result = await f.getDoc(
        f.doc(db, "bikes", id, "private", "details"),
      );
      return result.exists() ? result.data() : { serialNumber: "", notes: "" };
    },

    async photo(id) {
      const result = await f.getDoc(f.doc(db, "bikes", id, "photos", "main"));
      const value = result.exists() ? result.data().dataUrl : "";
      return validPhoto(value) ? value : "";
    },

    async saveBike(input, id = null) {
      const u = user(),
        data = bikeInput(input);
      const photo = input.photoDataUrl;
      if (photo !== undefined && photo !== null && !validPhoto(photo)) {
        throw new Error("Choose a valid bike photo before saving.");
      }
      const ref = id ? bikeRef(id) : f.doc(f.collection(db, "bikes"));
      await f.runTransaction(db, async (tx) => {
        const current = id ? await tx.get(ref) : null;
        if (id && (!current.exists() || current.data().ownerUid !== u.uid))
          throw new Error("This bike is not in your account.");
        const existing = current?.exists() ? current.data() : null;
        tx.set(ref, {
          ...data.public,
          photoVersion:
            photo === undefined
              ? existing?.photoVersion || ""
              : photo === null
                ? ""
                : crypto.randomUUID(),
          ownerUid: u.uid,
          available: existing?.available ?? true,
          activeRequestId: existing?.activeRequestId ?? "",
          createdAt: existing?.createdAt ?? f.serverTimestamp(),
          updatedAt: f.serverTimestamp(),
        });
        tx.set(f.doc(db, "bikes", ref.id, "private", "details"), {
          ...data.private,
          updatedAt: f.serverTimestamp(),
        });
        if (photo)
          tx.set(f.doc(db, "bikes", ref.id, "photos", "main"), {
            dataUrl: photo,
            updatedAt: f.serverTimestamp(),
          });
        else if (photo === null && existing?.photoVersion)
          tx.delete(f.doc(db, "bikes", ref.id, "photos", "main"));
      });
      return ref.id;
    },

    async unpublish(id) {
      user();
      await f.updateDoc(bikeRef(id), {
        published: false,
        updatedAt: f.serverTimestamp(),
      });
    },

    async removeBike(id) {
      const u = user();
      await f.runTransaction(db, async (tx) => {
        const snap = await tx.get(bikeRef(id));
        if (!snap.exists() || snap.data().ownerUid !== u.uid)
          throw new Error("Bike not found in your account.");
        if (snap.data().activeRequestId)
          throw new Error(
            "Mark the active rental returned before removing this bike.",
          );
        tx.delete(f.doc(db, "bikes", id, "private", "details"));
        if (snap.data().photoVersion)
          tx.delete(f.doc(db, "bikes", id, "photos", "main"));
        tx.delete(bikeRef(id));
      });
    },

    async requestRental(id, { startDate, endDate, message }) {
      const u = user();
      const start = new Date(`${startDate}T00:00:00Z`),
        end = new Date(`${endDate}T00:00:00Z`);
      if (
        !/^\d{4}-\d{2}-\d{2}$/.test(startDate) ||
        !/^\d{4}-\d{2}-\d{2}$/.test(endDate) ||
        !Number.isFinite(start.getTime()) ||
        !Number.isFinite(end.getTime()) ||
        start.toISOString().slice(0, 10) !== startDate ||
        end.toISOString().slice(0, 10) !== endDate ||
        end < start ||
        end - start > 30 * 86400000 ||
        start.getTime() < Date.now() - 86400000
      ) {
        throw new Error(
          "Choose dates from today onward, with an end date no more than 30 days after the start.",
        );
      }
      const ref = f.doc(f.collection(db, "rentalRequests"));
      await f.runTransaction(db, async (tx) => {
        const snap = await tx.get(bikeRef(id));
        if (!snap.exists()) throw new Error("This bike is no longer listed.");
        const bike = snap.data();
        if (!bike.published || !bike.available || bike.activeRequestId)
          throw new Error("This bike is no longer available.");
        if (bike.ownerUid === u.uid)
          throw new Error("You cannot request your own bike.");
        tx.set(ref, {
          bikeId: id,
          bikeTitle: `${bike.brand} ${bike.model}`,
          ownerUid: bike.ownerUid,
          renterUid: u.uid,
          renterName: clean(
            u.displayName || u.email.split("@")[0],
            "Your name",
            100,
            true,
          ),
          renterEmail: u.email,
          ownerEmail: "",
          startAt: f.Timestamp.fromDate(start),
          endAt: f.Timestamp.fromDate(end),
          message: clean(message, "Message", 500),
          status: "pending",
          rateHourCents: bike.rateHourCents,
          rateDayCents: bike.rateDayCents,
          createdAt: f.serverTimestamp(),
          updatedAt: f.serverTimestamp(),
        });
      });
      return ref.id;
    },

    async updateRequest(id, action) {
      const u = user();
      if (!["accepted", "declined", "cancelled", "completed"].includes(action))
        throw new Error("Unknown request action.");
      await f.runTransaction(db, async (tx) => {
        const req = await tx.get(requestRef(id));
        if (!req.exists()) throw new Error("Rental request not found.");
        const rental = req.data();
        if (action === "cancelled") {
          if (rental.renterUid !== u.uid || rental.status !== "pending")
            throw new Error(
              "Only your pending requests can be cancelled here.",
            );
        } else if (rental.ownerUid !== u.uid)
          throw new Error("Only the bike owner can do that.");
        if (action === "accepted" || action === "completed") {
          const snap = await tx.get(bikeRef(rental.bikeId));
          if (!snap.exists())
            throw new Error("This bike registration no longer exists.");
          const bike = snap.data();
          if (action === "accepted") {
            if (
              rental.status !== "pending" ||
              !bike.published ||
              !bike.available ||
              bike.activeRequestId
            )
              throw new Error(
                "This bike already has an active rental or is no longer available.",
              );
            tx.update(snap.ref, {
              available: false,
              activeRequestId: id,
              updatedAt: f.serverTimestamp(),
            });
          } else {
            if (rental.status !== "accepted" || bike.activeRequestId !== id)
              throw new Error("This is not the active rental.");
            tx.update(snap.ref, {
              available: true,
              activeRequestId: "",
              updatedAt: f.serverTimestamp(),
            });
          }
        } else if (action === "declined" && rental.status !== "pending")
          throw new Error("Only pending requests can be declined.");
        const changes = { status: action, updatedAt: f.serverTimestamp() };
        if (action === "accepted") changes.ownerEmail = u.email;
        tx.update(req.ref, changes);
      });
    },
  };
}
