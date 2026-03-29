# Arduino Projects

## Dependencies

- [arduino-cli](https://arduino.github.io/arduino-cli/) - Arduino command-line interface

### Installation (macOS)

```bash
brew install arduino-cli
```

## Workflow: Discover, Compile, Upload

### 1. Discover connected boards

Plug in your board and list connected devices:

```bash
arduino-cli board list
```

This will show the port (e.g. `/dev/cu.usbmodem5A4E1036791`) and board type if recognised.

### 2. Compile a sketch

```bash
arduino-cli compile --fqbn <board_fqbn> sketches/<sketch_name>
```

For example, to compile for an ESP32-S3:

```bash
arduino-cli compile --fqbn esp32:esp32:esp32s3 sketches/blinker
```

### 3. Upload to the board

```bash
arduino-cli upload --fqbn <board_fqbn> -p <port> sketches/<sketch_name>
```

For example:

```bash
arduino-cli upload --fqbn esp32:esp32:esp32s3 -p /dev/cu.usbmodem5A4E1036791 sketches/blinker
```

### 4. Monitor serial output

```bash
arduino-cli monitor -p <port>
```

### 5. Log serial output to file

```bash
arduino-cli monitor -p <port> --raw | tee logs/thermometer_output.txt
```

Logs are saved to the `logs/` directory (gitignored).

## Other useful commands

- `arduino-cli sketch new <sketch_name>` - create a new sketch
- `arduino-cli board attach <port> -b <fqbn>` - attach a board to a sketch (esp32:esp32:esp32s3 for ESP32-S3)
- `arduino-cli lib install "library_name"` - install a library
- `arduino-cli core list` - list installed board cores

## For secrets include an arduino_secrets.h file in the sketch directory
e.g. 
```
#define SECRET_DEVICE_KEY "12345"
#define SECRET_OPTIONAL_PASS "my_wifi_pass"
#define SECRET_SSID "my_wifi_ssid"
```

Include this file in your arduino project and add it to the .gitignore file
`#include "arduino_secrets.h"`
