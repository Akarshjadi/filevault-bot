# ISSUES — esp32_nat_router_extended GOD-TWEAK

Status legend: `open` / `fixing` / `done` / `wontfix`
Convention: every fix commit subject references the ID(s), e.g.
`fix(C-01,C-02): factory-reset now restarts via esp_restart`

| ID | Sev | File / function | Problem | Status |
|---|---|---|---|---|
| C-01 | C | esp32_nat_router.c app_main/checkForResetPinAndReset | app_main returns after erase_ns, no restart | done |
| C-02 | C | lockhandler.c unlock_handler | uninitialized `char *lock`; strcmp on garbage | done |
| C-03 | C | scan.c wifi_scan | returns pointer to dead stack frame | done |
| C-04 | C | applyhandler/indexhandler/lockhandler/portmaphandler/otahandler | `char buf[req->content_len]` VLA — stack overflow on big POST | done |
| C-05 | C | applyhandler.c setWpa2 | zero-length VLA when body empty | done |
| C-06 | C | cmd_nvs.c erase_all | nvs_close on uninitialized handle if open fails | done |
| C-07 | C | otahandler.c version_event_handler | realloc unchecked, memcpy to NULL | done |
| C-08 | C | otahandler.c updateVersion | file_buffer[file_size-1] underflow on empty response | done |
| C-09 | C | applyhandler.c getRedirectUrl | 16B host buffer, truncation, uninit strcmp/strcat | done |
| C-10 | C | advancedhandler.c | size-calc undercount, heap overflow on long hostname | done |
| C-11 | C | clientshandler.c | 1000B buffer, overflow at ~9 clients | done |
| C-12 | C | dnserver.c | bind-failure falls into recvfrom loop | done |
| C-13 | C | scan.c wifi_scan | AP torn down during scan; reboots after scan | done |
| C-14 | C | esp32_nat_router.c wifi_event_handler | blind reconnect forever when uplink absent | done |
| S-01 | S | otahandler.c ota_post_handler | one-byte overflow on header write | done |
| S-02 | S | otahandler.c | cert verification disabled | done |
| S-03 | S | http_server.c | /ota /portmap /advanced /reset not authenticated | done |
| U-01 | U | statichandler.c | captive redirect hardcodes port 80 | done |
| U-02 | U | apply.html | meta refresh hardcodes port 80 | done |
| U-03 | U | indexhandler.c getRedirectUrl | redirect URL wrong port | done |
| B-01 | B | dnserver.c | port hardcoded 53 | open |
| B-02 | B | esp32_nat_router.c | hardcoded log messages | open |

## End-to-end acceptance test

```
1. power-cycle the device; serial console must be quiet except INFO logs
   (no reconnect storm, no crash).
2. mac joins `toddler` AP.
3. open http://192.168.4.1:8080/login — UI loads (chunked, fast, no stuck tab).
4. POST wrong password -> "Wrong password", login page unchanged.
5. POST correct password -> redirect to /.
6. press "Scan" -> device stays up, AP not torn down, scan list shows
   nearby APs with RSSI (color-coded).
7. pick uplink, save -> device connects, serial shows "STA uplink RSSI: ...".
8. factory-reset via 5s reset-pin -> device reboots, AP back to defaults.
9. push OTA update -> success, cert verified (S-02 done) or logged skip.
```
