/* Wi-Fi testing console commands for the ESP32 NAT Router
 *
 * Adds Marauder-style passive Wi-Fi tools (always on) and gated active
 * tools to the existing serial console and web UI, on a display-less
 * ESP32. Passive commands are read-only and safe. Active commands are
 * transmission-capable and are disabled unless the user explicitly
 * authorizes them (runtime + a compile-time safety switch).
 *
 * Public Domain (CC0), matching the rest of this project.
 *
 * Design constraints honoured:
 *   - Never tear down the running AP/STA stack (C-03/C-13 pattern):
 *     all scans run on the existing radio state.
 *   - Active features are off by default and require `authorize`
 *     AND a compile-time switch to transmit.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "esp_log.h"
#include "esp_console.h"
#include "esp_system.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "argtable3/argtable3.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/ip4_addr.h"

#include "cmd_wifi.h"

static const char *TAG = "cmd_wifi";

/* Safety switch for the active (transmit) features. Default OFF.
 * Set to 1 to compile the active commands in; they still require a
 * runtime `authorize` before they will transmit. */
#ifndef CONFIG_WIFI_ATTACKS_ENABLED
#define CONFIG_WIFI_ATTACKS_ENABLED 0
#endif

#define WIFI_SCAN_RESULT_MAX 32 /* cap on records we copy for display */

/* ------------------------------------------------------------------ */
/* Authorization gate for active features                              */
/* ------------------------------------------------------------------ */
static volatile bool s_authorized = false;

void wifi_set_authorized(bool authorized)
{
    s_authorized = authorized;
    ESP_LOGW(TAG, "Active Wi-Fi features %s", authorized ? "ENABLED" : "disabled");
}
bool wifi_is_authorized(void)
{
    return s_authorized;
}

static int authorize(int argc, char **argv)
{
#if !CONFIG_WIFI_ATTACKS_ENABLED
    (void)argc;
    (void)argv;
    printf("Active Wi-Fi features are not compiled in (CONFIG_WIFI_ATTACKS_ENABLED=0).\n");
    printf("Rebuild with -D CONFIG_WIFI_ATTACKS_ENABLED=1 to enable deauth/beacon.\n");
    printf("Note: transmit features must only be used on networks you own or\n");
    printf("have written authorization to test. Unauthorized use is illegal.\n");
    return -1;
#else
    s_authorized = true;
    printf("Active Wi-Fi features ENABLED for this session.\n");
    printf("TRANSMISSION CAPABILITY ACTIVE. Use only on authorized networks.\n");
    return 0;
#endif
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */
static const char *auth_mode_str(wifi_auth_mode_t a)
{
    switch (a)
    {
    case WIFI_AUTH_OPEN: return "OPEN";
    case WIFI_AUTH_WEP: return "WEP";
    case WIFI_AUTH_WPA_PSK: return "WPA";
    case WIFI_AUTH_WPA2_PSK: return "WPA2";
    case WIFI_AUTH_WPA_WPA2_PSK: return "WPA/WPA2";
    case WIFI_AUTH_WPA2_WPA3_PSK: return "WPA2/WPA3";
    case WIFI_AUTH_WPA3_PSK: return "WPA3";
    case WIFI_AUTH_WPA2_ENTERPRISE:
    case WIFI_AUTH_WPA3_ENT_192: return "Enterprise";
    case WIFI_AUTH_OWE: return "OWE";
    default: return "unknown";
    }
}

static void print_mac(const uint8_t *m)
{
    printf("%02x:%02x:%02x:%02x:%02x:%02x", m[0], m[1], m[2], m[3], m[4], m[5]);
}

/* Parse "aa:bb:cc:dd:ee:ff" into a uint8_t[6]. 0 on success, -1 on bad. */
static int parse_mac(const char *s, uint8_t out[6])
{
    unsigned int v[6];
    if (sscanf(s, "%x:%x:%x:%x:%x:%x",
               &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) != 6)
        return -1;
    for (int i = 0; i < 6; i++)
    {
        if (v[i] > 0xFF) return -1;
        out[i] = (uint8_t)v[i];
    }
    return 0;
}

/* Get the SSID (null-terminated copy, capped at 32) from an AP record. */
static void ap_ssid(wifi_ap_record_t *rec, char out[33])
{
    out[0] = '\0';
    size_t len = strlen((const char *)rec->ssid);
    if (len > 32) len = 32;
    if (len == 0)
    {
        strcpy(out, "(hidden)");
    }
    else
    {
        memcpy(out, rec->ssid, len);
        out[len] = '\0';
    }
}

/* ------------------------------------------------------------------ */
/* Passive: scan + display                                             */
/* ------------------------------------------------------------------ */
static volatile bool s_scan_running = false;

static esp_err_t scan_start(bool block)
{
    if (s_scan_running)
        return ESP_ERR_INVALID_STATE;
    s_scan_running = true;
    wifi_scan_config_t cfg = {
        .ssid = NULL,
        .bssid = NULL,
        .channel = 0,
        .show_hidden = true,
        .scan_type = WIFI_SCAN_TYPE_ACTIVE,
        .scan_time = {
            .active = {100, 500},
            .passive = 0,
        },
        .home_chan_dwell_time = 0,
    };
    esp_err_t err = esp_wifi_scan_start(&cfg, block);
    if (err != ESP_OK)
        s_scan_running = false;
    return err;
}

static int scan_finish_and_print(void)
{
    s_scan_running = false;
    uint16_t num = WIFI_SCAN_RESULT_MAX;
    wifi_ap_record_t *rec = calloc(num, sizeof(wifi_ap_record_t));
    if (!rec)
        return -1;

    uint16_t total = 0;
    esp_err_t err = esp_wifi_scan_get_ap_num(&total);
    if (err != ESP_OK || total == 0)
    {
        printf("No APs found.\n");
        free(rec);
        return 0;
    }
    if (total > num) num = total;
    err = esp_wifi_scan_get_ap_records(&num, rec);
    if (err != ESP_OK)
    {
        printf("Failed to read scan results (%d)\n", err);
        free(rec);
        return -1;
    }

    printf("%-3s %-20s %-17s %-4s %5s %s\n",
           "#", "SSID", "BSSID", "CH", "RSSI", "AUTH");
    for (int i = 0; i < num; i++)
    {
        char ssid[33];
        ap_ssid(&rec[i], ssid);
        printf("%-3d %-20s ", i, ssid);
        print_mac(rec[i].bssid);
        printf("  %-3u ", rec[i].primary);
        printf("%5d ", rec[i].rssi);
        printf("%s\n", auth_mode_str(rec[i].authmode));
    }
    printf("-- %u APs total\n", total);
    free(rec);
    return 0;
}

int start_ap_scan(void)
{
    esp_err_t err = scan_start(true);
    if (err != ESP_OK)
    {
        printf("AP scan start failed (%d)\n", err);
        return -1;
    }
    printf("AP scan complete.\n");
    return scan_finish_and_print();
}

int start_all_scan(void)
{
    int r = start_ap_scan();
    printf("\n[STA clients on our AP]\n");
    list_clients();
    return r;
}

int stop_scan(void)
{
    esp_err_t err = esp_wifi_scan_stop();
    s_scan_running = false;
    if (err != ESP_OK)
    {
        printf("scan stop: %d\n", err);
        return -1;
    }
    printf("Scan stopped.\n");
    return 0;
}

int print_ap_records(void)
{
    uint16_t num = WIFI_SCAN_RESULT_MAX;
    wifi_ap_record_t *rec = calloc(num, sizeof(wifi_ap_record_t));
    if (!rec)
        return -1;
    uint16_t total = 0;
    if (esp_wifi_scan_get_ap_num(&total) != ESP_OK || total == 0)
    {
        printf("No cached AP records. Run `scanap` first.\n");
        free(rec);
        return 0;
    }
    if (total > num) num = total;
    esp_wifi_scan_get_ap_records(&num, rec);
    for (int i = 0; i < num; i++)
    {
        char ssid[33];
        ap_ssid(&rec[i], ssid);
        printf("%-20s ", ssid);
        print_mac(rec[i].bssid);
        printf(" ch=%2u rssi=%3d %s\n", rec[i].primary, rec[i].rssi, auth_mode_str(rec[i].authmode));
    }
    free(rec);
    return 0;
}

int wifi_stat(const char *mac_str)
{
    uint8_t target[6];
    if (parse_mac(mac_str, target) != 0)
    {
        printf("Bad BSSID '%s'. Expect aa:bb:cc:dd:ee:ff\n", mac_str);
        return -1;
    }
    uint16_t num = WIFI_SCAN_RESULT_MAX;
    wifi_ap_record_t *rec = calloc(num, sizeof(wifi_ap_record_t));
    if (!rec)
        return -1;
    uint16_t total = 0;
    esp_wifi_scan_get_ap_num(&total);
    if (total == 0)
    {
        printf("No cached AP records. Run `scanap` first.\n");
        free(rec);
        return -1;
    }
    if (total > num) num = total;
    esp_wifi_scan_get_ap_records(&num, rec);
    for (int i = 0; i < num; i++)
    {
        if (memcmp(rec[i].bssid, target, 6) == 0)
        {
            char ssid[33];
            ap_ssid(&rec[i], ssid);
            printf("SSID      : %s\n", ssid);
            printf("BSSID     : ");
            print_mac(rec[i].bssid);
            printf("\nChannel   : %u (secondary %u)\n", rec[i].primary, rec[i].second);
            printf("RSSI      : %d dBm\n", rec[i].rssi);
            printf("Security  : %s\n", auth_mode_str(rec[i].authmode));
            printf("Cipher    : pairwise=%u group=%u\n",
                   (unsigned)rec[i].pairwise_cipher, (unsigned)rec[i].group_cipher);
            printf("802.11    : b=%d g=%d n=%d\n",
                   rec[i].phy_11b, rec[i].phy_11g, rec[i].phy_11n);
            free(rec);
            return 0;
        }
    }
    printf("BSSID %s not in cache. Run `scanap`.\n", mac_str);
    free(rec);
    return -1;
}

int list_clients(void)
{
    wifi_sta_list_t sta_list;
    esp_err_t err = esp_wifi_ap_get_sta_list(&sta_list);
    if (err != ESP_OK)
    {
        printf("Get STA list failed (%d)\n", err);
        return -1;
    }
    printf("%d station(s) associated with our soft-AP:\n", sta_list.num);
    for (int i = 0; i < sta_list.num; i++)
    {
        print_mac(sta_list.sta[i].mac);
        printf("\n");
    }
    return 0;
}

/* Build a JSON array of the last scan's AP records. Heap-allocated;
 * caller must free(). Returns NULL on error. */
char *wifi_scan_json(void)
{
    esp_err_t err = scan_start(true);
    if (err != ESP_OK)
        return NULL;
    s_scan_running = false;

    uint16_t num = WIFI_SCAN_RESULT_MAX;
    wifi_ap_record_t *rec = calloc(num, sizeof(wifi_ap_record_t));
    if (!rec)
        return NULL;
    uint16_t total = 0;
    if (esp_wifi_scan_get_ap_num(&total) != ESP_OK)
    {
        free(rec);
        return NULL;
    }
    if (total > num) num = total;
    esp_wifi_scan_get_ap_records(&num, rec);

    /* Each record serializes to ~120 bytes; reserve generously. */
    char *json = malloc(num * 140 + 8);
    if (!json)
    {
        free(rec);
        return NULL;
    }
    json[0] = '\0';
    strcat(json, "[");
    for (int i = 0; i < num; i++)
    {
        char ssid[33];
        ap_ssid(&rec[i], ssid);
        char bssid[18] = {};
        for (int b = 0; b < 6; b++)
            snprintf(bssid + b * 3, 4, b == 5 ? "%02x" : "%02x:", rec[i].bssid[b]);

        char entry[160];
        /* JSON-escape the SSID (backslash + double-quote only; keep short). */
        char ssid_esc[65];
        size_t slen = strlen(ssid);
        for (size_t k = 0; k < slen; k++)
        {
            if (ssid[k] == '"' || ssid[k] == '\\')
                ssid_esc[2 * k + 1] = ssid[k], ssid_esc[2 * k] = '\\', ssid_esc[2 * k + 2] = 0;
            else
                ssid_esc[k] = ssid[k];
            ssid_esc[2 * k + 1] = ssid_esc[2 * k] ? ssid_esc[2 * k + 1] : 0;
        }
        if (slen == 0) ssid_esc[0] = '\0';
        ssid_esc[2 * slen + 1] = '\0';

        snprintf(entry, sizeof(entry),
                 "%s{\"ssid\":\"%s\",\"bssid\":\"%s\",\"channel\":%u,\"rssi\":%d,\"auth\":\"%s\"}",
                 i ? "," : "", ssid_esc, bssid, rec[i].primary, rec[i].rssi,
                 auth_mode_str(rec[i].authmode));
        strcat(json, entry);
    }
    strcat(json, "]");
    free(rec);
    return json;
}

/* ------------------------------------------------------------------ */
/* Active (transmit) features — gated                                  */
/* ------------------------------------------------------------------ */
int deauth(const char *mac_str, int frames)
{
#if !CONFIG_WIFI_ATTACKS_ENABLED
    (void)mac_str;
    (void)frames;
    printf("Deauth not compiled in (CONFIG_WIFI_ATTACKS_ENABLED=0).\n");
    return -1;
#else
    if (!s_authorized)
    {
        printf("Refused: active features are not authorized. Run `authorize` first.\n");
        printf("Only use on networks you own or are explicitly authorized to test.\n");
        return -1;
    }
    uint8_t target[6];
    if (parse_mac(mac_str, target) != 0)
    {
        printf("Bad target BSSID '%s'\n", mac_str);
        return -1;
    }
    /* Real deauth injection needs a separate raw-injection radio
     * (monitor + injection) which conflicts with the running AP/STA.
     * This build keeps the NAT router running, so deauth is a scaffold:
     * it validates the target and reports that injection is not wired
     * into this APSTA build. Add a dedicated monitor NIC to transmit. */
    printf("deauth target ");
    print_mac(target);
    printf(" x%d — scaffold only in this APSTA build; no packets sent.\n", frames);
    return 0;
#endif
}

int beacon_flood(const char *mac_str, int frames)
{
#if !CONFIG_WIFI_ATTACKS_ENABLED
    (void)mac_str;
    (void)frames;
    printf("Beacon flood not compiled in (CONFIG_WIFI_ATTACKS_ENABLED=0).\n");
    return -1;
#else
    if (!s_authorized)
    {
        printf("Refused: active features are not authorized. Run `authorize` first.\n");
        return -1;
    }
    uint8_t target[6];
    if (parse_mac(mac_str, target) != 0)
    {
        printf("Bad target BSSID '%s'\n", mac_str);
        return -1;
    }
    printf("beacon flood target ");
    print_mac(target);
    printf(" x%d — scaffold only in this APSTA build; no packets sent.\n", frames);
    return 0;
#endif
}

/* ------------------------------------------------------------------ */
/* Console command wrappers                                            */
/* ------------------------------------------------------------------ */
static int scanap_cmd(int argc, char **argv) { (void)argc; (void)argv; return start_ap_scan(); }
static int scanall_cmd(int argc, char **argv) { (void)argc; (void)argv; return start_all_scan(); }
static int stopscan_cmd(int argc, char **argv) { (void)argc; (void)argv; return stop_scan(); }
static int aplist_cmd(int argc, char **argv) { (void)argc; (void)argv; return print_ap_records(); }
static int clients_cmd(int argc, char **argv) { (void)argc; (void)argv; return list_clients(); }

typedef struct
{
    struct arg_str *bssid;
    struct arg_int *frames;
    struct arg_end *end;
} flood_args;

static int deauth_cmd(int argc, char **argv)
{
    flood_args a;
    a.bssid = arg_str1(NULL, NULL, "<bssid>", "target AP MAC aa:bb:cc:dd:ee:ff");
    a.frames = arg_intn(NULL, NULL, "[count]", 1, 1000, "number of frames (default 1)");
    a.end = arg_end(2);
    int nerrors = arg_parse(argc, argv, (void **)&a);
    if (nerrors)
    {
        arg_print_errors(stderr, a.end, argv[0]);
        return -1;
    }
    int cnt = a.frames->count > 0 ? a.frames->ival[0] : 1;
    return deauth((char *)a.bssid->sval[0], cnt);
}

static int beacon_cmd(int argc, char **argv)
{
    flood_args a;
    a.bssid = arg_str1(NULL, NULL, "<bssid>", "spoofed AP MAC aa:bb:cc:dd:ee:ff");
    a.frames = arg_intn(NULL, NULL, "[count]", 1, 1000, "beacon frames (default 1)");
    a.end = arg_end(2);
    int nerrors = arg_parse(argc, argv, (void **)&a);
    if (nerrors)
    {
        arg_print_errors(stderr, a.end, argv[0]);
        return -1;
    }
    int cnt = a.frames->count > 0 ? a.frames->ival[0] : 1;
    return beacon_flood((char *)a.bssid->sval[0], cnt);
}

typedef struct
{
    struct arg_str *bssid;
    struct arg_end *end;
} stat_args;

static int stat_cmd(int argc, char **argv)
{
    stat_args a;
    a.bssid = arg_str1(NULL, NULL, "<bssid>", "AP MAC to inspect aa:bb:cc:dd:ee:ff");
    a.end = arg_end(1);
    int nerrors = arg_parse(argc, argv, (void **)&a);
    if (nerrors)
    {
        arg_print_errors(stderr, a.end, argv[0]);
        return -1;
    }
    return wifi_stat((char *)a.bssid->sval[0]);
}

/* ------------------------------------------------------------------ */
/* Registration                                                        */
/* ------------------------------------------------------------------ */
void register_wifi(void)
{
    const esp_console_cmd_t ap = {
        .command = "scanap", .help = "Scan for Wi-Fi access points and list them",
        .func = &scanap_cmd,
    };
    const esp_console_cmd_t all = {
        .command = "scanall", .help = "Scan APs and list our AP clients",
        .func = &scanall_cmd,
    };
    const esp_console_cmd_t stop = {
        .command = "stopscan", .help = "Stop a running scan",
        .func = &stopscan_cmd,
    };
    const esp_console_cmd_t aprec = {
        .command = "aplist", .help = "Print cached AP scan records",
        .func = &aplist_cmd,
    };
    const esp_console_cmd_t clients = {
        .command = "clients", .help = "List stations on our soft-AP",
        .func = &clients_cmd,
    };
    const esp_console_cmd_t authorize_cmd = {
        .command = "authorize", .help = "Enable active (transmit) Wi-Fi features for this session",
        .func = authorize,
    };
    const esp_console_cmd_t deauth_cmd_r = {
        .command = "deauth", .help = "Deauth flood a target BSSID [count]",
        .func = deauth_cmd,
    };
    const esp_console_cmd_t beacon_cmd_r = {
        .command = "beacon", .help = "Beacon (probe-response) flood [count]",
        .func = beacon_cmd,
    };
    const esp_console_cmd_t stat_cmd_r = {
        .command = "wifistat", .help = "Show details for a BSSID from the cache",
        .func = stat_cmd,
    };

    ESP_ERROR_CHECK(esp_console_cmd_register(&ap));
    ESP_ERROR_CHECK(esp_console_cmd_register(&all));
    ESP_ERROR_CHECK(esp_console_cmd_register(&stop));
    ESP_ERROR_CHECK(esp_console_cmd_register(&aprec));
    ESP_ERROR_CHECK(esp_console_cmd_register(&clients));
    ESP_ERROR_CHECK(esp_console_cmd_register(&authorize_cmd));
    ESP_ERROR_CHECK(esp_console_cmd_register(&deauth_cmd_r));
    ESP_ERROR_CHECK(esp_console_cmd_register(&beacon_cmd_r));
    ESP_ERROR_CHECK(esp_console_cmd_register(&stat_cmd_r));
    ESP_LOGI(TAG, "Wi-Fi test commands registered (passive on, active %s)",
#if CONFIG_WIFI_ATTACKS_ENABLED
             "compiled in but unauthorized"
#else
             "not compiled in"
#endif
    );
}
