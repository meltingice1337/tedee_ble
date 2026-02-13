/**
 * Tedee Lock Card — Custom Lovelace card for the Tedee BLE integration.
 *
 * Shows lock state (with animated SVG), door sensor, battery level,
 * last trigger / user, and Lock / Unlock / Open action buttons.
 */

const CARD_VERSION = "1.0.3";

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

  /* ── battery icon helper ───────────────────────────────────── */

  _batteryIcon(level) {
    if (level == null) return "mdi:battery-unknown";
    if (level <= 10) return "mdi:battery-10";
    if (level <= 20) return "mdi:battery-20";
    if (level <= 30) return "mdi:battery-30";
    if (level <= 40) return "mdi:battery-40";
    if (level <= 50) return "mdi:battery-50";
    if (level <= 60) return "mdi:battery-60";
    if (level <= 70) return "mdi:battery-70";
    if (level <= 80) return "mdi:battery-80";
    if (level <= 90) return "mdi:battery-90";
    return "mdi:battery";
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

    // Last trigger / user (from lock entity attributes)
    let lastInfo = "";
    if (lockState && lockState.attributes) {
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
          padding: 10px 14px;
          overflow: hidden;
        }
        .row {
          display: flex;
          align-items: center;
          gap: 12px;
          flex-wrap: wrap;
        }
        /* — left: icon + name/state — */
        .identity {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-shrink: 0;
          cursor: pointer;
        }
        .lock-icon { display: flex; align-items: center; }
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
        }
        .state {
          font-size: 11px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.8px;
        }
        /* — middle: info chips — */
        .info {
          display: flex;
          align-items: center;
          gap: 10px;
          font-size: 12px;
          color: var(--secondary-text-color);
          flex: 1 1 auto;
        }
        .chip {
          display: flex;
          align-items: center;
          gap: 3px;
          white-space: nowrap;
        }
        .chip ha-icon { --mdc-icon-size: 15px; }
        .chip.clickable { cursor: pointer; }
        .chip.clickable:hover { color: var(--primary-text-color); }
        .last {
          font-size: 11px;
          color: var(--disabled-text-color, #999);
          white-space: nowrap;
        }
        /* — right: buttons — */
        .buttons {
          display: flex;
          gap: 5px;
          flex-shrink: 0;
        }
        .btn {
          padding: 5px 12px;
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 8px;
          background: none;
          color: var(--primary-text-color);
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          transition: background 0.15s;
          font-family: inherit;
          white-space: nowrap;
        }
        .btn:hover:not(:disabled) { background: var(--secondary-background-color, #f5f5f5); }
        .btn:active:not(:disabled) { background: var(--divider-color, #e0e0e0); }
        .btn:disabled { opacity: 0.4; cursor: not-allowed; }
      </style>

      <ha-card>
        <div class="row">
          <div class="identity">
            <div class="${animClass}">${this._lockSVG(meta.shackle, meta.color)}</div>
            <div>
              <div class="name">${this._esc(name)}</div>
              <div class="state" style="color:${meta.color}">${meta.label}</div>
            </div>
          </div>

          <div class="info">
            ${doorState ? `<span class="chip clickable" id="chip-door"><ha-icon icon="mdi:door${doorText === "Open" ? "-open" : "-closed"}"></ha-icon>${doorText}</span>` : ""}
            ${battLevel != null ? `<span class="chip clickable" id="chip-batt"><ha-icon icon="${this._batteryIcon(battLevel)}"></ha-icon>${battLevel}%</span>` : ""}
            ${lastInfo ? `<span class="last">\u21bb ${this._esc(lastInfo)}</span>` : ""}
          </div>

          <div class="buttons">
            ${showLock ? `<button class="btn" id="btn-lock" ${btnDisabled ? "disabled" : ""}>Lock</button>` : ""}
            ${showUnlock ? `<button class="btn" id="btn-unlock" ${btnDisabled ? "disabled" : ""}>Unlock</button>` : ""}
            ${showOpen ? `<button class="btn" id="btn-open" ${btnDisabled ? "disabled" : ""}>Open</button>` : ""}
          </div>
        </div>
      </ha-card>
    `;

    // More-info on click
    this.shadowRoot.querySelector(".identity")?.addEventListener("click", () => this._showMoreInfo(this._config.lock));
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
  documentationURL: "https://github.com/darius/tedee-ble",
});

console.info(
  `%c TEDEE-LOCK-CARD %c v${CARD_VERSION} `,
  "background:#4caf50;color:#fff;font-weight:bold;padding:2px 6px;border-radius:4px 0 0 4px",
  "background:#ddd;color:#333;padding:2px 6px;border-radius:0 4px 4px 0"
);
