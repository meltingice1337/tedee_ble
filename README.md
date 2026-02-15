<p>
  <img src="https://raw.githubusercontent.com/meltingice1337/tedee_ble/master/images/icon.png" alt="Tedee BLE">
</p>

# Tedee BLE - Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=meltingice1337&repository=tedee_ble&category=integration)

Control your Tedee smart lock over **Bluetooth Low Energy** directly from Home Assistant.

**What you need:**
- **Home Assistant 2024.1.0** or newer
- A **Tedee GO 2** lock (aluminium or plastic) - other models (GO, PRO) may work but are untested
- A **Bluetooth adapter** on your Home Assistant host (built-in or USB dongle), **or** an **[ESPHome Bluetooth Proxy](https://esphome.github.io/bluetooth-proxies/)** (ESP32) within BLE range of the lock (~10m, varies by environment)
- A free **Tedee Personal Access Key** from the [Tedee Portal](https://portal.tedee.com) (used during setup for certificate registration)

**What you don't need:**
- A **Tedee Bridge** - the integration talks directly to the lock over BLE
- A **permanent cloud connection** - after initial setup, all lock commands happen locally. The cloud is only contacted every few days to refresh certificates

---

## Table of contents

- [Features](#features)
- [Installation](#installation)
- [Setup](#setup)
- [Entities](#entities)
- [Dashboard card](#dashboard-card)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [CLI tool](#cli-tool)

---

## Features

- **Lock, Unlock, and Open** - Full lock control including pull spring (open latch) support
- **Auto-pull on unlock** - Optional setting to automatically pull the spring when unlocking, so the door unlatches in one step
- **Door open/closed sensor** - If your Tedee lock has the optional **door sensor** accessory installed, the integration exposes a binary sensor showing whether the door is open or closed
- **Battery monitoring** - See the current battery level and whether the lock is charging
- **Real-time state updates** - Lock state changes (locked, unlocked, jammed, door opened) are pushed instantly via BLE notifications, no polling needed
- **Jam detection** - The lock reports if it gets jammed during locking or unlocking
- **Activity tracking** - See who triggered the last action and how (see [details below](#activity-tracking))
- **Firmware version and update status** - Firmware version shown on the device page, plus a binary sensor indicating when a firmware update is available (refreshed from the Tedee Cloud API)
- **Persistent connection with auto-reconnect** - The integration maintains a live BLE connection and automatically reconnects if it drops, with a grace period that hides brief reconnections from the UI
- **Direct BLE and ESPHome Bluetooth Proxy** - Connect directly from your Home Assistant host's Bluetooth adapter, or route through an [ESPHome Bluetooth Proxy](https://esphome.github.io/bluetooth-proxies/) for extended range
- **Custom dashboard card** - A built-in Lovelace card with animated status icons, smart action buttons, and at-a-glance info

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu (top right) and select **Custom repositories**
3. Add the repository URL and select **Integration** as the category
4. Search for "Tedee BLE" and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/tedee_ble` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for **Tedee BLE**
3. Enter your Tedee Personal Access Key ([how to get one](#getting-your-api-key))
4. Select your lock from the list
5. The integration will scan for the lock over BLE - make sure your HA host has Bluetooth (or an ESPHome proxy) and is in range
6. If the scan doesn't find it, you can enter the BLE MAC address manually

### Getting your API key

The integration needs a Tedee Personal Access Key to register with the lock and obtain BLE certificates. After setup, all lock commands happen locally over BLE - the cloud API is only contacted every few days to refresh certificates. Your API key never leaves your Home Assistant instance.

1. Go to [Tedee Portal](https://portal.tedee.com) and log in
2. Navigate to **Personal Access Keys**
3. Create a new key with the following scopes:
   - **Device.Read** - discover your locks
   - **DeviceCertificate.Operate** - obtain BLE certificates
   - **Mobile.ReadWrite** - register as a mobile device
   - **DeviceActivity.Read** - read activity logs for user identification
4. Copy the key - you'll paste it during integration setup

### Configuration options

After setup, click the **Configure** button on the integration to adjust:

- **Auto-pull on unlock** - When enabled, unlocking the lock will also automatically pull the spring to unlatch the door. When disabled, unlock and open are separate actions.

## Entities

The integration creates the following entities per lock, all grouped under a single device:

| Entity | Type | Description |
|--------|------|-------------|
| **Lock** | `lock` | Lock, unlock, and open (pull spring). Shows locking, unlocking, and jammed states. |
| **Door** | `binary_sensor` | Door open/closed state. Requires the optional **Tedee door sensor** accessory to be installed on the lock. |
| **Battery** | `sensor` | Battery percentage and charging status |
| **Firmware update** | `binary_sensor` | Whether a firmware update is available for the lock (diagnostic) |

The **firmware version** is shown on the device info page (Settings > Devices > your lock), not as a separate entity.

### Activity tracking

The lock entity exposes two extra attributes - `last_trigger` and `last_user` - showing what caused the most recent state change:

- **last_trigger** tells you *how* it was triggered:
  - **button** - physical button press on the lock
  - **remote** - BLE command from Home Assistant or phone
  - **auto-lock** - the lock's built-in auto-lock timer
  - **door sensor** - triggered by opening or closing the door

- **last_user** tells you *who* triggered it, resolved from a user ID to a name. The integration builds a mapping of user IDs to names from the Tedee Cloud API activity logs. This map is automatically refreshed during periodic certificate renewals and whenever an unknown user is detected, so new shares are picked up without any manual action.

## Dashboard card

The integration ships with a built-in **Tedee Lock Card** that shows everything in a single compact row:

<p>
  <img src="https://raw.githubusercontent.com/meltingice1337/tedee_ble/master/custom_card2.png" alt="Tedee Lock Card">
</p>

The card is **auto-registered** on startup - no need to add it as a resource manually.

### Card configuration

```yaml
type: custom:tedee-lock-card
lock: lock.lock_lock
door: binary_sensor.lock_door      # optional
battery: sensor.lock_battery        # optional
name: Front Door                    # optional, overrides entity name
```

**What it shows:**
- State-colored lock icon (green = locked, amber = unlocked, blue = transitioning, red = jammed, grey = unavailable)
- Animated icon (pulse during locking/unlocking, shake when jammed)
- Smart buttons - only shows actions that make sense (e.g. "Open" only appears when unlocked)
- Door state and battery chips - click to open their respective entity dialogs
- Last user and trigger source (button press, remote command, auto-lock, door sensor)

## How it works

**Direct BLE connection:**

```
┌─────────────┐      BLE       ┌──────────────┐
│   Home      │◄──────────────►│  Tedee Lock  │
│   Assistant │  Encrypted     │  (GO 2)      │
└──────┬──────┘                └──────────────┘
       │
       │ HTTPS (certificate refresh only,
       │        every few days)
       │
┌──────▼──────┐
│ Tedee Cloud │
│    API      │
└─────────────┘
```

**Via ESPHome Bluetooth Proxy:**

```
┌─────────────┐    Wi-Fi     ┌──────────────┐     BLE      ┌──────────────┐
│   Home      │◄────────────►│   ESPHome    │◄────────────►│  Tedee Lock  │
│   Assistant │              │  BLE Proxy   │  Encrypted   │  (GO 2)      │
└──────┬──────┘              │  (ESP32)     │              └──────────────┘
       │                     └──────────────┘
       │ HTTPS (certificate refresh only,
       │        every few days)
       │
┌──────▼──────┐
│ Tedee Cloud │
│    API      │
└─────────────┘
```

1. **Device Registration** - The integration registers with Tedee's Cloud API and obtains a signed certificate for BLE authentication
2. **BLE Discovery** - The integration scans for your lock over Bluetooth (directly or through an ESPHome proxy)
3. **Encrypted Session** - A secure, encrypted BLE session is established using the certificate
4. **Persistent Connection** - The integration maintains a persistent BLE connection with keep-alive pings (the lock disconnects after ~25-45s of inactivity)
5. **Real-time Notifications** - Lock state changes are pushed instantly via BLE notifications
6. **Automatic Reconnection** - If the BLE connection drops, the integration reconnects automatically with exponential backoff (2s, 5s, 10s, 30s, 60s). A 15-second grace period prevents brief reconnections from showing entities as "unavailable"

## Troubleshooting

### Lock not found during BLE scan
- Make sure Bluetooth is enabled on your HA host
- Move the HA host closer to the lock
- Check that no other device (phone, bridge) is monopolizing the BLE connection
- You can enter the MAC address manually if the scan fails

### Frequent disconnections
- The Tedee lock (especially the GO model) drops idle BLE connections after ~25-45 seconds. This is normal battery-saving behavior. The integration reconnects automatically in ~2-5 seconds, and a grace period prevents entities from briefly showing as "unavailable"
- If entities stay unavailable for longer periods, check BLE range - move the HA host closer, or use an [ESPHome Bluetooth Proxy](https://esphome.github.io/bluetooth-proxies/) placed near the lock
- Interference from other 2.4GHz devices (Wi-Fi, Zigbee) can cause disconnections

### Certificate errors
- The integration auto-refreshes certificates. If you see persistent errors, remove and re-add the integration

### Reporting an issue

If you run into a problem, please [open an issue](https://github.com/meltingice1337/tedee_ble/issues) and include the following:

1. **Lock model and firmware version** (e.g. Tedee GO 2, firmware 2.4.18050)
2. **Connection type** - direct Bluetooth or ESPHome Bluetooth Proxy (and if proxy, the ESP32 board model)
3. **Debug logs** - enable debug logging by adding this to your `configuration.yaml` and restarting:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.tedee_ble: debug
   ```
   Then reproduce the issue and include the relevant log output from **Settings > System > Logs**.
4. **Steps to reproduce** - what you did before the issue occurred

## CLI tool

The repo includes a standalone `cli.py` for testing and debugging the BLE connection outside of Home Assistant. It uses the same underlying library as the integration and supports both direct Bluetooth and ESPHome proxy.

```bash
python cli.py scan                           # Find Tedee locks nearby
python cli.py register                       # One-time: generate keys and register with Tedee cloud
python cli.py status                         # Get lock state and battery
python cli.py lock                           # Lock the door
python cli.py unlock [--force] [--pull]      # Unlock (--pull to also pull spring)
python cli.py pull                           # Pull spring only
python cli.py info [--raw]                   # Show lock model, serial, firmware from cloud
python cli.py shell                          # Interactive session with persistent connection

# Via ESPHome Bluetooth Proxy
python cli.py --proxy 192.168.1.50 scan
python cli.py --proxy 192.168.1.50 shell
```

## License

MIT License - see [LICENSE](https://github.com/meltingice1337/tedee_ble/blob/master/LICENSE)
