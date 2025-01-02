# Copyright 2024 Comcast Cable Communications Management, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0


from python_globalcache.exceptions import *
from python_globalcache.gcdevice import Device
from python_globalcache.gcirdb import get_signal_data, ir_dataset_to_json, redrat_ir_dataset_loader, irdb_health
import time
import asyncio
import math

import logging

logger = logging.getLogger(__name__)
logger.setLevel("DEBUG")

'''
GC Dispatcher class helps to connects and sends IR signals to the devices.
It has the following methods:
1. __init__(self) - Constructor to initialize the devices list and next IR ID
2. _get_host_port(self, host) - Returns (host, port) from host string
3. _get_ir_id(self) - Returns the next IR ID
4. dict_repr(self) - Returns the dictionary representation of the devices
5. health(self) - Returns the health of the devices
6. clear_device_list(self) - Clears the device list
7. get_device(self, host) - Returns the device from the host
8. add_device(self, host) - Adds the device to the list
9. _send_ir_signal_repeats(self, ir_port, signal_data, repeat=None, wait_for_response=True, id_=None) - Sends the IR signal with repeats
10. _send_ir_signal_duration(self, ir_port, signal_data, seconds, check_max_repeats=True, id_=None) - Sends the IR signal with duration
11. send_ir_signal(self, host, ir_port_n, signal_data, repeats=None, duration=None) - Sends the IR signal
12. press_key(self, host, ir_port_n, keyset, key, repeats=None, duration=None) - Presses the key
13. get_ir_dataset_json(self) - Returns the IR dataset in JSON format
14. load_redrat_ir_dataset(self, xml_string_or_file) - Loads the redrat IR dataset
'''


class Dispatcher:
    def __init__(self):
        self.devices = []
        self._next_ir_id = 1

    def _get_host_port(self, host):
        """Return (host, port) from host string
            host="192.168.1.1" returns ("192.168.1.1", 4998)
            host="192.168.1.1:9999" returns ("192.168.1.1", 9999)
            """
        if ':' not in host:
            return host, 4998
        else:
            host, port = host.split(':')
            return host, int(port)

    def _get_ir_id(self):
        result = self._next_ir_id
        self._next_ir_id = (self._next_ir_id % 65535) + 1
        return result

    def dict_repr(self):
        return {
            'devices': [d.dict_repr() for d in self.devices]
        }

    async def health(self):
        return {
            'devices': [await d.health() for d in self.devices],
            'irdb': irdb_health()
        }

    async def clear_device_list(self):
        for device in self.devices:
            await device.teardown()
        self.devices.clear()

    def get_device(self, host):
        host, port = self._get_host_port(host)
        for device in self.devices:
            if device.host == host and device.port == port:
                return device
        return None

    async def add_device(self, host):
        if self.get_device(host):
            raise Exception(f"Device {host} already added")
        host, port = self._get_host_port(host)
        device = Device(host, port)
        try:
            await device.populate_info()
            await device.init_ir_device()
        except Exception as e:
            logger.warning(f"Connection errors for {device}: {e}")
        self.devices.append(device)
        return device

    async def _send_ir_signal_repeats(self, ir_port, signal_data, repeat=None, wait_for_response=True, id_=None):
        # Need to call ir_port.sendir(id_, freq, repeat, offset, durations):
        if id_ is None:
            id_ = self._get_ir_id()
        freq = signal_data['Frequency']
        if repeat is None:
            repeat = signal_data['DefaultRepeats']
        if repeat == 0:
            repeat = 1
            offset = 1
            durations = signal_data['BaseSequence']
            await ir_port._sendir(id_, freq, repeat, offset, durations, wait_for_response=wait_for_response)
        elif repeat <= ir_port.get_max_repeats():
            durations = signal_data['BaseSequence'] + signal_data['RepeatSequence']
            offset = len(signal_data['BaseSequence']) + 1
            await ir_port._sendir(id_, freq, repeat, offset, durations, wait_for_response=wait_for_response)
        else:
            seconds = (signal_data['BaseSequenceMicros'] + repeat * signal_data['RepeatSequenceMicros']) / 1_000_000
            await self._send_ir_signal_duration(ir_port, signal_data, seconds, check_max_repeats=False, id_=id_)

    async def _send_ir_signal_duration(self, ir_port, signal_data, seconds, check_max_repeats=True, id_=None):
        if id_ is None:
            id_ = self._get_ir_id()
        logger.debug(
            f"_send_ir_signal_duration -- BaseSequence seconds = {signal_data['BaseSequenceMicros'] / 1000000}")
        logger.debug(
            f"_send_ir_signal_duration -- RepeatSequence seconds = {signal_data['RepeatSequenceMicros'] / 1000000}")
        # Number of seconds if doing a press and hold for ir_port.get_max_repeats() repeats
        max_repeat_seconds = (signal_data['BaseSequenceMicros'] + ir_port.get_max_repeats() * signal_data[
            'RepeatSequenceMicros']) / 1_000_000
        assert (max_repeat_seconds > 0.5)  # just a sanity check
        now = time.time()
        end = now + seconds
        remaining = end - now
        # The -0.05 is so that I don't add an extra repeat if I'm only 5% into it in terms of seconds
        repeat = math.ceil(
            -0.05 + (1_000_000 * remaining - signal_data['BaseSequenceMicros']) / signal_data['RepeatSequenceMicros'])
        repeat = max(0, repeat)
        logger.debug(f"_send_ir_signal_duration -- repeat = {repeat}")
        logger.debug(
            f"_send_ir_signal_duration -- expected duration = {(signal_data['BaseSequenceMicros'] + signal_data['RepeatSequenceMicros'] * repeat) / 1000000}")
        if repeat < 0:
            return
        elif check_max_repeats and (repeat <= ir_port.get_max_repeats()):
            return await self._send_ir_signal_repeats(ir_port, signal_data, repeat, id_=id_)
        else:
            # From experimentation, I need to add about an extra two repeats in terms of duration. One makes repeating
            # a "ceiling" function (as in math.ceil line above). Using 1.9 instead of 2 to not overshoot.
            # The other repeat just seems to be necessary from a timing perspective
            end = end + 1.9 * signal_data['RepeatSequenceMicros'] / 1_000_000
            while True:  # keep sending max repeats every max_repeat_seconds/4, but make sure last request is sent in time
                # All sendir commands must be identical, including repeat count for continuous IR
                await self._send_ir_signal_repeats(ir_port, signal_data, ir_port.get_max_repeats(),
                                                   wait_for_response=False, id_=id_)
                if end - time.time() <= max_repeat_seconds + 2 * max_repeat_seconds / 4:
                    sleep_until = end - max_repeat_seconds  # Sleep until there are max repeats left to send
                    sleep_duration = sleep_until - time.time()
                    if sleep_duration > 0:
                        await asyncio.sleep(sleep_duration)
                    return await self._send_ir_signal_repeats(ir_port, signal_data, ir_port.get_max_repeats(),
                                                              wait_for_response=True, id_=id_)
                time.sleep(max_repeat_seconds / 4)
                now = time.time()

    async def send_ir_signal(self, host, ir_port_n, signal_data, repeats=None, duration=None):
        if (('BaseSequenceMicros' not in signal_data) or
                ('RepeatSequenceMicros' not in signal_data) or
                ('DefaultRepeats' not in signal_data)):
            signal_data = signal_data.copy()
            period_micros = 1_000_000 / signal_data['Frequency']
            signal_data['BaseSequenceMicros'] = round(sum(signal_data['BaseSequence']) * period_micros)
            signal_data['RepeatSequenceMicros'] = round(sum(signal_data['RepeatSequence']) * period_micros)
            if 'DefaultRepeats' not in signal_data:
                signal_data['DefaultRepeats'] = 1
        device = self.get_device(host)
        if not device:
            raise DeviceNotFound(host)
        ir_device = await device.get_ir_device()
        ir_port = ir_device.get_ir_port(ir_port_n)
        async with ir_port.lock:
            if repeats is None and duration is None:
                await self._send_ir_signal_repeats(ir_port, signal_data)
            elif repeats is not None and duration is not None:
                raise Exception("Repeats and duration both provided")
            elif repeats is not None:
                await self._send_ir_signal_repeats(ir_port, signal_data, repeats)
            elif duration is not None:
                await self._send_ir_signal_duration(ir_port, signal_data, seconds=duration / 1000)

    async def press_key(self, host, ir_port_n, keyset, key, repeats=None, duration=None):
        signal_data = get_signal_data(device_name=keyset, key_name=key)
        if signal_data:
            await self.send_ir_signal(host, ir_port_n, signal_data, repeats, duration)
            return True
        return False

    def get_ir_dataset_json(self):
        return ir_dataset_to_json()

    def load_redrat_ir_dataset(self, xml_string_or_file):
        redrat_ir_dataset_loader.load_dataset(xml_string_or_file)
