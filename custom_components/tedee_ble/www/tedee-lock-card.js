/**
 * Tedee Lock Card — Custom Lovelace card for the Tedee BLE integration.
 *
 * Shows lock state (with animated SVG), door sensor, battery level,
 * last trigger / user, and Lock / Unlock / Open action buttons.
 */

const CARD_VERSION = "1.3.1";

class TedeeLockCard extends HTMLElement {
  /* ── lifecycle ─────────────────────────────────────────────── */

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  /* ── config ────────────────────────────────────────────────── */

  setConfig(config) {
    if (!config.lock) {
      throw new Error("You must specify a lock entity (lock.*)");
    }
    this._config = {
      lock: config.lock,
      door: config.door || null,
      battery: config.battery || null,
      name: config.name || null,
      show_activity: config.show_activity !== false,
    };
    if (this._hass) this._render();
  }

  getCardSize() {
    return 1;
  }

  static getConfigElement() {
    return undefined; // use YAML editor
  }

  static getStubConfig() {
    return { lock: "lock.lock_lock" };
  }

  /* ── hass reactive setter ──────────────────────────────────── */

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  /* ── helpers ───────────────────────────────────────────────── */

  _stateOf(entityId) {
    if (!entityId || !this._hass) return null;
    return this._hass.states[entityId] || null;
  }

  _friendlyName(stateObj) {
    return (
      (stateObj && stateObj.attributes && stateObj.attributes.friendly_name) ||
      ""
    );
  }

  /* ── colour / label / animation by lock state ──────────────── */

  _lockMeta(state) {
    switch (state) {
      case "locked":
        return {
          color: "#4caf50",
          label: "Locked",
          shackle: "closed",
          anim: "",
        };
      case "unlocked":
        return {
          color: "#ff9800",
          label: "Unlocked",
          shackle: "open",
          anim: "",
        };
      case "locking":
        return {
          color: "#2196f3",
          label: "Locking…",
          shackle: "closed",
          anim: "pulse",
        };
      case "unlocking":
        return {
          color: "#2196f3",
          label: "Unlocking…",
          shackle: "open",
          anim: "pulse",
        };
      case "jammed":
        return {
          color: "#f44336",
          label: "Jammed",
          shackle: "closed",
          anim: "shake",
        };
      default:
        return {
          color: "#9e9e9e",
          label: "Unavailable",
          shackle: "closed",
          anim: "dim",
        };
    }
  }

  /* ── SVG lock icon ─────────────────────────────────────────── */

  _lockSVG(shackle, color) {
    const open = shackle === "open";
    const shacklePath = open
      ? `<path d="M30 38 L30 24 C30 14, 42 6, 54 14 L54 20"
              fill="none" stroke="${color}" stroke-width="5"
              stroke-linecap="round"/>`
      : `<path d="M30 38 L30 24 C30 14, 54 14, 54 24 L54 38"
              fill="none" stroke="${color}" stroke-width="5"
              stroke-linecap="round"/>`;

    return `
      <svg viewBox="0 0 84 84" width="40" height="40" xmlns="http://www.w3.org/2000/svg">
        ${shacklePath}
        <rect x="20" y="38" width="44" height="34" rx="6"
              fill="${color}" opacity="0.18" stroke="${color}"
              stroke-width="3"/>
        <circle cx="42" cy="53" r="5" fill="${color}"/>
        <line x1="42" y1="53" x2="42" y2="63" stroke="${color}"
              stroke-width="3" stroke-linecap="round"/>
      </svg>`;
  }

  /* ── battery helpers ──────────────────────────────────────── */

  _batteryColor(level) {
    if (level == null) return "#9e9e9e";
    if (level <= 10) return "#f44336";
    if (level <= 25) return "#ff9800";
    return "#4caf50";
  }

  _batterySVG(level, charging) {
    const color = this._batteryColor(level);
    const pct = level != null ? Math.max(0, Math.min(100, level)) : 0;
    const fillW = Math.round((pct / 100) * 16);
    const bolt = charging
      ? `<path d="M13 4 L10 9 L13 9 L11 14 L14 8 L11 8 Z" fill="#fff" opacity="0.9"/>`
      : "";
    return `
      <svg viewBox="0 0 26 16" width="26" height="16" xmlns="http://www.w3.org/2000/svg">
        <rect x="1" y="2" width="20" height="12" rx="2" ry="2"
              fill="none" stroke="${color}" stroke-width="1.5"/>
        <rect x="21" y="5.5" width="3" height="5" rx="1" ry="1"
              fill="${color}" opacity="0.5"/>
        <rect x="3" y="4" width="${fillW}" height="8" rx="1" ry="1"
              fill="${color}" opacity="0.7"/>
        ${bolt}
      </svg>`;
  }

  /* ── action handlers ───────────────────────────────────────── */

  _callService(service) {
    if (!this._hass) return;
    this._hass.callService("lock", service, {
      entity_id: this._config.lock,
    });
  }

  _showMoreInfo(entityId) {
    const event = new Event("hass-more-info", { bubbles: true, composed: true });
    event.detail = { entityId };
    this.dispatchEvent(event);
  }

  /* ── render ────────────────────────────────────────────────── */

  _render() {
    const lockState = this._stateOf(this._config.lock);
    const doorState = this._stateOf(this._config.door);
    const battState = this._stateOf(this._config.battery);

    const state = lockState ? lockState.state : "unavailable";
    const meta = this._lockMeta(state);

    // Name
    const name =
      this._config.name ||
      (lockState ? this._friendlyName(lockState).replace(/ Lock$/i, "") : "Tedee");

    // Door
    let doorText = "";
    if (doorState) {
      doorText = doorState.state === "on" ? "Open" : "Closed";
    }

    // Battery
    const battLevel =
      battState && battState.state !== "unavailable" && battState.state !== "unknown"
        ? parseInt(battState.state, 10)
        : null;

    // Battery extras
    const battCharging = battState && battState.attributes && battState.attributes.charging;

    // Last trigger / user (from lock entity attributes)
    let lastInfo = "";
    if (this._config.show_activity && lockState && lockState.attributes) {
      const parts = [];
      if (lockState.attributes.last_user) parts.push(lockState.attributes.last_user);
      if (lockState.attributes.last_trigger) parts.push(lockState.attributes.last_trigger);
      lastInfo = parts.join(" \u00b7 ");
    }

    // Determine animation class
    const animClass = meta.anim ? `lock-icon ${meta.anim}` : "lock-icon";

    // Button visibility: only show actions that make sense for current state
    const transitioning = state === "locking" || state === "unlocking";
    const unavailable = state === "unavailable";
    const btnDisabled = transitioning || unavailable;

    // Lock → show when unlocked (or jammed)
    const showLock = state === "unlocked" || state === "jammed";
    // Unlock → show when locked (or jammed)
    const showUnlock = state === "locked" || state === "jammed";
    // Open (pull spring) → only when already unlocked
    const showOpen = state === "unlocked";

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card {
          padding: 12px 14px;
          overflow: hidden;
        }
        /* — top row: icon + name/state … chips — */
        .top {
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .identity {
          display: flex;
          align-items: center;
          gap: 8px;
          cursor: pointer;
          min-width: 0;
        }
        .lock-icon { display: flex; align-items: center; flex-shrink: 0; }
        .lock-icon.pulse { animation: pulse 1s ease-in-out infinite; }
        .lock-icon.shake { animation: shake 0.4s ease-in-out infinite; }
        .lock-icon.dim   { opacity: 0.35; }
        @keyframes pulse {
          0%,100% { transform:scale(1); opacity:1; }
          50%     { transform:scale(1.08); opacity:0.7; }
        }
        @keyframes shake {
          0%,100% { transform:translateX(0); }
          20%     { transform:translateX(-3px); }
          40%     { transform:translateX(3px); }
          60%     { transform:translateX(-2px); }
          80%     { transform:translateX(2px); }
        }
        .name {
          font-size: 14px;
          font-weight: 500;
          color: var(--primary-text-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .state {
          font-size: 11px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.8px;
        }
        .chips {
          display: flex;
          align-items: center;
          gap: 10px;
          margin-left: auto;
          flex-shrink: 0;
          font-size: 12px;
          color: var(--secondary-text-color);
        }
        .chip {
          display: flex;
          align-items: center;
          gap: 3px;
          white-space: nowrap;
        }
        .chip ha-icon { --mdc-icon-size: 15px; }
        .chip.batt { font-weight: 600; font-size: 11px; gap: 4px; }
        .chip.batt svg { display: block; }
        .chip.clickable { cursor: pointer; }
        .chip.clickable:hover { color: var(--primary-text-color); }
        /* — activity line — */
        .activity {
          font-size: 11px;
          color: var(--disabled-text-color, #999);
          margin-top: 4px;
          padding-left: 48px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        /* — buttons row — */
        .buttons {
          display: flex;
          gap: 6px;
          margin-top: 10px;
        }
        .btn {
          flex: 1;
          padding: 7px 0;
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 10px;
          background: none;
          color: var(--primary-text-color);
          font-size: 13px;
          font-weight: 500;
          cursor: pointer;
          transition: background 0.15s;
          font-family: inherit;
          text-align: center;
        }
        .btn:hover:not(:disabled) { background: var(--secondary-background-color, #f5f5f5); }
        .btn:active:not(:disabled) { background: var(--divider-color, #e0e0e0); }
        .btn:disabled { opacity: 0.4; cursor: not-allowed; }
      </style>

      <ha-card>
        <div class="top">
          <div class="identity" id="identity">
            <div class="${animClass}">${this._lockSVG(meta.shackle, meta.color)}</div>
            <div>
              <div class="name">${this._esc(name)}</div>
              <div class="state" style="color:${meta.color}">${meta.label}</div>
            </div>
          </div>
          <div class="chips">
            ${doorState ? `<span class="chip clickable" id="chip-door"><ha-icon icon="mdi:door${doorText === "Open" ? "-open" : "-closed"}"></ha-icon>${doorText}</span>` : ""}
            ${battLevel != null ? `<span class="chip batt clickable" id="chip-batt">${this._batterySVG(battLevel, battCharging)}<span style="color:${this._batteryColor(battLevel)}">${battLevel}%</span></span>` : ""}
          </div>
        </div>
        ${lastInfo ? `<div class="activity">\u21bb ${this._esc(lastInfo)}</div>` : ""}
        <div class="buttons">
          ${showLock ? `<button class="btn" id="btn-lock" ${btnDisabled ? "disabled" : ""}>Lock</button>` : ""}
          ${showUnlock ? `<button class="btn" id="btn-unlock" ${btnDisabled ? "disabled" : ""}>Unlock</button>` : ""}
          ${showOpen ? `<button class="btn" id="btn-open" ${btnDisabled ? "disabled" : ""}>Open</button>` : ""}
        </div>
      </ha-card>
    `;

    // More-info on click
    this.shadowRoot.getElementById("identity")?.addEventListener("click", () => this._showMoreInfo(this._config.lock));
    this.shadowRoot.getElementById("chip-door")?.addEventListener("click", () => this._showMoreInfo(this._config.door));
    this.shadowRoot.getElementById("chip-batt")?.addEventListener("click", () => this._showMoreInfo(this._config.battery));

    // Action buttons
    this.shadowRoot.getElementById("btn-lock")?.addEventListener("click", () => this._callService("lock"));
    this.shadowRoot.getElementById("btn-unlock")?.addEventListener("click", () => this._callService("unlock"));
    this.shadowRoot.getElementById("btn-open")?.addEventListener("click", () => this._callService("open"));
  }

  /* ── XSS helper ────────────────────────────────────────────── */

  _esc(str) {
    const el = document.createElement("span");
    el.textContent = str;
    return el.innerHTML;
  }
}

/* ── register element ──────────────────────────────────────────── */

customElements.define("tedee-lock-card", TedeeLockCard);

/* ── register in HA card picker ────────────────────────────────── */

window.customCards = window.customCards || [];
window.customCards.push({
  type: "tedee-lock-card",
  name: "Tedee Lock",
  description: "Combined lock, door sensor and battery card for Tedee BLE locks.",
  preview: true,
  documentationURL: "https://github.com/meltingice1337/tedee-ble",
});

console.info(
  `%c TEDEE-LOCK-CARD %c v${CARD_VERSION} `,
  "background:#4caf50;color:#fff;font-weight:bold;padding:2px 6px;border-radius:4px 0 0 4px",
  "background:#ddd;color:#333;padding:2px 6px;border-radius:0 4px 4px 0"
);
