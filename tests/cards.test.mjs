/**
 * Tests des cartes Lovelace (www/sncf-trains-cards.js), sans navigateur :
 * le script est exécuté dans un contexte vm avec le strict nécessaire
 * (HTMLElement, customElements, horloge figée).
 *
 *   TZ=Europe/Paris node --test tests/cards.test.mjs
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

process.env.TZ = "Europe/Paris";

const SOURCE = readFileSync(
  new URL("../custom_components/sncf_trains/www/sncf-trains-cards.js", import.meta.url),
  "utf8",
);

const DEVICE = "SNCF L'Isle-Jourdain → Saint-Agne (Toulouse)";
const STATIONS = { departure_name: "L'Isle-Jourdain", arrival_name: "Saint-Agne (Toulouse)" };

/** hass minimal : un capteur « Train N » par train, sur l'appareil dev1. */
function makeHass(trains, devices = { dev1: { name: DEVICE } }) {
  const entities = {};
  const states = {};
  trains.forEach((train, index) => {
    const id = `sensor.train_${index + 1}`;
    const num = String(870300 + 2 * index);
    entities[id] = { entity_id: id, device_id: train.device ?? "dev1" };
    states[id] = {
      state: train.planned,
      last_updated: train.planned,
      attributes: {
        train_uid: `${num}_${train.planned}`,
        train_num: num,
        status: "on_time",
        delay_minutes: 0,
        departure_delay_minutes: 0,
        ...STATIONS,
        ...train.attributes,
      },
    };
  });
  return { locale: { language: "fr" }, devices, entities, states };
}

/** HTML d'une carte, rendue à l'heure ``now``. */
function render(type, config, hass, now) {
  const fixed = new Date(now).getTime();
  class FakeDate extends Date {
    constructor(...args) {
      if (args.length) super(...args);
      else super(fixed);
    }

    static now() {
      return fixed;
    }
  }
  class FakeElement {
    attachShadow() {
      this.shadowRoot = { innerHTML: "", addEventListener() {} };
      return this.shadowRoot;
    }

    dispatchEvent() {}
  }
  const elements = {};
  const context = vm.createContext({
    Date: FakeDate,
    HTMLElement: FakeElement,
    CustomEvent: class {},
    customElements: {
      get: (name) => elements[name],
      define: (name, cls) => {
        elements[name] = cls;
      },
    },
    window: {},
    document: { querySelector: () => null },
    navigator: { language: "fr" },
    setInterval: () => 0,
    clearInterval: () => {},
  });
  vm.runInContext(SOURCE, context);

  const card = new elements[type]();
  card.setConfig(config);
  card.hass = hass;
  return card.shadowRoot.innerHTML;
}

/* ------------------------------------------------------------------ */
/* Gares                                                               */
/* ------------------------------------------------------------------ */

test("tableau : gares lues sur le capteur quand l'appareil est renommé", () => {
  const hass = makeHass([{ planned: "2026-10-14T08:47:00+02:00" }], {
    dev1: { name: DEVICE, name_by_user: "Boulot" },
  });

  const html = render("sncf-departure-board", { device_id: "dev1" }, hass, "2026-10-14T08:00:00+02:00");

  assert.match(html, /<span class="dest-name">Saint-Agne \(Toulouse\)<\/span>/);
  assert.match(html, /<small>L&#39;Isle-Jourdain<\/small>/);
});

test("tuile : le titre reste le nom choisi pour l'appareil", () => {
  const hass = makeHass([{ planned: "2026-10-14T08:47:00+02:00" }], {
    dev1: { name: DEVICE, name_by_user: "Boulot" },
  });

  const html = render("sncf-train-tile", { device_id: "dev1" }, hass, "2026-10-14T08:00:00+02:00");

  assert.match(html, /<div class="route">Boulot<small>/);
});

test("tableau : sans attributs de gare, repli sur le nom de l'appareil", () => {
  const hass = makeHass([
    { planned: "2026-10-14T08:47:00+02:00", attributes: { departure_name: undefined, arrival_name: undefined } },
  ]);

  const html = render("sncf-departure-board", { device_id: "dev1" }, hass, "2026-10-14T08:00:00+02:00");

  assert.match(html, /<span class="dest-name">Saint-Agne \(Toulouse\)<\/span>/);
  assert.match(html, /<small>L&#39;Isle-Jourdain<\/small>/);
});

/* ------------------------------------------------------------------ */
/* Jour du départ                                                      */
/* ------------------------------------------------------------------ */

// Vendredi 16 octobre 2026, 20 h : prochain train lundi 19.
const FRIDAY_EVENING = "2026-10-16T20:00:00+02:00";
const MONDAY_TRAINS = [{ planned: "2026-10-19T07:47:00+02:00" }, { planned: "2026-10-19T08:47:00+02:00" }];

test("tuile : un train dans trois jours affiche sa date, pas « demain »", () => {
  const html = render("sncf-train-tile", { device_id: "dev1" }, makeHass(MONDAY_TRAINS), FRIDAY_EVENING);

  assert.match(html, /19\/10 <b>07:47<\/b>/);
  assert.doesNotMatch(html, /demain/);
});

test("tuile : un train le lendemain affiche « demain »", () => {
  const html = render(
    "sncf-train-tile",
    { device_id: "dev1" },
    makeHass([{ planned: "2026-10-17T07:47:00+02:00" }]),
    FRIDAY_EVENING,
  );

  assert.match(html, /demain <b>07:47<\/b>/);
});

test("tableau : un train parti la veille affiche « hier »", () => {
  const html = render(
    "sncf-departure-board",
    { device_id: "dev1", show_departed: true },
    makeHass([{ planned: "2026-10-14T23:50:00+02:00" }]),
    "2026-10-15T00:10:00+02:00",
  );

  assert.match(html, /23:50<span class="via">hier<\/span>/);
});

test("frise : une plage dans trois jours affiche sa date", () => {
  const html = render("sncf-train-timeline", { device_id: "dev1" }, makeHass(MONDAY_TRAINS), FRIDAY_EVENING);

  assert.match(html, /19\/10 · 07:00 – 09:00/);
});
