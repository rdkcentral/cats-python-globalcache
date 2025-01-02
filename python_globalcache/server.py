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
import logging
import logging.config
import os
import sys
from enum import Enum
from typing import List, Optional

import yaml
from fastapi import FastAPI, Query, Response, File
from fastapi.openapi import docs as fastapi_docs
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from starlette.middleware import Middleware
from starlette_context import plugins, context
from starlette_context.middleware import RawContextMiddleware
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)
logger.setLevel("DEBUG")


class DeploymentEnv(Enum):
    python_command = 'python_command'  # This was run as python command (default) on local dev machine
    # => note: python command is... python -m python_globalcache.server
    localdev = 'localdev'  # For testing as docker container on local dev machine
    rack = 'rack'  # For rack deployment


class Settings(BaseSettings):
    gcdispatcher_env: DeploymentEnv = 'python_command'

    class Config:
        use_enum_values = True


settings = Settings()

if settings.gcdispatcher_env == 'python_command':
    if __name__ != "__main__":
        raise Exception("Must set GCDISPATCHER_ENV if running as docker app")
    if not os.path.exists('localdev/config'):
        if os.path.isfile('server.py'):
            os.chdir('../..')
        if not os.path.exists('localdev/config'):
            raise Exception("Can't find localdev/config... Please run in base of python-globalcache project\n" +
                            "or in the python_globalcache source directory, for debug")


class EnvSettings(BaseModel):
    url_root_path: str
    gcdispatcher_config_path: str
    redrat_xml_path: str


env_settings_all = {
    "python_command": {
        "url_root_path": "",
        "gcdispatcher_config_path": "localdev/config/gc-dispatcher-config.yml",
        "redrat_xml_path": "localdev/config/REDRAT_KEYMANAGER.xml"
    },
    "localdev": {
        "url_root_path": "/gcdispatcher",
        "gcdispatcher_config_path": "localdev/config/gc-dispatcher-config.yml",
        "redrat_xml_path": "localdev/config/REDRAT_KEYMANAGER.xml"
    },
    "rack": {
        "url_root_path": "/gcdispatcher",
        "gcdispatcher_config_path": "/irms/config/gc/gc-dispatcher-config.yml",
        "redrat_xml_path": "/irms/redrat/REDRAT_KEYMANAGER.xml"
    }
}

env_settings = EnvSettings.parse_obj(env_settings_all[settings.gcdispatcher_env])
print(f"Deployment Environment = {settings.gcdispatcher_env}")
print(f"Environment Settings = {env_settings}")

server1 = {"url": "/"}
server2 = {"url": "/gcdispatcher", "description": "nginx proxy"}

if env_settings.url_root_path == "":
    servers = server1, server2
else:
    servers = server2, server1

middleware = [
    Middleware(
        RawContextMiddleware,
        plugins=(
            plugins.RequestIdPlugin(),
            plugins.CorrelationIdPlugin()
        )
    )
]

description = """
### GCDispatcher: A hub for global-cache devices

### Device APIs

There are APIs to add, list, and clear global-cache devices from the internal device list.

All that's needed to add a device is IP address and port provided as <IP>[:Port], with port defaulting to 4998

Upon addition, GCDispatcher will connect to the device to determine number of ports and to create connections on each

For persisted devices, please use the evironment settings gcdispatcher_config_path yml file

### RedRat IR Dataset

There is an API to take a file-upload of a RedRat IR dataset and use it for key-codes. Note that this
dataset is not persisted between service restarts.

For persisted redrat datasets, please use the evironment settings redrat_xml_path file

### Health

The health API provides information about global-cache devices in the device list and the redrat dataset

### Response format

Currently the response format is:

- {"result": **result_data**} for valid responses
- {"error": **error_data**} for errors

### Rack URL prefix

On rack, these APIs can be accessed under the /gcdispatcher path prefix, if proxied via nginx (primarily
for testing)

NOTE: Normally gcdispatcher runs in a docker container exposing port 9710, so these APIs can be accessed
via e.g.

```curl http://localhost:9710/api/v1/health```
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    await update_logging()
    await configure()
    yield


app = FastAPI(
    servers=servers,
    root_path=env_settings.url_root_path,
    docs_url=None,
    title="GCDispatcher",
    description=description,
    version="0.1",
    middleware=middleware,
    lifespan=lifespan
)


def custom_get_swagger_ui_html(*args, **kwargs):
    # Syntax highlighting was causing some performance issues
    html = fastapi_docs.get_swagger_ui_html(*args, **kwargs)
    logger.info("Getting swagger ui html")
    html = html.body.decode().replace("dom_id: '#swagger-ui'", "syntaxHighlight: false,\n   dom_id: '#swagger-ui'")
    return HTMLResponse(html)


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    logger.info("OpenAPI URL is {}".format(app.openapi_url))
    return custom_get_swagger_ui_html(
        openapi_url=env_settings.url_root_path + app.openapi_url,
        title=app.title + " - Swagger UI"
    )


SUCCESS = {"result": "success"}
BAD_KEY = {"result": "Error", "Reason": "Key not supported"}


def do_update_logging():
    logging.getLogger('multipart.multipart').setLevel(logging.WARNING)
    with importlib.resources.files("python_globalcache.data").joinpath("logging_config.yaml").open('r') as file:
        log_config = yaml.load(file, Loader=yaml.FullLoader)
        logging.config.dictConfig(log_config)


async def update_logging():
    do_update_logging()
    logger.info("Updated logging 2")


do_update_logging()  # do both immediately and on fastapi startup
logger.info("Updated logging 1")


async def configure():
    null_constructor = lambda loader, node: loader.construct_mapping(node)
    yaml.SafeLoader.add_constructor('!IRMS-Autogenerated-Config', null_constructor)
    if os.path.exists(env_settings.gcdispatcher_config_path):
        with open(env_settings.gcdispatcher_config_path) as f:
            config = yaml.safe_load(f)
            print(config)
            for device in config['irDevices']:
                if device.get('count', 1) != 1:
                    raise ValueError("Error count should be 1 in gcdispatcher config devices")
                if device.get('type', 'itach').lower() != 'itach':
                    raise ValueError("Error type should be itach in gcdispatcher config devices")
                host = device['host']
                port = device.get('port', '')
                hostport = device['host'] if not port else f"{host}:{port}"
                try:
                    await dispatcher.add_device(hostport)
                except Exception as e:
                    logger.exception(f"Can't add {hostport}")
    if os.path.exists(env_settings.redrat_xml_path):
        logger.info(f"Loading redrat xml: {env_settings.redrat_xml_path!r}")
        with open(env_settings.redrat_xml_path) as f:
            try:
                dispatcher.load_redrat_ir_dataset(f)
            except Exception as e:
                logger.exception(f"Exception in load_redrat_ir_dataset")
    else:
        logger.warning(f"Not loading redrat xml: {env_settings.redrat_xml_path!r}")


from python_globalcache.gcdispatch import Dispatcher

dispatcher = Dispatcher()


@app.post("/api/v1/add_device", tags=["GC Dispatcher"], summary="Add a global-cache device")
async def add_device(
        host: str = Query(...,
                          description="Either **host** or **host:port**. Default port is 4998",
                          examples="192.168.100.35:4998")
):
    """
    Add a global-cache device
    """
    device = await dispatcher.add_device(host)
    return {"result": device.dict_repr()}


@app.get("/api/v1/list_devices", tags=["GC Dispatcher"], summary="List global-cache devices")
async def list_devices():
    return {"result": dispatcher.dict_repr()}


@app.post("/api/v1/clear_device_list", tags=["GC Dispatcher"], summary="Clear global-cache device list")
async def clear_device_list():
    await dispatcher.clear_device_list()
    return SUCCESS


@app.post("/api/v1/press_key", tags=["GC Dispatcher"], summary="Press key (send IR signal)")
async def press_key(
        host: str = Query(...,
                          description="Either **host** or **host:port**. Default port is 4998",
                          examples="192.168.100.35:4998"),
        ir_port_number: int = Query(...,
                                    description="Port number of global-cache device, starting at 1",
                                    examples=3),
        keyset: str = Query(...,
                            description="Keyset to use",
                            examples="PC_REMOTE"),
        key: str = Query(...,
                         description="Key string code to use",
                         examples="VOLDN"),
        repeats: Optional[int] = Query(None,
                                       description="Number of repeats to use; only specify zero or one of repeats | duration",
                                       examples=40,
                                       ge=0, le=50_000),
        duration: Optional[int] = Query(None,
                                        description="Hold key duration in seconds; only specify zero or one of repeats | duration",
                                        examples=5,
                                        ge=0, le=300)):
    is_success = await dispatcher.press_key(host, ir_port_number, keyset, key, repeats, duration)
    print(f"is_success:{is_success}")
    if is_success:
        response = JSONResponse(SUCCESS)
        response.headers["HW-Command-Request-Time"] = context.data["HW-Command-Request-Time"]
        response.headers["HW-Command-Response-Time"] = context.data["HW-Command-Response-Time"]
        response.headers["HW-Command-Duration-Ms"] = context.data["HW-Command-Duration-Ms"]
    else:
        response = JSONResponse(BAD_KEY)
    return response


@app.get("/api/v1/health", tags=["GC Dispatcher"], summary="Get health info")
async def health():
    return {"result": await dispatcher.health()}


@app.post("/api/v1/load_redrat_ir_dataset", tags=["GC Dispatcher"], summary="Load redrat XML file")
async def load_redrat_ir_dataset(file: bytes = File(...)):
    # Do something with data
    try:
        return {"result": dispatcher.load_redrat_ir_dataset(file)}
    except Exception as e:
        logger.exception("Exception in load_redrat_ir_dataset")
        return {"error": {"code": repr(e)}}


class SignalData(BaseModel):
    Frequency: float
    BaseSequence: List[int]
    RepeatSequence: List[int]


@app.post("/api/v1/admin/send_signal", tags=["admin"], summary="ADMIN: send IR signal")
async def send_signal(host: str, ir_port_number: int,
                      repeats: int = Query(None, ge=0, le=1_000),
                      duration: int = Query(None, ge=0, le=60_000),
                      signal_data: SignalData = ...):
    logger.info(f'send_signal: {host}-{ir_port_number}')
    logger.info(f'send_signal: {signal_data}')
    await dispatcher.send_ir_signal(host, ir_port_number, signal_data.dict(), repeats, duration)
    return SUCCESS


@app.get("/api/v1/admin/get_ir_dataset_json", tags=["admin"], summary="ADMIN: get IR dataset as JSON file")
async def get_ir_dataset_json():
    # Do something with data
    result = dispatcher.get_ir_dataset_json()
    return Response(content=result, media_type="application/json")


@app.exception_handler(Exception)
async def default_exception_handler(request, err):
    # base_error_message = f"Failed to execute: {request.method}: {request.url}"
    logger.exception(err)
    # Change here to LOGGER
    return JSONResponse(status_code=400, content={"error": {"code": "UnhandledException", "detail": repr(err)}})


@app.get("/", tags=["GC Dispatcher"])
async def docs_redirect():
    return RedirectResponse(url=env_settings.url_root_path + '/docs')


if __name__ == "__main__":
    import uvicorn

    args = {}
    if '--reload' in sys.argv:
        args['reload'] = True
        uvicorn.run('python_globalcache.server:app', host="0.0.0.0", port=9710, **args)
    else:
        uvicorn.run(app, host="0.0.0.0", port=9710, **args)
