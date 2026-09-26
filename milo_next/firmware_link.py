"""Fail-closed V6 handshake, independent of ROS and the legacy actuator.

The caller serializes callbacks and tick(), uses receipt-time stamps, and owns
the single ROS command publisher. No automatic enable or fault reset occurs.
This does not brake an already moving servo.
"""
import time

from .safety import SafetyError


class FirmwareLink:
    VERSION = 6003
    VERSION_TTL = 2.5
    STATE_TTL = .5
    RENEW_INTERVAL = .25

    def __init__(self, publish_control, *, clock=time.monotonic):
        self.publish_control = publish_control
        self.clock = clock
        self.version = None
        self.version_at = None
        self.state_at = None
        self.state_serial = 0
        self.enabled = False
        self.fault = False
        self.pending = 0
        self.requested = False
        self.request_serial = 0
        self.last_renew = None
        self.error = None
        self.sequence = None
        self.await_sequence = None

    def receive_version(self, value):
        self.version = value if type(value) is int else None
        self.version_at = self.clock()
        if self.version != self.VERSION:
            self.inhibit('unsupported_firmware')

    def receive_state(self, value):
        if (type(value) is not int or value < 0 or value & ~0x7fff0703
                or ((value >> 8) & 7) > 6):
            self.state_at = None
            self.inhibit('invalid_controller_state')
            return
        self.state_serial += 1
        self.state_at = self.clock()
        if self.requested and self.enabled and not (value & 1):
            self.inhibit('controller_disabled_or_restarted')
        self.enabled = bool(value & 1)
        self.fault = bool(value & 2)
        self.pending = (value >> 8) & 7
        sequence = value >> 16
        if self.sequence is not None and sequence != self.sequence:
            if self.await_sequence != sequence:
                self.inhibit('unexpected_controller_command_or_restart')
            else:
                self.await_sequence = None
        self.sequence = sequence
        if self.fault:
            self.inhibit('controller_fault')

    def _fresh(self):
        now = self.clock()
        return (self.version == self.VERSION and self.version_at is not None
                and 0 <= now - self.version_at <= self.VERSION_TTL
                and self.state_at is not None
                and 0 <= now - self.state_at <= self.STATE_TTL)

    def enable(self):
        if not self._fresh() or self.fault or self.pending or self.await_sequence is not None:
            raise SafetyError('controller_not_ready')
        self.requested = True
        self.request_serial = self.state_serial
        self.error = None
        try:
            self.publish_control(1)
        except Exception:
            self.requested = False
            raise
        self.last_renew = self.clock()

    def inhibit(self, reason=None):
        was_requested = self.requested
        self.requested = False
        if reason is not None:
            self.error = reason
        # Do not send unknown protocol messages to a legacy controller.
        if was_requested:
            self.publish_control(0)

    def reset_fault(self):
        if self.requested or not self._fresh() or self.pending or self.await_sequence is not None:
            raise SafetyError('controller_reset_not_ready')
        self.publish_control(2)
        # Require a fresh controller report, never assume reset succeeded.
        self.state_at = None

    def tick(self):
        if not self.requested:
            return
        if not self._fresh() or self.fault:
            self.inhibit('controller_status_lost')
            return
        if self.clock() - self.last_renew >= self.RENEW_INTERVAL:
            try:
                self.publish_control(1)
                self.last_renew = self.clock()
            except Exception:
                self.requested = False
                self.error = 'control_publish_failed'
                raise

    def require_idle(self):
        self.tick()
        if (not self.requested or not self._fresh() or not self.enabled
                or self.fault or self.pending or self.await_sequence is not None
                or self.state_serial <= self.request_serial):
            raise SafetyError('controller_not_acknowledged_or_busy')

    def command_sent(self):
        """Do not permit a second publication on the same idle report."""
        self.request_serial = self.state_serial
        self.await_sequence = (self.sequence + 1) & 0x7fff

    def snapshot(self):
        return {'version': self.version, 'fresh': self._fresh(),
                'requested': self.requested, 'enabled': self.enabled,
                'fault': self.fault, 'pending_joint': self.pending,
                'accepted_sequence': self.sequence, 'await_sequence': self.await_sequence,
                'error': self.error}
