/* Wi-Fi testing console commands for the ESP32 NAT Router
 *
 * Adds Marauder-style passive Wi-Fi tools (always on) and gated active
 * tools to the existing serial console and web UI, on a display-less
 * ESP32. Passive commands are read-only and safe. Active commands are
 * transmission-capable and are disabled unless the user explicitly
 * authorizes them (runtime + a compile-time safety switch).
 *
 * Public Domain (CC0), matching the rest of this project.
 */
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/** Register all Wi-Fi console commands. Called from app_main. */
void register_wifi(void);

/**
 * Set whether active (transmit) Wi-Fi features are permitted.
 * Only when true do deauth/beacon/evilportal actually transmit;
 * otherwise they print a refusal. This is a runtime authorization
 * gate: a user must explicitly enable it before any transmission.
 */
void wifi_set_authorized(bool authorized);
bool wifi_is_authorized(void);

/* --- passive operations (always allowed, read-only) --- */

/** Start an AP scan and print results. Returns 0 on success. */
int start_ap_scan(void);
/** Start combined AP scan + our-AP client list. */
int start_all_scan(void);
/** Stop the currently running scan. */
int stop_scan(void);

/** Print cached AP scan records. */
int print_ap_records(void);
/** Print details for a single AP by BSSID (MAC string). */
int wifi_stat(const char *bssid_mac_str);
/** List stations currently associated with our own soft-AP. */
int list_clients(void);

/* Web-UI support: run a scan and return a malloc'd JSON array of APs.
 * Each object: {"ssid":..,"bssid":"aa:bb:..","channel":N,"rssi":N,"auth":".."}.
 * The returned string must be free()d by the caller. Returns NULL on error. */
char *wifi_scan_json(void);

/* --- active operations (require wifi_set_authorized(true)) --- */

/** Transmit a deauth flood to a target BSSID. */
int deauth(const char *bssid_mac_str, int frames);
/** Transmit beacon (probe-response) flood. */
int beacon_flood(const char *bssid_mac_str, int frames);

#ifdef __cplusplus
}
#endif
