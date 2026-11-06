# FrictionTester

Tkinter desktop app for a friction tester driven by a PLC (RS232) and an NI USB-6210 DAQ.

## Setup

1. Install the [NI-DAQmx driver](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html) (required for the USB-6210).
2. Install Python packages:
   ```
   pip install -r requirements.txt
   ```
3. Run:
   ```
   python main.py
   ```

## Comm Settings page

**PLC – RS232:** COM port (auto-detected, ⟳ to rescan), baud rate, data bits, parity, stop bits,
flow control, terminator and timeout. *Connect* opens the port; *Send* writes a test command and
shows the reply.

**NI USB-6210:** device (auto-detected, prefers a 6210), AI channels ai0–ai15, terminal config
(Differential uses ai0–ai7), input range (±10 / ±5 / ±1 / ±0.2 V), sample rate (≤ 250 kS/s
aggregate) and samples per channel. *Connect* creates and verifies the DAQmx task; *Read* acquires
one block and shows the mean voltage per channel.

*Save Settings* writes `settings.json` next to `main.py` (git-ignored, machine-specific).
