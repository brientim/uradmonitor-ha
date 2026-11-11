# uRADMonitor for Home Assistant

Radiation and air quality from [uRADMonitor](https://www.uradmonitor.com) units in Home Assistant, read from your account's cloud API and/or each unit's local `/j` page. One device per unit, one sensor per reading.

This is the Home Assistant port of the ControlZ uRADMonitor HomeSeer HS4 plugin (`CzURadMonitor`), with the same parsing, units and local-first merging.

## Install

**Home Assistant 2026.11 and newer — Marketplace:** **Settings → Marketplace**, three-dot menu → **Custom repositories**, add this repository with type **Integration**, open **uRADMonitor** and select **Download**. Restart Home Assistant.

**Older versions — HACS:** the same steps under **HACS**.

**Manually:** copy `custom_components/uradmonitor` into your Home Assistant `config/custom_components/` folder and restart.

Requires Home Assistant 2025.8 or newer. From 2026.3 the integration shows its own icon.

## Set up

**Settings → Devices & services → Add integration → uRADMonitor**, then pick where to read your units:

| Source | What it reads |
|--|--|
| **Local and cloud** | Local units first; the cloud for everything else and as a fallback |
| **Local only** | Units on your network; no account or internet needed |
| **Cloud only** | Every unit on your uRADMonitor account, over the internet |

- **Cloud:** the **API Credentials** on your uRADMonitor profile ([uradmonitor.com/wp-admin/profile.php](https://www.uradmonitor.com/wp-admin/profile.php)). **User ID** is the API *user-id*, **User Key** the API *key*. Every unit you own, or were given access to, is added.
- **Local:** the address of each unit, e.g. `192.168.1.50` or `192.168.1.50:8080`. Give each unit a fixed IP (a DHCP reservation on your router).

Change the source, account or addresses later with **Reconfigure** on the integration. **Configure** sets how often each source is read: the cloud every 300 s by default (at least 120 s — please keep the load on uRADMonitor's free API light), local units every 60 s (at least 10 s).

## What you get

Each unit is a device named `uRADMonitor <unit ID>` with a sensor for every reading it reports. Sensors appear as a unit starts reporting them; no reload needed.

| Sensor | Unit | Notes |
|--|--|--|
| Radiation dose | µSv/h | CPM × the tube's conversion factor (from the cloud, or the SBM20 / SI29BG table) |
| Radiation | CPM | |
| Air quality index | Good … Extremely poor | European AQI band, from the cloud |
| Temperature, Humidity, Atmospheric pressure | °C, %, hPa | |
| Carbon dioxide, Formaldehyde, Ozone | ppm, ppb, ppb | |
| PM1, PM2.5, PM10 | µg/m³ | |
| VOC resistance, VOC index, Noise, Radon | Ω, —, dB, Bq/m³ | |
| Connectivity | | On while the unit reports to the cloud or answers locally |
| Data source | Local / Cloud | Diagnostic |
| Tube voltage, Duty cycle | V, ‰ | Diagnostic |
| Signal strength, Uptime, Last seen | dBm, d, time | Diagnostic, disabled by default |

Readings of an offline unit are unavailable. With **Local and cloud**, a unit's local readings are used while it answered within the last three local intervals; after that the cloud's readings take over.

To leave out a unit (for example someone else's unit shared with your account), disable its device. A unit that no source reports any more can be deleted from its device page.

## Coming from the HomeSeer plugin

| HS4 plugin | Home Assistant |
|--|--|
| Settings page (mode, credentials, addresses, intervals) | Setup, **Reconfigure** and **Configure** |
| Root device per unit, Status feature and summary | Device per unit, **Connectivity** sensor |
| Feature per sensor, status icon bands | Sensor per reading with device class, unit and icon |
| "Units in HomeSeer" checkboxes | Disable the unit's device |
| Repair HS devices | Not needed |
| Plugin status warnings | A sign-in prompt when credentials are rejected, the log and **Download diagnostics** |

## Privacy

Unit positions in the cloud and local replies (latitude, longitude, altitude, city, country) are dropped while parsing and never stored. Diagnostics leave out the User ID, User Key and unit addresses.

## Development

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/ruff check custom_components tests && .venv/bin/ruff format --check custom_components tests
.venv/bin/pytest -q
```

`tests/test_protocol.py` and `tests/test_api.py` port the HS4 plugin's `--selftest` checks and fixtures; the other tests run the integration in Home Assistant.
