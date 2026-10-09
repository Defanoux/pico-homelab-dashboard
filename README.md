# Pico Homelab Dashboard

A desk display for the Raspberry Pi Pico 2 W that shows room conditions, outside weather, and (optionally) live stats from a TrueNAS server. 3D-printable case on MakerWorld: https://makerworld.com/en/models/3413429-pico-homelab-dashboard

## Two versions

| Version | Screens | Needs a server? |
|---|---|---|
| `pico/basic/` | Room, Outside, System | No |
| `pico/truenas/` | Room, Outside, System, Server, Graph | Yes, runs `server/truenas_bridge.py` |

## Hardware

- Raspberry Pi Pico 2 W (with headers)
- Waveshare Pico-OLED-1.3 (SH1107, 128x64, two onboard buttons)
- BME280 breakout, I2C
- 4 female-to-female jumper wires

BME280 wiring:

| BME280 | Pico 2 W |
|---|---|
| VCC | 3V3 (pin 36) |
| GND | GND (pin 38) |
| SDA | GP0 (pin 1) |
| SCL | GP1 (pin 2) |

The OLED board uses GP8-GP12 and buttons on GP15/GP17, so there are no pin conflicts.

## Pico setup

1. Flash MicroPython for the Pico 2 W (`RPI_PICO2_W` firmware from micropython.org): hold BOOTSEL while plugging in USB and drag the `.uf2` onto the drive that appears.
2. Copy `pico/config.example.py` to `config.py` and fill in your Wi-Fi name, password, location, and temperature unit.
3. Using Thonny (or `mpremote`), copy these files to the root of the Pico:
   - `main.py` from **either** `pico/basic/` **or** `pico/truenas/`
   - `config.py`
   - `pico/bme280.py` (included in this repo)
   - `sh1107.py`: download Waveshare's Pico-OLED-1.3 demo code from their wiki, take the SPI driver class, and save it as `sh1107.py`. It must define `OLED_1inch3`.
4. Unplug and replug. The Pico runs `main.py` automatically on boot.

The Pico 2 W only connects to 2.4 GHz Wi-Fi. If it shows "WiFi: offline", connect with Thonny and read the status code it prints.

## TrueNAS setup (truenas version only)

The bridge is a single Python file with no dependencies. It runs on the TrueNAS host and serves stats as JSON on port 9191.

**1. Copy the script into your home directory.** Your home directory lives on a dataset, so app deletions and TrueNAS updates won't remove it. From your computer:

```
ssh YOUR_USER@TRUENAS_IP 'mkdir -p ~/truenas-bridge'
scp server/truenas_bridge.py YOUR_USER@TRUENAS_IP:~/truenas-bridge/
```

**2. Test it.** On TrueNAS, run:

```
python3 ~/truenas-bridge/truenas_bridge.py
```

Then from another computer:

```
curl http://TRUENAS_IP:9191/stats
```

You should get JSON with `cpu_percent`, `memory`, and `pools`. Stop the test with `Ctrl+C`.

**3. Start it at boot.** Find your home path with `echo $HOME` on TrueNAS. In the web UI, go to **System → Advanced Settings → Init/Shutdown Scripts → Add**:

- Type: **Command**
- When: **Post Init**
- Command (replace `YOUR_USER` and the home path):

```
systemd-run --unit=truenas-bridge --uid=YOUR_USER -p Restart=always /usr/bin/python3 /mnt/POOL/YOUR_USER/truenas-bridge/truenas_bridge.py
```

To start it now without rebooting, run the same command over SSH with `sudo` in front, then check it with `sudo systemctl status truenas-bridge`.

**4. Point the Pico at it.** In `config.py`, set:

```python
TRUENAS_IP = "192.168.1.50"   # your TrueNAS IP
```

Give TrueNAS a static IP or a DHCP reservation on your router. If its IP changes, the Pico loses the connection and shows "Fetch err".

### Notes

- RAM usage looks high because ZFS uses free RAM as cache (ARC). This is normal.
- The bridge works on any Linux server. Without ZFS, the pool stat is skipped and everything else still shows.
