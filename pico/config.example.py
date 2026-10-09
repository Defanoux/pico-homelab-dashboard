# Copy this file to config.py and fill in your own values.
# config.py is listed in .gitignore, so your Wi-Fi password never gets committed.
# Both versions (basic and truenas) read this same file.

WIFI_SSID = "your-wifi-name"      # Pico 2 W is 2.4 GHz only
WIFI_PASSWORD = "your-wifi-password"

# Your location for the outside weather screen, in decimal degrees.
LATITUDE = 0.0
LONGITUDE = 0.0

# False = Celsius, True = Fahrenheit (room and outside temperatures)
USE_FAHRENHEIT = False

# TrueNAS version only - the basic version ignores this.
# The IP address of the server running truenas_bridge.py.
TRUENAS_IP = "192.168.1.100"
