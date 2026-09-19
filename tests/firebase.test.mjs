import assert from "node:assert/strict";
import { before, beforeEach, after, test } from "node:test";
import { readFile } from "node:fs/promises";
import {
  initializeTestEnvironment,
  assertFails,
  assertSucceeds,
} from "@firebase/rules-unit-testing";
import * as f from "firebase/firestore";
import { createBikeStore, bikeInput } from "../static/bike-store.js";

if (!process.env.FIRESTORE_EMULATOR_HOST)
  throw new Error(
    "Run npm run test:firebase; tests never use a live database.",
  );
let env;
const account = (uid) => ({
  uid,
  email: `${uid}@example.test`,
  emailVerified: true,
  displayName: uid,
});
const dbFor = (uid) =>
  env
    .authenticatedContext(uid, {
      email: `${uid}@example.test`,
      email_verified: true,
    })
    .firestore();
const storeFor = (uid) =>
  createBikeStore({
    db: dbFor(uid),
    auth: { currentUser: account(uid) },
    firestore: f,
  });
const input = (published = false) => ({
  brand: "Test",
  model: "City bike",
  type: "Hybrid",
  colour: "Blue",
  frameSize: "Medium",
  neighbourhood: "The Annex",
  description: "Emulator-only test bike.",
  serialNumber: "PRIVATE-SERIAL-123",
  notes: "Private purchase notes.",
  rateHour: "8.50",
  rateDay: "30",
  published,
});
const dates = () => {
  const start = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  return {
    startDate: start,
    endDate: start,
    message: "Emulator-only rental request.",
  };
};
const bikeDoc = (db, id) => f.doc(db, "bikes", id);
const reqDoc = (db, id) => f.doc(db, "rentalRequests", id);
before(async () => {
  env = await initializeTestEnvironment({
    projectId: "demo-safer-ride",
    firestore: { rules: await readFile("firestore.rules", "utf8") },
  });
});
beforeEach(async () => {
  await env.clearFirestore();
});
after(async () => {
  await env.cleanup();
});

test("registration is private by default, including the serial number", async () => {
  const owner = storeFor("owner"),
    id = await owner.saveBike(input());
  const bike = await f.getDoc(bikeDoc(dbFor("owner"), id));
  assert.equal(bike.data().published, false);
  assert.equal(bike.data().serialNumber, undefined);
  assert.equal(
    (await owner.privateDetails(id)).serialNumber,
    "PRIVATE-SERIAL-123",
  );
  await assertFails(f.getDoc(bikeDoc(dbFor("stranger"), id)));
  await assertFails(
    f.getDoc(bikeDoc(env.unauthenticatedContext().firestore(), id)),
  );
});

test("public listings never reveal private documents or serial-number fields", async () => {
  const id = await storeFor("owner").saveBike(input(true));
  const db = env.unauthenticatedContext().firestore();
  const snap = await assertSucceeds(
    f.getDocs(
      f.query(
        f.collection(db, "bikes"),
        f.where("published", "==", true),
        f.where("available", "==", true),
      ),
    ),
  );
  assert.equal(snap.size, 1);
  await assertFails(f.getDoc(f.doc(db, "bikes", id, "private", "details")));
  await assertFails(
    f.getDoc(f.doc(dbFor("renter"), "bikes", id, "private", "details")),
  );
  await assertFails(
    f.updateDoc(bikeDoc(dbFor("owner"), id), {
      serialNumber: "leak",
      updatedAt: f.serverTimestamp(),
    }),
  );
});

test("only an owner may edit, publish, unpublish or delete their registration", async () => {
  const owner = storeFor("owner"),
    id = await owner.saveBike(input());
  await assertFails(
    f.updateDoc(bikeDoc(dbFor("other"), id), {
      published: true,
      updatedAt: f.serverTimestamp(),
    }),
  );
  await assert.rejects(storeFor("other").removeBike(id));
  await owner.saveBike(input(true), id);
  assert.equal(
    (await f.getDoc(bikeDoc(dbFor("owner"), id))).data().published,
    true,
  );
  await owner.unpublish(id);
  await assertFails(f.getDoc(bikeDoc(dbFor("other"), id)));
  await owner.removeBike(id);
  await env.withSecurityRulesDisabled(async (c) => {
    assert.equal((await f.getDoc(bikeDoc(c.firestore(), id))).exists(), false);
  });
});

test("unverified accounts cannot write registrations", async () => {
  const id = await storeFor("owner").saveBike(input(true));
  const unverified = env
    .authenticatedContext("owner", {
      email: "owner@example.test",
      email_verified: false,
    })
    .firestore();
  await assertFails(
    f.updateDoc(bikeDoc(unverified, id), {
      colour: "Red",
      updatedAt: f.serverTimestamp(),
    }),
  );
});

test("money validation rejects invalid or fractional-cent input", () => {
  for (const price of ["-1", "8.555", "Infinity", "1001", "0"]) {
    assert.throws(() => bikeInput({ ...input(true), rateHour: price }));
  }
  assert.equal(bikeInput(input(true)).public.rateHourCents, 850);
});

test("rules enforce prices even if browser validation is bypassed", async () => {
  const id = await storeFor("owner").saveBike(input(true));
  for (const value of [-1, 1.5, 100001, 0]) {
    await assertFails(
      f.updateDoc(bikeDoc(dbFor("owner"), id), {
        rateDayCents: value,
        updatedAt: f.serverTimestamp(),
      }),
    );
  }
});

test("rental requests persist agreed prices and are private to the two parties", async () => {
  const id = await storeFor("owner").saveBike(input(true));
  const req = await storeFor("renter").requestRental(id, dates());
  const data = (await f.getDoc(reqDoc(dbFor("owner"), req))).data();
  assert.equal(data.rateHourCents, 850);
  assert.equal(data.renterEmail, "renter@example.test");
  assert.equal(data.status, "pending");
  await assertSucceeds(f.getDoc(reqDoc(dbFor("renter"), req)));
  await assertFails(f.getDoc(reqDoc(dbFor("other"), req)));
  await assertFails(
    f.getDoc(reqDoc(env.unauthenticatedContext().firestore(), req)),
  );
});

test("cannot rent your own bike, an unavailable bike, or a private bike", async () => {
  const id = await storeFor("owner").saveBike(input(true));
  await assert.rejects(storeFor("owner").requestRental(id, dates()));
  await storeFor("owner").unpublish(id);
  await assert.rejects(storeFor("renter").requestRental(id, dates()));
});

test("past, reversed, and overlong dates are rejected", async () => {
  const id = await storeFor("owner").saveBike(input(true));
  await assert.rejects(
    storeFor("renter").requestRental(id, {
      startDate: "2020-01-01",
      endDate: "2020-01-02",
    }),
  );
  await assert.rejects(
    storeFor("renter").requestRental(id, {
      startDate: "2030-01-03",
      endDate: "2030-01-01",
    }),
  );
  await assert.rejects(
    storeFor("renter").requestRental(id, {
      startDate: "2030-01-01",
      endDate: "2030-03-01",
    }),
  );
});

test("renter cannot change prices or accept their own request", async () => {
  const id = await storeFor("owner").saveBike(input(true));
  const req = await storeFor("renter").requestRental(id, dates());
  await assertFails(
    f.updateDoc(reqDoc(dbFor("renter"), req), {
      rateDayCents: 1,
      updatedAt: f.serverTimestamp(),
    }),
  );
  await assertFails(
    f.updateDoc(reqDoc(dbFor("renter"), req), {
      status: "accepted",
      updatedAt: f.serverTimestamp(),
    }),
  );
});

test("acceptance atomically reserves the bike and reveals owner contact only to the renter", async () => {
  const owner = storeFor("owner"),
    id = await owner.saveBike(input(true));
  const req = await storeFor("renter").requestRental(id, dates());
  await assertFails(
    f.updateDoc(reqDoc(dbFor("owner"), req), {
      status: "accepted",
      ownerEmail: "owner@example.test",
      updatedAt: f.serverTimestamp(),
    }),
  );
  await owner.updateRequest(req, "accepted");
  const bike = (await f.getDoc(bikeDoc(dbFor("owner"), id))).data();
  assert.equal(bike.available, false);
  assert.equal(bike.activeRequestId, req);
  assert.equal(
    (await f.getDoc(reqDoc(dbFor("renter"), req))).data().ownerEmail,
    "owner@example.test",
  );
  await assert.rejects(storeFor("other").requestRental(id, dates()));
  await assert.rejects(owner.removeBike(id));
});

test("two concurrent accepts cannot reserve the same bike twice", async () => {
  const owner = storeFor("owner"),
    id = await owner.saveBike(input(true));
  const a = await storeFor("renter").requestRental(id, dates());
  const b = await storeFor("other").requestRental(id, dates());
  const result = await Promise.allSettled([
    owner.updateRequest(a, "accepted"),
    owner.updateRequest(b, "accepted"),
  ]);
  assert.equal(result.filter((r) => r.status === "fulfilled").length, 1);
  const statusA = (await f.getDoc(reqDoc(dbFor("owner"), a))).data().status;
  const statusB = (await f.getDoc(reqDoc(dbFor("owner"), b))).data().status;
  assert.deepEqual([statusA, statusB].sort(), ["accepted", "pending"]);
});

test("returning the bike releases it, and active bookings cannot be cancelled by renters", async () => {
  const owner = storeFor("owner"),
    renter = storeFor("renter"),
    id = await owner.saveBike(input(true));
  const req = await renter.requestRental(id, dates());
  await owner.updateRequest(req, "accepted");
  await assert.rejects(renter.updateRequest(req, "cancelled"));
  await owner.updateRequest(req, "completed");
  const bike = (await f.getDoc(bikeDoc(dbFor("owner"), id))).data();
  assert.equal(bike.available, true);
  assert.equal(bike.activeRequestId, "");
  assert.equal(
    (await f.getDoc(reqDoc(dbFor("renter"), req))).data().status,
    "completed",
  );
});

test("pending requests can be cancelled or declined, including after a listing is removed", async () => {
  const owner = storeFor("owner"),
    renter = storeFor("renter"),
    id = await owner.saveBike(input(true));
  const first = await renter.requestRental(id, dates());
  await renter.updateRequest(first, "cancelled");
  const second = await renter.requestRental(id, dates());
  await owner.removeBike(id);
  await owner.updateRequest(second, "declined");
  assert.equal(
    (await f.getDoc(reqDoc(dbFor("renter"), second))).data().status,
    "declined",
  );
});
