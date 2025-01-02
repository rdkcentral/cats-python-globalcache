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


import importlib.resources
import json
from bs4 import BeautifulSoup
from _ctypes import PyObj_FromPtr
import json
import re
import base64
from itertools import zip_longest
import copy
import logging

logger = logging.getLogger(__name__)
logger.setLevel("DEBUG")


def grouper(iterable, n, fillvalue=None):
    "Collect data into fixed-length chunks or blocks"
    # grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx"
    args = [iter(iterable)] * n
    return zip_longest(*args, fillvalue=fillvalue)


class NoIndent(object):
    """ Value wrapper. """

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return str(self.value)

    def __repr__(self):
        return self.__str__()


class NoIndentEncoder(json.JSONEncoder):
    FORMAT_SPEC = '@@{}@@'
    regex = re.compile(FORMAT_SPEC.format(r'(\d+)'))

    def __init__(self, **kwargs):
        # Save keyword argument values required for encoding use.
        self.__sort_keys = kwargs.get('sort_keys', None)
        self.no_indent_objs = {}
        super().__init__(**kwargs)

    def default(self, obj):
        # if is instance of NoIndent object, return the format spec with the id
        if isinstance(obj, NoIndent):
            id = id(obj)
            self.no_indent_objs[id] = obj
            return self.FORMAT_SPEC.format(id)
        return super().default(obj)

    def encode(self, obj):
        format_spec = self.FORMAT_SPEC
        default_json = super().encode(obj)

        # Replace object ids in the default JSON with the
        # value returned from the json.dumps() of the corresponding
        # wrapped Python object.
        for match in self.regex.finditer(default_json):
            idx = int(match.group(1))
            no_indent_obj = self.no_indent_objs[idx]
            no_indent_obj_json = json.dumps(no_indent_obj.value, sort_keys=self.__sort_keys)

            # Replace matching id string with json
            # representation for the object given that id.
            id_spec = format_spec.format(idx)
            json_repr = json_repr.replace('"{}"'.format(id_spec), no_indent_obj_json)
        return json_repr


ir_devices = {}


def import_data(data):
    ir_devices.clear()
    for device in data:
        device_keys = {}
        for device_key in device['DeviceKeys']:
            device_keys[device_key['Name']] = device_key
            if 'RRNoRepeats' in device_key:
                logger.warn("Updating RRNoRepeats => DefaultRepeats")
                device_key['DefaultRepeats'] = device_key['RRNoRepeats']
                del device_key['RRNoRepeats']
        ir_devices[device['DeviceName']] = device
        device['DeviceKeys'] = device_keys


def irdb_health():
    result = {}
    result['dataset_loaded'] = bool(ir_devices)
    result['ir_devices'] = list(ir_devices.keys())
    return result


def get_signal_data(device_name, key_name):
    ir_key = None
    if device_name and key_name and device_name in ir_devices:
        device = ir_devices[device_name]
        if device and 'DeviceKeys' in device:
            device_keys = device['DeviceKeys']
            if device_keys and key_name in device_keys:
                ir_key = device_keys[key_name]
    return ir_key


def ir_dataset_to_json():
    obj = copy.deepcopy(ir_devices)
    todump = []
    for device_name, device in obj.items():
        device_keys = []
        for device_key_name, device_key in device['DeviceKeys'].items():
            device_key['BaseSequence'] = NoIndent(device_key['BaseSequence'])
            device_key['RepeatSequence'] = NoIndent(device_key['RepeatSequence'])
            device_keys.append(device_key)
        device['DeviceKeys'] = device_keys
        todump.append(device)
    return json.dumps(todump, cls=NoIndentEncoder, indent=2)


'''
RedRatDatasetLoader class is used to load the RedRat IR database from the XML file.
'''


class RedRatDatasetLoader:
    MIN_INTRA_SIG_PAUSE_CYLCES = 10

    def __init__(self):
        self.devices = []

    def _clean_sequence(self, seq):
        result = []
        for pulse, space in grouper(seq, 2):
            pulse_remainder = pulse - round(pulse)
            result.append(round(pulse))
            if space is not None:
                result.append(round(space + pulse_remainder))
        return result

    def load_default_dataset(self):
        logger.warning("Loading default IR DB")
        with importlib.resources.open_text("python_globalcache.data", "REDRAT_KEYMANAGER.xml") as file:
            self.load_dataset(file)

    def load_dataset(self, xml_string_or_file):
        soup = BeautifulSoup(xml_string_or_file, 'xml')
        self.devices.clear()
        for avdevice in soup.AVDeviceDB.AVDevices.find_all('AVDevice'):
            device = {'Device' + tag.name: tag.text for tag in avdevice.find_all(recursive=False) if not tag.find()}
            device = {'DeviceName': device['DeviceName']}  # only saving the device name
            self.devices.append(device)
            length_set = []
            keys = []
            for key in avdevice.Signals.find_all('IRPacket'):
                if key['xsi:type'] == 'DoubleSignal':
                    print("TODO: DoubleSignal")
                    continue
                if not key.Lengths:
                    continue
                key_data = device.copy()
                key_data.update({tag.name: tag.text for tag in key.find_all(recursive=False) if not tag.find()})
                lengths = [float(it.text) for it in key.Lengths.find_all('double')]
                base_time, repeat_time = base64.b64decode(key.SigData.text).split(b'\x7f')[:2]
                base_time = [lengths[s] for s in base_time]
                repeat_time = [lengths[s] for s in repeat_time]
                freq = round(float(key_data['ModulationFreq']))
                base_sequence = [v * freq / 1000 for v in base_time]  # in number of cycles at freq
                repeat_sequence = [v * freq / 1000 for v in repeat_time]  # in number of cycles at freq
                base_sequence = self._clean_sequence(base_sequence)
                repeat_sequence = self._clean_sequence(repeat_sequence)
                intra_sig_pause = round(float(key_data['IntraSigPause']) * freq / 1000)
                if intra_sig_pause < self.MIN_INTRA_SIG_PAUSE_CYLCES:
                    intra_sig_pause = self.MIN_INTRA_SIG_PAUSE_CYLCES
                base_sequence.append(intra_sig_pause)
                if repeat_sequence:
                    repeat_sequence.append(intra_sig_pause)
                assert (len(base_sequence) % 2 == 0)
                assert (len(repeat_sequence) % 2 == 0)
                base_sequence_duration = sum(it / freq * 1000000 for it in base_sequence)
                repeat_sequence_duration = sum(it / freq * 1000000 for it in repeat_sequence)
                key_data['Frequency'] = freq
                key_data['DefaultRepeats'] = int(key_data['NoRepeats'])
                key_data['BaseSequence'] = base_sequence
                key_data['RepeatSequence'] = repeat_sequence
                key_data['BaseSequenceMicros'] = round(base_sequence_duration)
                key_data['RepeatSequenceMicros'] = round(repeat_sequence_duration)
                saved_attributes = ['Name', 'Frequency', 'BaseSequence', 'RepeatSequence', 'DefaultRepeats',
                                    'BaseSequenceMicros', 'RepeatSequenceMicros']
                keys.append({k: key_data[k] for k in saved_attributes})
            device['DeviceKeys'] = keys
        import_data(self.devices)


redrat_ir_dataset_loader = RedRatDatasetLoader()

# with importlib.resources.open_text("python_globalcache", "irdb-gc.json") as file:
#     data = json.load(file)
# import_data(data)
