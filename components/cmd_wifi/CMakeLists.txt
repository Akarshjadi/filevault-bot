idf_component_register(SRCS "cmd_wifi.c"
                    INCLUDE_DIRS .
                    REQUIRES console nvs_flash driver esp_wifi esp_netif esp_event esp_system)
