# Wi‑Fi Test Suite (console + web)

Adds Marauder‑style Wi‑Fi testing to the ESP32 NAT Router, driven through the
existing **serial console** and **web UI** on a display‑less ESP32.

> **Legal.** Passive commands are read‑only and safe. The active (transmit)
> commands — `deauth`, `beacon` — are **compile‑gated** (off by default) and
> require a runtime `authorize` before they act. They are scaffolds in this
> AP/STA build. Use only on networks you own or have **written authorization**
> to test. Unauthorized Wi‑Fi interference is illegal in most jurisdictions.

## Console commands

Registered from `components/cmd_wifi/`. Available at the `esp32nre>` prompt.

| Command      | Mode    | What it does                                             |
|--------------|---------|----------------------------------------------------------|
| `scanap`     | passive | Scan for APs and print SSID/BSSID/channel/RSSI/auth      |
| `scanall`    | passive | `scanap` + list stations on our soft‑AP                  |
| `stopscan`   | passive | Stop a running scan                                      |
| `aplist`     | passive | Re‑print the last cached AP records                      |
| `clients`    | passive | List stations currently associated with our soft‑AP      |
| `wifistat <bssid>` | passive | Show details for a BSSID from the cache               |
| `authorize`  | active  | Enable active (transmit) features for this session       |
| `deauth <bssid> [count]` | active | Deauth flood (scaffold, off by default)          |
| `beacon <bssid> [count]`   | active | Beacon/probe‑response flood (scaffold, off by default) |

Passive commands never disturb the running AP/STA stack.

## Web UI

- **`/wifi`** — live AP scan table (auto‑refreshes every 30 s), with a
  "Wi‑Fi Test Suite" button on the main config page.
- **`/api/wifi`** — JSON array of the most recent scan:
  `{"ssid":..,"bssid":"aa:bb:..","channel":N,"rssi":N,"auth":".."}`.

## Build notes

The full app (~1.35 MB) needs the 4 MB flash partition table. The repo's
`larger.csv` (v7.2) is 2×1500 K OTA banks on a 4 MB flash and fits the full
app. Set `upload_port` in `platformio.ini` (`[env:esp32]`) to your serial port.

### Enabling active (transmit) features (optional, not recommended by default)

Build with:

```
pio run -e esp32 -D CONFIG_WIFI_ATTACKS_ENABLED=1
```

then at runtime run `authorize`, then `deauth`/`beacon`. Even then, these are
scaffolds: true frame injection needs a dedicated monitor/injection radio,
which conflicts with the running AP/STA stack in this build.
