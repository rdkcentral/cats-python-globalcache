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



import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from enum import Enum, auto
from typing import List

from starlette_context import context

from python_globalcache.exceptions import *

logger = logging.getLogger(__name__)
logger.setLevel("DEBUG")


class DevicePortType(Enum):
    IR = auto()
    ETHERNET = auto()

@dataclass
class DeviceModuleDescriptor:
    module: int
    ports: int
    type: DevicePortType


class Device:
    """A generic Global Cache Device. Any IR specific behavior is handled in the IRDevice, which exposes IRPort instances
     with methods for IR commands. The IRDevice, if available, may be accessed from self._ir_device"""
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.connections = []
        self.version = None
        self.module_descriptors = []
        self._ir_device = None

    def __repr__(self):
        return f"Device({self.host}, {self.port})"

    async def teardown(self):
        for connection in self.connections:
            if not connection.closed:
                await connection.close()

    def dict_repr(self):
        result = {}
        result['host'] = self.host
        result['port'] = self.port
        result['active_connections'] = len(self.connections)
        result['version'] = self.version
        result['modules'] = []
        for m in self.module_descriptors:
            desc = {}
            desc['module'] = m.module
            desc['ports'] = m.ports
            desc['type'] = m.type.name
            result['modules'].append(desc)
        return result

    async def health(self):
        health = {}
        try:
            await self.populate_info()
        except Exception as e:
            logger.exception("Health error for device {}".format((self.host, self.port)))
            health['available'] = False
            health['errors'] = [repr(e)]
        else:
            health['available'] = True
            health['errors'] = []
        result = self.dict_repr()
        result['health'] = health
        return result

    async def init_ir_device(self):
        if not self._ir_device:
            self._ir_device = await IRDevice.create(self)

    async def get_ir_device(self):
        if not self._ir_device:
            await self.init_ir_device()
        return self._ir_device

    async def connect(self):
        self.connections.append(await StreamConnection.create(self))
        return self.connections[-1]

    async def populate_info(self):
        connection = await self.connect()
        await asyncio.sleep(0.5) # make sure connection is established and reader started...
        self.version = await connection.getversion()
        self.module_descriptors = await connection.getdevices()
        await connection.close()
        self.connections.remove(connection)



class StreamConnection:
    """A telnet connection to a Global Cache device. Note, for IR this is implemented as synchronous request/responses
    I.e., must wait for a response before sending a new request. This should be asynchronous messaging if we want one
    connection to send IR simultaneously to multiple ports. E.g., the GC100 only allows one TCP connection, so this
    would need to be implemented with asynchronous IR messaging for that device. The iTach, however, allows multiple
    TCP connections, so we can use one TCP connection per IR port to simplify things.
    (asyncio is still needed regardless, as it handles concurrency across multiple separate connections)
    """
    current_index = 0

    # The initial delay between retries in seconds
    _RECONNECT_TIME_START = 1
    # Multiply the delay by this amount each time a reconnect fails
    _RECONNECT_TIME_BACKOFF = 2
    # The maximum delay in seconds for reconnects
    _RECONNECT_TIME_MAX = 30

    def __init__(self, device, async_rw_pair):
        self.index = self.get_index()
        self.closed = False
        self.device = device
        self.line_buffer = asyncio.Queue(1000)
        self.reader, self.writer = async_rw_pair
        self._reconnect_delay = self._RECONNECT_TIME_START

    @classmethod
    def get_index(cls):
        result = cls.current_index
        cls.current_index+=1
        return result

    @classmethod
    async def create(cls, device):
        try:
            async_rw_pair = await asyncio.wait_for(asyncio.open_connection(device.host, device.port), 3)
        except Exception as e:
            raise DeviceUnavailable(f'{e!r}')
        self = cls(device, async_rw_pair)
        logger.info(f'Created connection {self}')
        self.reader_task = asyncio.get_event_loop().create_task(self.reader_worker())
        return self

    def __repr__(self):
        return f'StreamConnection({self.device}, index={self.index})'

    def get_line_buffer_joined(self):
        result = []
        while True:
            try:
                result.append(self.line_buffer.get_nowait())
            except asyncio.QueueEmpty:
                break
        return '\n'.join(result)

    def clear_line_buffer(self):
        while True:
            try:
                self.line_buffer.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def write_line(self, string):
        if self.closed:
            raise ConnectionClosed(f'{self}')
        byts = string.encode() + b'\r'
        logger.debug(f"{self}: Write {byts!r}")
        self.writer.write(byts)
        await self.writer.drain()

    async def wait_for_line(self, timeout=30):
        return await asyncio.wait_for(self.line_buffer.get(), timeout=timeout)

    async def reader_worker(self):
        while True:
            try:
                try:
                    line = await self.reader.readuntil(b'\r')
                    logger.debug(f"{self}: Read {line!r}")
                    # Read succeeded, reset retry backoff to initial value
                    self._reconnect_delay = self._RECONNECT_TIME_START
                except asyncio.IncompleteReadError as e:
                    if self.closed:
                        return
                    logger.warning(f"{self}: IncompleteReadError but not closed... {e!r}")
                    await self.reconnect_with_backoff()
                    continue
                except ConnectionResetError as e:
                    logger.warning(f"{self}: Connection reset {e!r}")
                    await self.reconnect_with_backoff()
                    continue
                except Exception as e:
                    logger.warning(f"{self}: I got this exception: {e!r}")
                    raise
                line = line.decode(errors='replace').rstrip()
                if not line and self.reader.at_eof():
                    logger.warning(f'EOF for {self}')
                    await self.reconnect_with_backoff()
                if line:
                    self.line_buffer.put_nowait(line)
            except Exception as e:
                logger.exception(e)
                await asyncio.shield(self.close())
                raise

    async def reconnect_with_backoff(self):
        logger.warning(f"{self}: Will try to reconnect in {self._reconnect_delay}s")
        await asyncio.sleep(self._reconnect_delay)
        await self.reconnect()
        # Progressively increase the retry delay up to the maximum
        reconnect_delay = self._reconnect_delay * self._RECONNECT_TIME_BACKOFF
        self._reconnect_delay = min(reconnect_delay, self._RECONNECT_TIME_MAX)

    async def reconnect(self):
        self.writer.close()
        logger.debug(f"{self}: Attempting reconnect")
        try:
            async_rw_pair = await asyncio.wait_for(asyncio.open_connection(self.device.host, self.device.port), 3)
            self.reader, self.writer = async_rw_pair
            logger.debug("Reconnect successful")
            return
        except Exception as e:
            logger.debug(f"{self}: Can't reconnect due to {e!r}")
        logger.debug(f"{self}: Reconnect failed... Closing connection")
        await asyncio.shield(self.close())


    async def close(self):
        self.closed = True
        self.reader_task.cancel()
        logger.debug(f"{self}: Canceled")
        await asyncio.sleep(1)
        self.writer.close()

    async def getversion(self):
        self.clear_line_buffer()
        await self.write_line('getversion')
        line = await self.wait_for_line()
        if line.startswith('ERR') or line.startswith('unknown'):
            self.clear_line_buffer()
            raise Exception(line)
        else:
            return line

    async def getdevices(self) -> List[DeviceModuleDescriptor]:
        """example from API spec for iTach IP2IR
            getdevices
            device,0,0 ETHERNET
            device,1,3 IR
            endlistdevices
        """
        result = []

        self.clear_line_buffer()
        await self.write_line('getdevices')

        while True:
            line = await self.wait_for_line()
            if line.startswith('ERR') or line.startswith('unknown'):
                self.clear_line_buffer()
                raise Exception(line)
            elif line.startswith('device'):
                _, module, ports, port_type = line.replace(',', ' ').split()
                module, ports = int(module), int(ports)
                port_type = DevicePortType[port_type]
                result.append(DeviceModuleDescriptor(module=module, ports=ports, type=port_type))
            elif line.startswith('endlistdevices'):
                return result
            else:
                data = line + '\n' + self.get_line_buffer_joined()
                raise Exception(f"Unexpected response: {data!r}")

    async def sendir(self, module, port, id_, freq, repeat, offset, durations, wait_for_response=True):
        module = int(module)
        port = int(port)
        id_ = int(id_)
        freq = int(freq)
        repeat = int(repeat)
        offset = int(offset)
        durations = [int(duration) for duration in durations]
        durations = ','.join(map(str, durations))
        command = f'sendir,{module}:{port},{id_},{freq},{repeat},{offset},{durations}'
        logger.debug(f"Writing command: {command!r}")
        # self.telnet.read_very_eager()
        # self.telnet.write(command)
        start = datetime.now(timezone.utc)

        self.clear_line_buffer()
        await self.write_line(command)
        if wait_for_response:
            logger.debug("TODO: adjustme timeout=60")
            line = await self.wait_for_line()
            if line.startswith('ERR') or line.startswith('unknown'):
                self.clear_line_buffer()
                raise Exception(line)
            elif line.startswith('completeir'):
                _, rmodule, rport, rid  = line.replace(',', ' ').replace(':', ' ').split()
                rmodule, rport, rid = int(rmodule), int(rport), int(rid)
                if (rmodule, rport, rid) != (module, port, id_):
                    raise Exception(f"Unexpected response {line!r} for module, port, id = {(module, port, id_)}")
                else:
                    end = datetime.now(timezone.utc)
                    context.data["HW-Command-Request-Time"] = start.isoformat()
                    context.data["HW-Command-Response-Time"] = end.isoformat()
                    delta = end - start
                    context.data["HW-Command-Duration-Ms"] = str(math.ceil(delta.total_seconds() * 1000))
                    return # Success!
            else:
                data = line + '\n' + self.get_line_buffer_joined()
                logger.warn(f"Unexpected response: {data!r}... waiting")
        else:
            end = datetime.now(timezone.utc)
            context.data["HW-Command-Request-Time"] = start.isoformat()
            context.data["HW-Command-Response-Time"] = end.isoformat()
            delta = end - start
            context.data["HW-Command-Duration-Ms"] = str(math.ceil(delta.total_seconds() * 1000))
            return

    async def stopir(self, module, port):
        module = int(module)
        port = int(port)
        command = f'stopir,{module}:{port}'
        self.clear_line_buffer()
        await self.write_line(command)
        line = await self.wait_for_line()
        if line.startswith('ERR') or line.startswith('unknown'):
            self.clear_line_buffer()
            raise Exception(line)
        elif line.startswith('stopir'):
            _, rmodule, rport = line.replace(',', ' ').replace(':', ' ').split()
            rmodule, rport = int(rmodule), int(rport)
            if (rmodule, rport) != (module, port):
                raise Exception(f"Unexpected response {line!r} for module, port = {(module, port)}")
            else:
                return # Success!
        else:
            data = line + '\n' + self.get_line_buffer_joined()
            raise Exception(f"Unexpected response: {data}")


class IRDevice:
    """An IRDevice wraps a Global Cache Device and provides IRPorts"""
    def __init__(self, device, called_from_create=False):
        """This should be called from IRDevice.create"""
        self.device = device # The primary global cache device
        if not called_from_create:
            raise Exception("Error: This needs to be called from the create method")

    @classmethod
    async def create(cls, device):
        self = IRDevice(device, called_from_create=True)
        portmap = self.portmap = {}
        if not device.module_descriptors:
            await device.populate_info()
        for module_descriptor in device.module_descriptors:
            if module_descriptor.type == DevicePortType.IR:
                module, ports = module_descriptor.module, module_descriptor.ports
                for port in range(1, ports+1):
                    logger.info(f"Creating connection for {device}")
                    ir_port = IRPort(self, module, port, await device.connect())
                    portmap[(module, port)] = ir_port
        self.module_ports = sorted(portmap)
        return self

    def get_ir_port(self, port_n): # Get 1-indexed IRPort
        assert port_n > 0
        module, port = self.module_ports[port_n - 1]
        return self.portmap[(module, port)]

    def get_max_repeats(self):
        # TODO: This value is accurate only for iTach.. See the sendir documentation
        # GC-100 max repeates = 31, iTach = 50, Flex = 20, Global Connect = 20
        # We can determine model from version info
        return 50


class IRPort:
    """A single IR port on an IRDevice with its own connection"""
    def __init__(self, ir_device, module, port, connection):
        self.ir_device = ir_device
        self.module = module
        self.port = port
        self.connection = connection
        self.lock = asyncio.Lock() # Need this to be an RLock because dispatcher locks this too for continuous repeats

    async def _sendir(self, id_, freq, repeat, offset, durations, wait_for_response=True):
        return await self.connection.sendir(self.module, self.port, id_, freq, repeat, offset, durations, wait_for_response)

    async def sendir(self, id_, freq, repeat, offset, durations, wait_for_response=True):
        async with self.lock:
            return await self._sendir(id_, freq, repeat, offset, durations, wait_for_response)

    async def stopir(self):
        async with self.lock:
            return await self.connection.stopir(self.module, self.port)

    def get_max_repeats(self):
        return self.ir_device.get_max_repeats()
