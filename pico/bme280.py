# Minimal BME280 driver for MicroPython (I2C) - part of pico-homelab-dashboard, MIT license
# Returns temperature (C), pressure (hPa), humidity (%RH)

from machine import I2C
import time

BME280_ADDR = 0x76  # use 0x77 if your module is wired with the address pin high


class BME280:
    def __init__(self, i2c, address=BME280_ADDR):
        self.i2c = i2c
        self.address = address
        self._load_calibration()
        # Mode: normal, oversampling x1 for all, no IIR filter
        self.i2c.writeto_mem(self.address, 0xF2, b'\x01')  # humidity oversampling x1
        self.i2c.writeto_mem(self.address, 0xF4, b'\x27')  # temp/pressure oversampling x1, normal mode
        self.i2c.writeto_mem(self.address, 0xF5, b'\xA0')  # config: standby 1000ms
        time.sleep_ms(100)  # let the sensor settle
        try:
            self.read()  # discard first reading - often inaccurate right after power-on
        except Exception:
            pass

    def _read16(self, reg, signed=False):
        data = self.i2c.readfrom_mem(self.address, reg, 2)
        val = data[1] << 8 | data[0]
        if signed and val > 32767:
            val -= 65536
        return val

    def _read8(self, reg):
        return self.i2c.readfrom_mem(self.address, reg, 1)[0]

    def _load_calibration(self):
        self.dig_T1 = self._read16(0x88)
        self.dig_T2 = self._read16(0x8A, signed=True)
        self.dig_T3 = self._read16(0x8C, signed=True)
        self.dig_P1 = self._read16(0x8E)
        self.dig_P2 = self._read16(0x90, signed=True)
        self.dig_P3 = self._read16(0x92, signed=True)
        self.dig_P4 = self._read16(0x94, signed=True)
        self.dig_P5 = self._read16(0x96, signed=True)
        self.dig_P6 = self._read16(0x98, signed=True)
        self.dig_P7 = self._read16(0x9A, signed=True)
        self.dig_P8 = self._read16(0x9C, signed=True)
        self.dig_P9 = self._read16(0x9E, signed=True)
        self.dig_H1 = self._read8(0xA1)
        self.dig_H2 = self._read16(0xE1, signed=True)
        self.dig_H3 = self._read8(0xE3)
        e4 = self._read8(0xE4)
        e5 = self._read8(0xE5)
        e6 = self._read8(0xE6)
        self.dig_H4 = (e4 << 4) | (e5 & 0x0F)
        if self.dig_H4 > 2047:
            self.dig_H4 -= 4096
        self.dig_H5 = (e6 << 4) | (e5 >> 4)
        if self.dig_H5 > 2047:
            self.dig_H5 -= 4096
        self.dig_H6 = self._read8(0xE7)
        if self.dig_H6 > 127:
            self.dig_H6 -= 256

    def read(self):
        """Returns (temp_c, pressure_hpa, humidity_pct)"""
        data = self.i2c.readfrom_mem(self.address, 0xF7, 8)
        pres_raw = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
        temp_raw = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
        hum_raw = (data[6] << 8) | data[7]

        # Temperature
        var1 = (temp_raw / 16384.0 - self.dig_T1 / 1024.0) * self.dig_T2
        var2 = ((temp_raw / 131072.0 - self.dig_T1 / 8192.0) *
                (temp_raw / 131072.0 - self.dig_T1 / 8192.0)) * self.dig_T3
        t_fine = var1 + var2
        temperature = t_fine / 5120.0

        # Pressure
        var1 = t_fine / 2.0 - 64000.0
        var2 = var1 * var1 * self.dig_P6 / 32768.0
        var2 = var2 + var1 * self.dig_P5 * 2.0
        var2 = var2 / 4.0 + self.dig_P4 * 65536.0
        var1 = (self.dig_P3 * var1 * var1 / 524288.0 + self.dig_P2 * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * self.dig_P1
        if var1 == 0:
            pressure = 0
        else:
            pressure = 1048576.0 - pres_raw
            pressure = (pressure - var2 / 4096.0) * 6250.0 / var1
            var1 = self.dig_P9 * pressure * pressure / 2147483648.0
            var2 = pressure * self.dig_P8 / 32768.0
            pressure = pressure + (var1 + var2 + self.dig_P7) / 16.0
            pressure = pressure / 100.0  # hPa

        # Humidity
        h = t_fine - 76800.0
        h = ((hum_raw - (self.dig_H4 * 64.0 + self.dig_H5 / 16384.0 * h)) *
             (self.dig_H2 / 65536.0 * (1.0 + self.dig_H6 / 67108864.0 * h *
              (1.0 + self.dig_H3 / 67108864.0 * h))))
        h = h * (1.0 - self.dig_H1 * h / 524288.0)
        if h > 100:
            h = 100
        elif h < 0:
            h = 0

        return (temperature, pressure, h)
