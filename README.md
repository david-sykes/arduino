# Arduino Projects

## Useful commands
`arduino-cli sketch new <sketch_name>`
`arduino-cli board attach <port> -b <fqbn>` - esp32:esp32:esp32s3 is the fqpn for esp32s3 dev board
`arduino-cli monitor`
`arduino-cli lib install "library_name"`

## For secrets include an arduino_secrets.h file in the sketch directory
e.g. 
```
#define SECRET_DEVICE_KEY "12345"
#define SECRET_OPTIONAL_PASS "my_wifi_pass"
#define SECRET_SSID "my_wifi_ssid"
```

Include this file in your arduino project and add it to the .gitignore file
`#include "arduino_secrets.h"`
