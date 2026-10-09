# main.py - Pico Homelab Dashboard (basic version, no server needed)
# Screens: ROOM, OUTSIDE, SYSTEM
# Button A (GP15): short press = next screen
# Button B (GP17): short press = previous screen, hold 600 ms+ = refresh

import time
import network
import urequests
from machine import Pin, I2C

from sh1107 import OLED_1inch3
from bme280 import BME280
import config

# ---------------- CONFIG ----------------
WIFI_SSID = config.WIFI_SSID
WIFI_PASSWORD = config.WIFI_PASSWORD
LATITUDE = config.LATITUDE
LONGITUDE = config.LONGITUDE
USE_FAHRENHEIT = config.USE_FAHRENHEIT

WEATHER_REFRESH_SECONDS = 600  # refresh outside weather every 10 minutes
SCREEN_AUTO_REFRESH_SECONDS = 5  # repaint room/system screens this often

IDLE_DIM_SECONDS = 30  # dim the screen after this many seconds of no button activity
DIM_CONTRAST = 5  # very low but not fully off
NORMAL_CONTRAST = 0x6f  # matches the driver's default init contrast

UNIT = "F" if USE_FAHRENHEIT else "C"

# ---------------- HARDWARE SETUP ----------------

# OLED over SPI (Waveshare's driver, pins are hardcoded inside sh1107.py:
# DC=GP8, RST=GP12, MOSI=GP11, SCK=GP10, CS=GP9)
oled = OLED_1inch3()

# BME280 over I2C
i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=400000)
bme = BME280(i2c)

# Buttons (onboard the Waveshare OLED board)
btn_a = Pin(15, Pin.IN, Pin.PULL_UP)
btn_b = Pin(17, Pin.IN, Pin.PULL_UP)

# ---------------- STATE ----------------
SCREEN_ROOM = 0
SCREEN_WEATHER = 1
SCREEN_SYSTEM = 2
SCREEN_COUNT = 3

BOOT_MS = time.ticks_ms()  # uptime reference (the RTC isn't set without NTP)

current_screen = SCREEN_ROOM
last_weather = {"temp": None, "desc": "...", "humidity": None}
last_weather_fetch = 0
wlan = None
refresh_icon_active = False
is_fetching = False
is_dimmed = False
last_activity_ms = 0


def set_contrast(level):
    """SH1107 contrast command (0x81 + level). Kept here instead of in sh1107.py
    so the stock Waveshare driver works without modification."""
    oled.write_cmd(0x81)
    oled.write_cmd(level & 0xff)


def c_to_unit(temp_c):
    return temp_c * 9 / 5 + 32 if USE_FAHRENHEIT else temp_c


# ---------------- WIFI ----------------
wifi_connect_started_ms = None
wifi_connect_attempted = False
WIFI_CONNECT_TIMEOUT_MS = 15000


def start_wifi_connect():
    """Starts a Wi-Fi connection attempt and returns immediately (non-blocking).
    check_wifi_connect_progress() is polled from the main loop afterward."""
    global wlan, wifi_connect_started_ms, wifi_connect_attempted
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    print("Starting WiFi connect to SSID:", WIFI_SSID)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        wifi_connect_started_ms = time.ticks_ms()
        wifi_connect_attempted = True


def check_wifi_connect_progress():
    """Returns True exactly once, the moment the connection succeeds."""
    global wifi_connect_started_ms
    if not wifi_connect_attempted or wifi_connect_started_ms is None:
        return False
    if wlan.isconnected():
        print("WiFi connected! IP:", wlan.ifconfig()[0])
        wifi_connect_started_ms = None
        return True
    status = wlan.status()
    elapsed = time.ticks_diff(time.ticks_ms(), wifi_connect_started_ms)
    if elapsed >= WIFI_CONNECT_TIMEOUT_MS:
        status_names = {
            0: "IDLE", 1: "CONNECTING", 2: "WRONG_PASSWORD",
            3: "NO_AP_FOUND", -1: "CONNECT_FAIL", -2: "BADAUTH",
            -3: "FAIL", 1010: "GOT_IP",
        }
        print("WiFi connect TIMED OUT. Status code: {} ({})".format(
            status, status_names.get(status, "UNKNOWN")))
        wifi_connect_started_ms = None
    return False


def get_rssi():
    if wlan is None or not wlan.isconnected():
        return None
    try:
        return wlan.status('rssi')
    except Exception:
        return None


def draw_wifi_icon():
    """Signal bars in the top right corner. X = disconnected or very weak."""
    icon_x, icon_y, bar_w, gap, max_h = 108, 0, 3, 1, 8
    rssi = get_rssi()
    if rssi is None or rssi < -80:
        oled.line(icon_x, icon_y, icon_x + 7, icon_y + 7, 1)
        oled.line(icon_x, icon_y + 7, icon_x + 7, icon_y, 1)
        return
    if rssi >= -50:
        bars = 4
    elif rssi >= -60:
        bars = 3
    elif rssi >= -70:
        bars = 2
    else:
        bars = 1
    for i in range(4):
        bar_h = 2 + i * 2
        x = icon_x + i * (bar_w + gap)
        y = icon_y + (max_h - bar_h)
        if i < bars:
            oled.fill_rect(x, y, bar_w, bar_h, 1)
        else:
            oled.rect(x, y, bar_w, bar_h, 1)


def draw_refresh_icon(x=114, y=52, active=False):
    """Open-ring refresh icon, bottom right. Solid square while B is held."""
    cx, cy, r = x + 5, y + 5, 5
    if active:
        oled.fill_rect(x, y, 11, 11, 1)
        return
    oled.ellipse(cx, cy, r, r, 1, False, 0b0111)
    oled.line(cx + r, cy, cx + r - 3, cy - 2, 1)
    oled.line(cx + r, cy, cx + r - 1, cy + 3, 1)


# ---------------- WEATHER ----------------
WEATHER_CODES = {
    0: "Clear", 1: "Clear", 2: "P Cloudy", 3: "Cloudy",
    45: "Fog", 48: "Fog",
    51: "L Drizzle", 53: "Drizzle", 55: "H Drizzle",
    61: "L Rain", 63: "Rain", 65: "H Rain",
    71: "L Snow", 73: "Snow", 75: "H Snow",
    80: "Showers", 81: "Showers", 82: "Storms",
    95: "Storm", 96: "Storm", 99: "Storm",
}


def fetch_weather(manual=False):
    """Current outside conditions from Open-Meteo (no API key needed).
    manual=True shows 'Fetching...'; background refreshes stay silent."""
    global last_weather_fetch, is_fetching
    if wlan is None or not wlan.isconnected():
        return
    if manual:
        is_fetching = True
        draw_current_screen()
    url = ("https://api.open-meteo.com/v1/forecast?latitude={}&longitude={}"
           "&current=temperature_2m,relative_humidity_2m,weather_code").format(LATITUDE, LONGITUDE)
    if USE_FAHRENHEIT:
        url += "&temperature_unit=fahrenheit"
    try:
        resp = urequests.get(url)
        data = resp.json()
        resp.close()
        current = data["current"]
        last_weather["temp"] = current.get("temperature_2m")
        last_weather["humidity"] = current.get("relative_humidity_2m")
        last_weather["desc"] = WEATHER_CODES.get(current.get("weather_code", -1), "Unknown")
        last_weather_fetch = time.time()
    except Exception as e:
        last_weather["desc"] = "Fetch err"
        last_weather_fetch = time.time()  # back off instead of retrying every loop
        print("Weather fetch failed:", e)
    finally:
        is_fetching = False


# ---------------- SCREENS ----------------
def draw_header(title):
    oled.fill(0)
    oled.text(title, 0, 0, 1)
    oled.hline(0, 9, 128, 1)


def draw_footer():
    draw_refresh_icon(active=refresh_icon_active)
    draw_wifi_icon()
    oled.show()


def draw_room_screen():
    draw_header("ROOM")
    try:
        temp_c, pressure_hpa, humidity = bme.read()
        oled.text("Temp: {:.1f}{}".format(c_to_unit(temp_c), UNIT), 0, 14, 1)
        oled.text("Hum:  {:.0f}%".format(humidity), 0, 26, 1)
        oled.text("Pres: {:.0f}hPa".format(pressure_hpa), 0, 38, 1)
    except Exception as e:
        oled.text("Sensor error", 0, 14, 1)
        print("BME280 read failed:", e)
    draw_footer()


def draw_weather_screen():
    draw_header("OUTSIDE")
    if is_fetching:
        oled.text("Fetching...", 0, 14, 1)
    elif last_weather["temp"] is not None:
        oled.text("Temp: {:.0f}{}".format(last_weather["temp"], UNIT), 0, 14, 1)
        oled.text("Hum:  {:.0f}%".format(last_weather["humidity"]), 0, 26, 1)
        oled.text(last_weather["desc"], 0, 38, 1)
    else:
        oled.text("No data yet", 0, 14, 1)
        oled.text("Hold B to fetch", 0, 26, 1)
    draw_footer()


def draw_system_screen():
    draw_header("SYSTEM")
    if wlan is not None and wlan.isconnected():
        oled.text("WiFi: connected", 0, 14, 1)
        oled.text("IP: {}".format(wlan.ifconfig()[0]), 0, 26, 1)
        rssi = get_rssi()
        if rssi is not None:
            oled.text("RSSI: {}dBm".format(rssi), 0, 38, 1)
    else:
        oled.text("WiFi: offline", 0, 14, 1)
    uptime_min = time.ticks_diff(time.ticks_ms(), BOOT_MS) // 60000
    oled.text("Up: {}m".format(uptime_min), 0, 50, 1)
    draw_footer()


def draw_current_screen():
    if current_screen == SCREEN_ROOM:
        draw_room_screen()
    elif current_screen == SCREEN_WEATHER:
        draw_weather_screen()
    elif current_screen == SCREEN_SYSTEM:
        draw_system_screen()


# ---------------- BUTTON HANDLING ----------------
LONG_PRESS_MS = 600

_btn_a_pressed_since = None
_btn_b_pressed_since = None
_btn_b_long_fired = False


def any_button_pressed():
    """Instant raw check, used to wake the display on press, not release."""
    return btn_a.value() == 0 or btn_b.value() == 0


def check_buttons():
    """Non-blocking button state machine. Returns:
    'short_a'       - A released after a short press
    'short_b'       - B released before the long-press threshold
    'long_b_start'  - B just crossed the threshold (fires once)
    'long_b_active' - B still held past the threshold
    None            - nothing happened"""
    global _btn_a_pressed_since, _btn_b_pressed_since, _btn_b_long_fired
    result = None

    if btn_a.value() == 0:
        if _btn_a_pressed_since is None:
            _btn_a_pressed_since = time.ticks_ms()
    elif _btn_a_pressed_since is not None:
        held_ms = time.ticks_diff(time.ticks_ms(), _btn_a_pressed_since)
        _btn_a_pressed_since = None
        if held_ms < LONG_PRESS_MS:
            result = "short_a"

    if btn_b.value() == 0:
        if _btn_b_pressed_since is None:
            _btn_b_pressed_since = time.ticks_ms()
            _btn_b_long_fired = False
        if time.ticks_diff(time.ticks_ms(), _btn_b_pressed_since) >= LONG_PRESS_MS:
            if not _btn_b_long_fired:
                _btn_b_long_fired = True
                result = "long_b_start"
            else:
                result = "long_b_active"
    elif _btn_b_pressed_since is not None:
        was_long = _btn_b_long_fired
        _btn_b_pressed_since = None
        _btn_b_long_fired = False
        if not was_long:
            result = "short_b"

    return result


# ---------------- MAIN LOOP ----------------
def main():
    global current_screen, refresh_icon_active, is_dimmed, last_activity_ms

    draw_current_screen()  # room screen works before Wi-Fi is up
    start_wifi_connect()

    last_draw = time.time()
    last_activity_ms = time.ticks_ms()

    while True:
        action = check_buttons()

        if action is not None or any_button_pressed():
            last_activity_ms = time.ticks_ms()
            if is_dimmed:
                set_contrast(NORMAL_CONTRAST)
                is_dimmed = False

        if check_wifi_connect_progress():
            fetch_weather()
            draw_current_screen()

        if action == "short_a":
            current_screen = (current_screen + 1) % SCREEN_COUNT
            draw_current_screen()
        elif action == "short_b":
            current_screen = (current_screen - 1) % SCREEN_COUNT
            draw_current_screen()
        elif action == "long_b_start":
            refresh_icon_active = True
            if current_screen == SCREEN_WEATHER:
                fetch_weather(manual=True)
            draw_current_screen()
        elif action == "long_b_active":
            draw_current_screen()

        # Independent of the branches above so the icon can never get stuck.
        if refresh_icon_active and action not in ("long_b_start", "long_b_active"):
            refresh_icon_active = False
            draw_current_screen()

        if not is_dimmed and time.ticks_diff(time.ticks_ms(), last_activity_ms) >= IDLE_DIM_SECONDS * 1000:
            set_contrast(DIM_CONTRAST)
            is_dimmed = True

        now = time.time()
        if now - last_draw >= SCREEN_AUTO_REFRESH_SECONDS:
            if current_screen in (SCREEN_ROOM, SCREEN_SYSTEM):
                draw_current_screen()
            last_draw = now

        if wlan is not None and wlan.isconnected():
            if now - last_weather_fetch >= WEATHER_REFRESH_SECONDS:
                fetch_weather()
                if current_screen == SCREEN_WEATHER:
                    draw_current_screen()

        time.sleep_ms(50)


if __name__ == "__main__":
    main()
