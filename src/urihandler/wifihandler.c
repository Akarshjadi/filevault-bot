/* Web UI handlers for the Wi-Fi test suite:
 *   GET /wifi       -> interactive scan page (auto-refreshing)
 *   GET /api/wifi   -> JSON array of the most recent AP scan
 *
 * Public Domain (CC0), matching the rest of this project.
 */
#include "handler.h"
#include "cmd_wifi.h"
#include "router_globals.h"

static const char *TAG = "WifiHandler";

esp_err_t wifi_download_get_handler(httpd_req_t *req)
{
    if (isLocked())
    {
        return redirectToLock(req);
    }

    httpd_req_to_sockfd(req);

    extern const char wifi_start[] asm("_binary_wifi_html_start");
    extern const char wifi_end[] asm("_binary_wifi_html_end");
    const size_t wifi_html_size = (wifi_end - wifi_start);

    char *wifi_page = malloc(wifi_html_size);
    if (!wifi_page)
        return ESP_ERR_NO_MEM;
    strcpy(wifi_page, wifi_start);

    closeHeader(req);
    ESP_LOGI(TAG, "Requesting Wi-Fi test page");

    esp_err_t ret = httpd_resp_send(req, wifi_page, HTTPD_RESP_USE_STRLEN);
    free(wifi_page);
    return ret;
}

esp_err_t wifi_api_get_handler(httpd_req_t *req)
{
    if (isLocked())
    {
        return httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, NULL);
    }
    httpd_resp_set_type(req, "application/json");

    char *json = wifi_scan_json();
    if (!json)
    {
        return httpd_resp_send(req, "[]", HTTPD_RESP_USE_STRLEN);
    }
    esp_err_t ret = httpd_resp_send(req, json, HTTPD_RESP_USE_STRLEN);
    ESP_LOGD(TAG, "Wi-Fi scan JSON: %s", json);
    free(json);
    return ret;
}
