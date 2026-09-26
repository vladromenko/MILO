"""Bounded, unprivileged hardware readings. Unavailable counters stay null."""
from pathlib import Path
import time


class Diagnostics:
    def __init__(self):
        self._previous = None
        self._at = 0
        self._cached = {}

    def snapshot(self):
        now = time.monotonic()
        if now - self._at < 2:
            return self._cached
        result = {'cpu_percent': None, 'ram_used_mb': None, 'ram_total_mb': None,
                  'gpu_percent': None, 'gpu_memory': 'shared system RAM', 'temperatures': {}}
        try:
            fields = [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
            total, idle = sum(fields), fields[3] + fields[4]
            if self._previous and total > self._previous[0]:
                result['cpu_percent'] = round(100 * (1 - (idle - self._previous[1]) /
                                                     (total - self._previous[0])), 1)
            self._previous = total, idle
            mem = {line.split(':')[0]: int(line.split()[1])
                   for line in Path('/proc/meminfo').read_text().splitlines()}
            result['ram_total_mb'] = round(mem['MemTotal'] / 1024)
            result['ram_used_mb'] = round((mem['MemTotal'] - mem['MemAvailable']) / 1024)
        except (OSError, ValueError, KeyError):
            pass
        for zone in list(Path('/sys/class/thermal').glob('thermal_zone*'))[:16]:
            try:
                result['temperatures'][(zone / 'type').read_text().strip()] = round(
                    float((zone / 'temp').read_text()) / 1000, 1)
            except (OSError, ValueError):
                pass
        for candidate in ('/sys/devices/platform/bus@0/17000000.gpu/load',
                          '/sys/devices/platform/17000000.gpu/load'):
            try:
                result['gpu_percent'] = float(Path(candidate).read_text()) / 10
                break
            except (OSError, ValueError):
                pass
        self._at, self._cached = now, result
        return result
