"""NI USB-6210 analog input via the nidaqmx driver.

USB-6210: 16 single-ended / 8 differential AI, 16-bit, 250 kS/s aggregate,
input ranges ±10, ±5, ±1, ±0.2 V. No analog outputs.
"""

try:
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, TerminalConfiguration
    from nidaqmx.system import System
except ImportError:  # pragma: no cover - reported in the UI
    nidaqmx = None

AI_CHANNELS = [f"ai{i}" for i in range(16)]
TERMINAL_CONFIGS = ["Differential", "RSE", "NRSE"]
VOLTAGE_RANGES = {"±10 V": 10.0, "±5 V": 5.0, "±1 V": 1.0, "±0.2 V": 0.2}
MAX_AGGREGATE_RATE = 250_000.0


def parse_channels(text):
    """Parse 'ai0, ai2, ai4-ai7' (or '0,2,4-7') into ['ai0', 'ai2', 'ai4', ...]."""
    channels = []
    for part in text.replace(";", ",").split(","):
        part = part.strip().lower().replace(" ", "")
        if not part:
            continue
        try:
            if "-" in part or ":" in part:
                lo, hi = part.replace(":", "-").split("-", 1)
                lo, hi = int(lo.removeprefix("ai")), int(hi.removeprefix("ai"))
                nums = range(min(lo, hi), max(lo, hi) + 1)
            else:
                nums = [int(part.removeprefix("ai"))]
        except ValueError:
            raise ValueError(f"Invalid channel '{part}' (use e.g. ai0, ai2 or ai0-ai3)")
        for n in nums:
            if not 0 <= n <= 15:
                raise ValueError(f"ai{n} does not exist on the USB-6210 (ai0–ai15)")
            if f"ai{n}" not in channels:
                channels.append(f"ai{n}")
    if not channels:
        raise ValueError("Enter at least one AI channel")
    return channels


class NIDaq:
    def __init__(self):
        self._task = None
        self._cfg = None

    @staticmethod
    def available():
        return nidaqmx is not None

    @staticmethod
    def list_devices():
        """Return [(name, product_type), ...] for NI-DAQmx devices on this PC."""
        if nidaqmx is None:
            return []
        try:
            return [(d.name, d.product_type) for d in System.local().devices]
        except Exception:
            # Driver runtime (NI-DAQmx) not installed or not reachable.
            return []

    @property
    def is_open(self):
        return self._task is not None

    def connect(self, cfg):
        if nidaqmx is None:
            raise RuntimeError("nidaqmx is not installed (pip install nidaqmx)")
        if not cfg["device"]:
            raise ValueError("Select a DAQ device")
        channels = cfg["channels"]
        if not channels:
            raise ValueError("Select at least one AI channel")

        rate = float(cfg["sample_rate"])
        if rate <= 0 or rate * len(channels) > MAX_AGGREGATE_RATE:
            raise ValueError(
                f"Sample rate × channels must be ≤ {MAX_AGGREGATE_RATE:,.0f} S/s "
                f"(got {rate * len(channels):,.0f})"
            )
        if cfg["terminal_config"] == "Differential":
            bad = [c for c in channels if int(c[2:]) >= 8]
            if bad:
                raise ValueError(
                    "Differential mode uses ai0–ai7 only (ai8–ai15 are their negative inputs): "
                    + ", ".join(bad)
                )

        self.disconnect()
        term = {
            "Differential": TerminalConfiguration.DIFF,
            "RSE": TerminalConfiguration.RSE,
            "NRSE": TerminalConfiguration.NRSE,
        }[cfg["terminal_config"]]
        vmax = VOLTAGE_RANGES[cfg["voltage_range"]]

        task = nidaqmx.Task()
        try:
            for ch in channels:
                task.ai_channels.add_ai_voltage_chan(
                    f"{cfg['device']}/{ch}",
                    terminal_config=term,
                    min_val=-vmax,
                    max_val=vmax,
                )
            task.timing.cfg_samp_clk_timing(
                rate,
                sample_mode=AcquisitionType.FINITE,
                samps_per_chan=int(cfg["samples_per_channel"]),
            )
            task.control(nidaqmx.constants.TaskMode.TASK_VERIFY)
        except Exception:
            task.close()
            raise
        self._task = task
        self._cfg = dict(cfg)

    def disconnect(self):
        if self._task is not None:
            try:
                self._task.close()
            finally:
                self._task = None

    def read_average(self):
        """Acquire one finite block and return {channel: mean voltage}."""
        if not self.is_open:
            raise RuntimeError("DAQ task is not configured")
        n = int(self._cfg["samples_per_channel"])
        timeout = n / float(self._cfg["sample_rate"]) + 2.0
        data = self._task.read(number_of_samples_per_channel=n, timeout=timeout)
        self._task.stop()
        channels = self._cfg["channels"]
        if len(channels) == 1:
            data = [data]
        return {ch: sum(vals) / len(vals) for ch, vals in zip(channels, data)}
