/**
 * Cartes Lovelace de l'intégration SNCF Trains.
 *
 *  - sncf-train-tile        : tuile « prochain train » (compte à rebours)
 *  - sncf-departure-board   : tableau des départs façon gare
 *  - sncf-train-timeline    : frise des départs de la plage horaire
 *
 * Les trois lisent les capteurs « Train N » des appareils (trajets) choisis.
 * Un appui sur un train ouvre la fiche du capteur correspondant.
 */

const DOMAIN = "sncf_trains";
const MINUTE = 60000;

/* ------------------------------------------------------------------ */
/* Textes                                                              */
/* ------------------------------------------------------------------ */

const TEXTS = {
  fr: {
    on_time: "À l'heure",
    delayed: "Retard",
    departed: "Parti",
    cancelled: "Supprimé",
    no_train: "Aucun train à venir",
    no_device: "Choisissez un trajet SNCF dans l'éditeur de la carte.",
    min: "min",
    in: "dans",
    now: "maintenant",
    next_possible: "Prochain départ possible :",
    arr: "arr.",
    departures: "Départs",
    time: "Heure",
    destination: "Destination",
    train: "Train",
    state: "État",
    trains: "trains",
    tomorrow: "demain",
    not_served: "Ne dessert plus la gare d'arrivée",
    legend_late: "En retard (trait = retard)",
    // Éditeur
    device_id: "Trajets",
    title: "Titre (facultatif)",
    next_count: "Trains suivants affichés",
    rows: "Nombre de lignes",
    show_departed: "Afficher les trains partis",
    start: "Début de la frise (HH:MM, facultatif)",
    end: "Fin de la frise (HH:MM, facultatif)",
  },
  en: {
    on_time: "On time",
    delayed: "Delayed",
    departed: "Departed",
    cancelled: "Cancelled",
    no_train: "No upcoming train",
    no_device: "Pick an SNCF journey in the card editor.",
    min: "min",
    in: "in",
    now: "now",
    next_possible: "Next possible departure:",
    arr: "arr.",
    departures: "Departures",
    time: "Time",
    destination: "Destination",
    train: "Train",
    state: "Status",
    trains: "trains",
    tomorrow: "tomorrow",
    not_served: "No longer serves the arrival station",
    legend_late: "Delayed (line = delay)",
    device_id: "Journeys",
    title: "Title (optional)",
    next_count: "Following trains shown",
    rows: "Number of rows",
    show_departed: "Show departed trains",
    start: "Timeline start (HH:MM, optional)",
    end: "Timeline end (HH:MM, optional)",
  },
};

function language(hass) {
  const lang =
    hass?.locale?.language ||
    hass?.language ||
    document.querySelector("home-assistant")?.hass?.locale?.language ||
    navigator.language ||
    "fr";
  return lang.toLowerCase().startsWith("fr") ? "fr" : "en";
}

function t(hass, key) {
  return TEXTS[language(hass)][key] ?? TEXTS.fr[key] ?? key;
}

/* ------------------------------------------------------------------ */
/* Données                                                             */
/* ------------------------------------------------------------------ */

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

/** « 19/11/2025 - 08:20 » (format des attributs) → Date. */
function parseSncfTime(value) {
  const match = /^(\d{2})\/(\d{2})\/(\d{4}) - (\d{2}):(\d{2})$/.exec(value || "");
  if (!match) return null;
  const [, d, m, y, hh, mm] = match.map(Number);
  return new Date(y, m - 1, d, hh, mm);
}

function formatTime(date) {
  if (!date) return "--:--";
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function toArray(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  return value ? [value] : [];
}

/** Nom du trajet à partir de l'appareil : « SNCF A → B » → { from: A, to: B }. */
function routeOf(hass, deviceId) {
  const device = hass?.devices?.[deviceId];
  const name = (device?.name_by_user || device?.name || "").replace(/^SNCF\s+/, "");
  const [from = "", to = ""] = name.split(/\s*→\s*/);
  return { name, from, to };
}

/** Un capteur « Train N » → train normalisé, ou null pour les autres capteurs. */
function toTrain(hass, entityId, deviceId, now) {
  const state = hass.states[entityId];
  const a = state?.attributes;
  if (!a || !a.train_uid) return null;

  const planned = Number.isNaN(Date.parse(state.state))
    ? parseSncfTime(a.base_departure_time)
    : new Date(state.state);
  if (!planned) return null;

  const depDelay = Number(a.departure_delay_minutes ?? a.delay_minutes ?? 0) || 0;
  const delay = Number(a.delay_minutes ?? depDelay) || 0;
  const real = new Date(planned.getTime() + depDelay * MINUTE);
  const arrival = parseSncfTime(a.arrival_time);

  let status = a.status || (a.cancelled ? "cancelled" : a.has_delay ? "delayed" : "on_time");
  // L'attribut n'est rafraîchi qu'à la mise à jour de l'intégration :
  // on marque le train parti dès son heure de départ réelle passée.
  if (status !== "cancelled" && real < now) status = "departed";

  return {
    entityId,
    deviceId,
    uid: a.train_uid,
    num: a.train_num || "",
    mode: a.commercial_mode || a.physical_mode || "",
    planned,
    real,
    arrival,
    delay,
    depDelay,
    status,
    cause: a.disruption_message || "",
    destinationServed: a.destination_served !== false,
    route: routeOf(hass, deviceId),
  };
}

/* ------------------------------------------------------------------ */
/* Base commune                                                        */
/* ------------------------------------------------------------------ */

const BASE_STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; height: 100%; box-sizing: border-box; }
  [data-entity] { cursor: pointer; }
  [data-entity]:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 8px; }
  .empty { padding: 16px; color: var(--secondary-text-color); }
  .num { font-variant-numeric: tabular-nums; }
`;

class SncfBaseCard extends HTMLElement {
  static defaults = {};

  setConfig(config) {
    if (!config || !toArray(config.device_id).length) {
      throw new Error("device_id is required");
    }
    this._config = { ...this.constructor.defaults, ...config, device_id: toArray(config.device_id) };
    this._signature = null;
    this._entitiesRef = null;
    this._render();
  }

  /** Capteurs des trajets : recalculés seulement quand le registre change. */
  _refreshEntityIds() {
    const hass = this._hass;
    if (!hass || !this._config) return [];
    if (hass.entities !== this._entitiesRef) {
      this._entitiesRef = hass.entities;
      const ids = new Set(this._config.device_id);
      this._entityIds = Object.values(hass.entities || {})
        .filter((e) => ids.has(e.device_id) && e.entity_id.startsWith("sensor."))
        .map((e) => [e.entity_id, e.device_id]);
    }
    return this._entityIds;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const signature = this._refreshEntityIds()
      .map(([id]) => hass.states[id]?.last_updated)
      .join("|") + language(hass);
    if (signature !== this._signature) {
      this._signature = signature;
      this._render();
    }
  }

  connectedCallback() {
    // Compte à rebours et trains partis : rafraîchis chaque minute.
    this._timer = setInterval(() => this._render(), 30000);
    this._render();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    return 3;
  }

  /** Trains des trajets, triés par départ prévu, sans doublon. */
  _trains() {
    if (!this._hass) return [];
    const now = new Date();
    const seen = new Set();
    return this._refreshEntityIds()
      .map(([id, deviceId]) => toTrain(this._hass, id, deviceId, now))
      .filter((train) => train && !seen.has(train.uid) && seen.add(train.uid))
      .sort((a, b) => a.planned - b.planned);
  }

  _root() {
    if (!this.shadowRoot) {
      const root = this.attachShadow({ mode: "open" });
      const open = (event) => {
        const el = event.target.closest("[data-entity]");
        if (!el) return;
        if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            detail: { entityId: el.dataset.entity },
            bubbles: true,
            composed: true,
          })
        );
      };
      root.addEventListener("click", open);
      root.addEventListener("keydown", open);
    }
    return this.shadowRoot;
  }

  _render() {
    if (!this._config || !this._hass) return;
    this._root().innerHTML = `<style>${BASE_STYLE}${this.constructor.style}</style>${this._html()}`;
  }

  _t(key) {
    return t(this._hass, key);
  }

  _clickable(train) {
    return `data-entity="${escapeHtml(train.entityId)}" tabindex="0" role="button"`;
  }

  static _form(fields) {
    return {
      schema: [
        {
          name: "device_id",
          required: true,
          selector: { device: { multiple: true, filter: { integration: DOMAIN } } },
        },
        { name: "title", selector: { text: {} } },
        ...fields,
      ],
      computeLabel: (schema) => t(null, schema.name),
    };
  }

  static getStubConfig(hass) {
    const device = Object.entries(hass?.devices || {}).find(
      ([, d]) => d.manufacturer === "SNCF" && d.model === "Trajet"
    );
    return { device_id: device ? [device[0]] : [] };
  }
}

/* ------------------------------------------------------------------ */
/* 1. Tuile « prochain train »                                         */
/* ------------------------------------------------------------------ */

const TRAIN_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2C8 2 4 2.5 4 6v9.5A3.5 3.5 0 0 0 7.5 19L6 20.5v.5h2.2l2-2h3.6l2 2H18v-.5L16.5 19a3.5 3.5 0 0 0 3.5-3.5V6c0-3.5-3.6-4-8-4zM7.5 17a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm3.5-7H6V6h5v4zm2 0V6h5v4h-5zm3.5 7a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z"/></svg>`;

class SncfTrainTile extends SncfBaseCard {
  static defaults = { next_count: 3 };

  static style = `
    .tile { padding: 14px 16px 12px; display: grid; gap: 10px; }
    .top { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .icon { width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center; flex: none;
      color: var(--c); background: color-mix(in srgb, var(--c) 18%, transparent); }
    .icon svg { width: 20px; height: 20px; fill: currentColor; }
    .route { font-weight: 500; line-height: 1.25; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
    .route small { display: block; font-size: .8em; font-weight: 400; color: var(--secondary-text-color); }
    .main { display: flex; align-items: flex-end; justify-content: space-between; gap: 8px; }
    .big { font-size: 2.2em; font-weight: 700; line-height: 1; letter-spacing: -.01em; }
    .big span { font-size: .45em; font-weight: 500; margin-left: 3px; letter-spacing: 0; }
    .dep { text-align: right; color: var(--secondary-text-color); font-size: .9em; line-height: 1.3; }
    .dep b { display: block; font-size: 1.25em; font-weight: 500; color: var(--primary-text-color); }
    .chip { display: inline-block; font-size: .8em; font-weight: 500; border-radius: 999px; padding: 2px 9px;
      color: var(--c); background: color-mix(in srgb, var(--c) 16%, transparent); }
    .cause { font-size: .85em; color: var(--error-color, #db4437); line-height: 1.35; }
    .next { display: flex; gap: 6px; border-top: 1px solid var(--divider-color); padding-top: 10px; }
    .next > div { flex: 1; min-width: 0; text-align: center; font-size: .82em; border-radius: 8px; padding: 4px 0;
      color: var(--c, var(--secondary-text-color)); background: color-mix(in srgb, var(--primary-text-color) 5%, transparent); }
    .next b { display: block; font-size: 1.15em; font-weight: 500; }
    .next .cancelled b { text-decoration: line-through; }
  `;

  static getConfigForm() {
    return SncfBaseCard._form([
      { name: "next_count", selector: { number: { min: 0, max: 5, step: 1, mode: "box" } } },
    ]);
  }

  _html() {
    const now = new Date();
    const trains = this._trains().filter((tr) => tr.status !== "departed");
    const route = routeOf(this._hass, this._config.device_id[0]);
    const title = this._config.title || route.name;

    if (!trains.length) {
      return `<ha-card><div class="tile">
        <div class="top"><div class="icon" style="--c: var(--disabled-text-color, #9e9e9e)">${TRAIN_ICON}</div>
        <div class="route">${escapeHtml(title)}</div></div>
        <div class="empty">${this._t("no_train")}</div></div></ha-card>`;
    }

    const [train, ...rest] = trains;
    const color = statusColor(train.status);
    const sub = [train.mode, train.num].filter(Boolean).join(" ")
      + (train.arrival ? ` · ${this._t("arr")} ${formatTime(train.arrival)}` : "");

    let main;
    let status;
    if (train.status === "cancelled") {
      const nextOk = rest.find((tr) => tr.status !== "cancelled");
      main = `<div class="big" style="color: var(--c)">${this._t("cancelled")}</div>
        <div class="dep num"><s>${formatTime(train.planned)}</s></div>`;
      const cause = train.cause && !/[.!?]$/.test(train.cause) ? `${train.cause}.` : train.cause;
      status = `<div class="cause">${escapeHtml(cause)}${cause && nextOk ? " " : ""}${
        nextOk ? `${this._t("next_possible")} ${formatTime(nextOk.real)}.` : ""}</div>`;
    } else {
      const minutes = Math.max(0, Math.ceil((train.real - now) / MINUTE));
      const big = minutes === 0
        ? this._t("now")
        : minutes < 180
          ? `${minutes >= 60 ? `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}` : minutes}<span>${minutes >= 60 ? "" : this._t("min")}</span>`
          : formatTime(train.real);
      const day = sameDay(train.real, now) ? "" : `${this._t("tomorrow")} `;
      main = `<div class="big num">${big}</div>
        <div class="dep num">${train.depDelay ? `<s>${formatTime(train.planned)}</s>` : day}<b>${formatTime(train.real)}</b></div>`;
      const label = train.status === "delayed" ? `${this._t("delayed")} +${train.delay} ${this._t("min")}` : this._t("on_time");
      status = `<div><span class="chip">${label}</span></div>${
        train.destinationServed ? "" : `<div class="cause">${this._t("not_served")}</div>`}`;
    }

    const next = rest.slice(0, Number(this._config.next_count) || 0).map((tr) => {
      const label = tr.status === "cancelled" ? this._t("cancelled").toLowerCase()
        : tr.status === "delayed" ? `+${tr.delay}` : this._t("on_time").toLowerCase();
      return `<div ${this._clickable(tr)} style="--c: ${tr.status === "on_time" ? "var(--secondary-text-color)" : statusColor(tr.status)}">
        <div class="${tr.status}"><b class="num">${formatTime(tr.real)}</b>${label}</div></div>`;
    }).join("");

    return `<ha-card><div class="tile" style="--c: ${color}">
      <div class="top" ${this._clickable(train)}>
        <div class="icon">${TRAIN_ICON}</div>
        <div class="route">${escapeHtml(title)}<small>${escapeHtml(sub)}</small></div>
      </div>
      <div class="main">${main}</div>
      ${status}
      ${next ? `<div class="next">${next}</div>` : ""}
    </div></ha-card>`;
  }
}

function statusColor(status) {
  return {
    on_time: "var(--success-color, #43a047)",
    delayed: "var(--warning-color, #ffa600)",
    cancelled: "var(--error-color, #db4437)",
    departed: "var(--disabled-text-color, #9e9e9e)",
  }[status] || "var(--primary-color)";
}

/* ------------------------------------------------------------------ */
/* 2. Tableau des départs (couleurs de gare, quel que soit le thème)   */
/* ------------------------------------------------------------------ */

class SncfDepartureBoard extends SncfBaseCard {
  static defaults = { rows: 6, show_departed: true };

  static style = `
    ha-card { --ha-card-background: #0d1f3c; background: #0d1f3c; }
    .board { background: #0d1f3c; color: #f4f6fa; font-family: "Barlow Condensed", "Roboto Condensed", "Arial Narrow", sans-serif; height: 100%; }
    .head { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 16px; background: #0a1830; }
    .head h2 { margin: 0; font-size: 1.35em; font-weight: 700; letter-spacing: .02em; text-transform: uppercase; }
    .head h2 small { font-weight: 500; color: #9fb3d6; font-size: .72em; margin-left: 8px; text-transform: none; }
    .clock { color: #ffd23f; font-size: 1.6em; font-weight: 700; }
    .scroll { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 1.12em; }
    th { text-align: left; font-weight: 500; font-size: .7em; letter-spacing: .12em; text-transform: uppercase; color: #9fb3d6; padding: 6px 16px; }
    td { padding: 8px 16px; border-top: 1px solid #1d3762; vertical-align: middle; }
    tr:nth-child(even) td { background: #12284d; }
    .t { color: #ffd23f; font-weight: 700; font-size: 1.2em; width: 1%; white-space: nowrap; }
    .dest { font-weight: 600; }
    .via { display: block; font-size: .78em; color: #9fb3d6; font-weight: 500; }
    .train { color: #c7d3e8; white-space: nowrap; }
    .st { text-align: right; white-space: nowrap; font-weight: 700; }
    .ok { color: #8fe39b; }
    .late { color: #1b1300; background: #ffd23f; padding: 1px 8px; border-radius: 3px; }
    .cancel { color: #fff; background: #d83a3a; padding: 1px 8px; border-radius: 3px; }
    tr.departed td { opacity: .45; }
    tr.cancelled .t, tr.cancelled .dest-name { text-decoration: line-through; text-decoration-color: #ff7a7a; }
    tr[data-entity]:focus-visible td { outline: none; background: #1d3762; }
    .empty { color: #9fb3d6; }
    @media (prefers-reduced-motion: no-preference) {
      .blink { animation: blink 1.6s steps(1) infinite; }
      @keyframes blink { 50% { opacity: .35; } }
    }
  `;

  static getConfigForm() {
    return SncfBaseCard._form([
      { name: "rows", selector: { number: { min: 1, max: 20, step: 1, mode: "box" } } },
      { name: "show_departed", selector: { boolean: {} } },
    ]);
  }

  getCardSize() {
    return 2 + Number(this._config?.rows || 6);
  }

  _html() {
    const now = new Date();
    let trains = this._trains();
    if (!this._config.show_departed) trains = trains.filter((tr) => tr.status !== "departed");
    trains = trains.slice(0, Number(this._config.rows) || 6);

    const route = routeOf(this._hass, this._config.device_id[0]);
    const title = this._config.title || this._t("departures");
    const station = this._config.title ? "" : route.from;

    const rows = trains.map((tr) => {
      const state = {
        on_time: `<span class="ok">${this._t("on_time")}</span>`,
        delayed: `<span class="late blink">+${tr.delay} ${this._t("min")}</span>`,
        cancelled: `<span class="cancel">${this._t("cancelled")}</span>`,
        departed: this._t("departed"),
      }[tr.status];
      const via = tr.status === "cancelled" || !tr.destinationServed
        ? tr.cause || (tr.destinationServed ? "" : this._t("not_served"))
        : "";
      const day = sameDay(tr.planned, now) ? "" : `<span class="via">${this._t("tomorrow")}</span>`;
      return `<tr class="${tr.status}" data-entity="${escapeHtml(tr.entityId)}" tabindex="0">
        <td class="t num">${formatTime(tr.planned)}${day}</td>
        <td class="dest"><span class="dest-name">${escapeHtml(tr.route.to)}</span>${via ? `<span class="via">${escapeHtml(via)}</span>` : ""}</td>
        <td class="train num">${escapeHtml([tr.mode, tr.num].filter(Boolean).join(" "))}</td>
        <td class="st">${state}</td></tr>`;
    }).join("");

    return `<ha-card><div class="board">
      <div class="head"><h2>${escapeHtml(title)}${station ? `<small>${escapeHtml(station)}</small>` : ""}</h2>
        <div class="clock num">${formatTime(now)}</div></div>
      ${rows
        ? `<div class="scroll"><table>
            <thead><tr><th>${this._t("time")}</th><th>${this._t("destination")}</th><th>${this._t("train")}</th><th style="text-align:right">${this._t("state")}</th></tr></thead>
            <tbody>${rows}</tbody></table></div>`
        : `<div class="empty">${this._t("no_train")}</div>`}
    </div></ha-card>`;
  }
}

/* ------------------------------------------------------------------ */
/* 3. Frise de la plage                                                */
/* ------------------------------------------------------------------ */

function atTime(day, hhmm) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(hhmm || "");
  if (!match) return null;
  const date = new Date(day);
  date.setHours(Number(match[1]), Number(match[2]), 0, 0);
  return date;
}

class SncfTrainTimeline extends SncfBaseCard {
  static defaults = {};

  static style = `
    .wrap { padding: 14px 16px 10px; }
    .head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
    .head h2 { margin: 0; font-size: 1.05em; font-weight: 500; }
    .head span { font-size: .82em; color: var(--secondary-text-color); }
    .scroll { overflow-x: auto; }
    svg { display: block; width: 100%; min-width: 480px; height: auto; }
    .axis { stroke: var(--secondary-text-color); stroke-width: 1.5; }
    .grid { stroke: var(--divider-color); stroke-width: 1; }
    .tick { fill: var(--secondary-text-color); font-size: 11px; }
    .now line { stroke: var(--primary-color); stroke-width: 1.5; stroke-dasharray: 3 3; }
    .now text { fill: var(--primary-color); font-size: 11px; font-weight: 700; }
    .lbl { fill: var(--primary-text-color); font-size: 11px; font-weight: 500; }
    .tag { font-size: 10px; font-weight: 700; }
    .seg { stroke: var(--warning-color, #ffa600); stroke-width: 3; stroke-linecap: round; }
    g[data-entity]:focus-visible circle { stroke: var(--primary-color); stroke-width: 3; }
    .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: .8em; color: var(--secondary-text-color); margin-top: 4px; }
    .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }
  `;

  static getConfigForm() {
    return SncfBaseCard._form([
      {
        type: "grid",
        name: "",
        schema: [
          { name: "start", selector: { text: {} } },
          { name: "end", selector: { text: {} } },
        ],
      },
    ]);
  }

  _html() {
    const now = new Date();
    const trains = this._trains();
    const route = routeOf(this._hass, this._config.device_id[0]);
    const title = this._config.title || route.name;

    if (!trains.length) {
      return `<ha-card><div class="wrap"><div class="head"><h2>${escapeHtml(title)}</h2></div>
        <div class="empty">${this._t("no_train")}</div></div></ha-card>`;
    }

    // Échelle : heures configurées, sinon heures pleines autour des trains.
    const first = trains[0].planned;
    const last = trains.reduce((max, tr) => (tr.real > max ? tr.real : max), first);
    let start = atTime(first, this._config.start);
    if (!start) {
      start = new Date(first);
      start.setMinutes(0, 0, 0);
    }
    let end = atTime(start, this._config.end);
    if (end && end <= start) end = new Date(end.getTime() + 24 * 60 * MINUTE);
    if (!end) {
      end = new Date(last);
      end.setMinutes(0, 0, 0);
      end = new Date(end.getTime() + 60 * MINUTE);
    }
    if (end - start < 60 * MINUTE) end = new Date(start.getTime() + 60 * MINUTE);

    const W = 620, LEFT = 30, RIGHT = 590, AXIS = 60;
    const x = (date) => LEFT + ((date - start) / (end - start)) * (RIGHT - LEFT);
    const inRange = (date) => date >= start && date <= end;

    const hours = (end - start) / (60 * MINUTE);
    const step = hours > 12 ? 3 : hours > 7 ? 2 : 1;
    const ticks = [];
    const tick = new Date(start);
    tick.setMinutes(0, 0, 0);
    if (tick < start) tick.setHours(tick.getHours() + 1);
    for (; tick <= end; tick.setHours(tick.getHours() + step)) {
      const tx = x(tick).toFixed(1);
      ticks.push(`<line class="grid" x1="${tx}" y1="${AXIS - 6}" x2="${tx}" y2="${AXIS + 6}"/>
        <text class="tick" x="${tx}" y="${AXIS + 46}" text-anchor="middle">${String(tick.getHours()).padStart(2, "0")}h</text>`);
    }

    // Étiquettes d'heure sur deux rangées pour éviter les chevauchements.
    let lastLabel = -Infinity;
    let row = 0;
    const marks = trains.filter((tr) => inRange(tr.planned)).map((tr) => {
      const px = x(tr.planned);
      const rx = inRange(tr.real) ? x(tr.real) : px;
      row = px - lastLabel < 36 ? 1 - row : 0;
      lastLabel = px;
      const labelY = row ? AXIS - 30 : AXIS - 14;
      const color = statusColor(tr.status);
      const dot = tr.status === "cancelled"
        ? `<circle cx="${px.toFixed(1)}" cy="${AXIS}" r="6" fill="var(--ha-card-background, var(--card-background-color, #fff))" stroke="${color}" stroke-width="2"/>`
        : `<circle cx="${rx.toFixed(1)}" cy="${AXIS}" r="6" fill="${color}"/>`;
      const seg = tr.status !== "cancelled" && tr.depDelay > 0
        ? `<line class="seg" x1="${px.toFixed(1)}" y1="${AXIS}" x2="${rx.toFixed(1)}" y2="${AXIS}"/>`
        : "";
      const tag = tr.status === "cancelled"
        ? `<text class="tag" x="${px.toFixed(1)}" y="${AXIS + 22}" text-anchor="middle" fill="${color}">${this._t("cancelled").toLowerCase()}</text>`
        : tr.delay > 0 && tr.status !== "departed"
          ? `<text class="tag" x="${px.toFixed(1)}" y="${AXIS + 22}" text-anchor="middle" fill="${color}">+${tr.delay}</text>`
          : "";
      return `<g ${this._clickable(tr)}><title>${escapeHtml(`${formatTime(tr.planned)} ${tr.mode} ${tr.num}`)}</title>
        ${seg}${dot}
        <text class="lbl num" x="${px.toFixed(1)}" y="${labelY}" text-anchor="middle">${formatTime(tr.planned)}</text>${tag}</g>`;
    }).join("");

    const nowMark = inRange(now)
      ? `<g class="now"><line x1="${x(now).toFixed(1)}" y1="8" x2="${x(now).toFixed(1)}" y2="${AXIS + 30}"/>
          <text x="${x(now).toFixed(1)}" y="${AXIS + 62}" text-anchor="middle">${this._t("now")}</text></g>`
      : "";

    const day = sameDay(start, now) ? "" : `${this._t("tomorrow")} · `;
    return `<ha-card><div class="wrap">
      <div class="head"><h2>${escapeHtml(title)}</h2>
        <span class="num">${day}${formatTime(start)} – ${formatTime(end)} · ${trains.length} ${this._t("trains")}</span></div>
      <div class="scroll"><svg viewBox="0 0 ${W} 130" role="img" aria-label="${escapeHtml(title)}">
        <line class="axis" x1="${LEFT}" y1="${AXIS}" x2="${RIGHT}" y2="${AXIS}"/>
        ${ticks.join("")}${nowMark}${marks}
      </svg></div>
      <div class="legend">
        <span><i style="background: ${statusColor("on_time")}"></i>${this._t("on_time")}</span>
        <span><i style="background: ${statusColor("delayed")}"></i>${this._t("legend_late")}</span>
        <span><i style="box-shadow: inset 0 0 0 2px ${statusColor("cancelled")}"></i>${this._t("cancelled")}</span>
        <span><i style="background: ${statusColor("departed")}"></i>${this._t("departed")}</span>
      </div>
    </div></ha-card>`;
  }
}

/* ------------------------------------------------------------------ */
/* Déclaration                                                         */
/* ------------------------------------------------------------------ */

const CARDS = [
  ["sncf-train-tile", SncfTrainTile, "SNCF : prochain train", "Compte à rebours du prochain départ et trains suivants."],
  ["sncf-departure-board", SncfDepartureBoard, "SNCF : tableau des départs", "Les prochains départs façon écran en gare."],
  ["sncf-train-timeline", SncfTrainTimeline, "SNCF : frise des départs", "Les trains de la plage horaire sur une ligne de temps."],
];

window.customCards = window.customCards || [];
for (const [type, cls, name, description] of CARDS) {
  if (!customElements.get(type)) customElements.define(type, cls);
  if (!window.customCards.some((card) => card.type === type)) {
    window.customCards.push({ type, name, description, preview: true, documentationURL: "https://github.com/Orken/SNCF-API-HA#-cartes-lovelace" });
  }
}
