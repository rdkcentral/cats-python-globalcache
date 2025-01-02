# python-globalcache
Library to interface with and dispatch commands to global cache devices (especially IR)

## TOC:
* [Auxiliary documentation](#auxiliary-documentation)
* [IR-MS based deployment](#ir-ms-based-deployment)
* [Debug/Test](#debugtest)

---

## Auxiliary documentation

* [Software architecture](docs/architecture.md) in /docs
* [Redrat keyset loader](docs/redrat_loader.md) in /docs
* Global-cache spec https://www.globalcache.com/files/docs/API-GC-UnifiedTCPv1.1.pdf

---

## High level overview

python-globalcache is a python package for communicating with global cache devices, specifically iTach IP2IR

The library implements the following methods/functionality:
* Create and maintain TCP connections to multiple global-cache devices, with health info. In the case of IP2IR devices, one TCP scoket is
  maintained per IR port + 1 socket for admin/device queries (8 total TCP connections are allowed)
* Send commands and receive responses
* Abstract remote control usage by mapping keysets / button names, and performing high-level operations like press and hold
* Parse/decode redrat keyset data and expose keysets/button names.
* FastAPI server to interact with devices

Currently, the primary use case is the gcdispatcher FastAPI service, which is called from IR-MS analogously
to how IR-MS interacts with redrathub.

---

## IR-MS based deployment

GCDispatcher was built expecting to primarily be called from IR-MS


### Docker config

For docker container deployment, please refer to settings in `python-globalcache/docker-compose.yml`, especially:
* Environment variable `GCDISPATCHER_ENV=rack`
  * Needed to set paths, URL root, etc for rack deployment as opposed to local development
* Volume mapping: `/irms:/irms`
  * Houses config files (see below)
* External network name `cats_remote`
  * The `cats_remote` network has to be used for ir-ms to be able to send HTTP requests to gcdispatcher 



The `/irms` directory contains the following files for gcdispatcher:
* `/irms/config/gc/gc-dispatcher-config.yml`
  * Thie file is created by IR-MS and defines device configuration (primarily hostname and port of global-cache devices)
  * [ ] TODO: gcdispatcher should query IR-MS at startup to make sure it's loaded before reading this file.
* `/irms/redrat/REDRAT_KEYMANAGER.xml`
  * This is the RedRat key dataset file, and must be copied separately to this location (e.g. via Ansible job)
  * This is currently copied from the IR-MS ansible job.
  * [ ] TODO: Should this be copied from the gcdispatcher ansible job?

Both files are loaded at service start. Restart the docker container to reload.

### IR-MS Config

Define the global-cache (itach devices) in the irDevices section of `/irms/ir-ms.yml` on the rack server
```yml
irDevices:
  - type: itach
    host: 192.168.100.35
    port: 4998
    maxPorts: 3
```

For sequential hosts, you can use the count attribute. For example, if 12 iTachs are present from
192.168.100.31 to 192.168.100.42:
```yml
irDevices:
  - type: itach
    count: 12
    host: 192.168.100.31
    port: 4998
    maxPorts: 3
```

Also be sure to update `/irms/ms/mappings.json`, e.g. if 6 A-side and 6 B-side devices are used:
```json
{
   "slots": {
      "1": "1:1",
      "2": "1:2",
      "3": "1:3",
      "4": "2:1",
      "5": "2:2",
      "6": "2:3",
      "7": "3:1",
      "8": "3:2",
      "9": "3:3",
      "10": "4:1",
      "11": "4:2",
      "12": "4:3",
      "13": "5:1",
      "14": "5:2",
      "15": "5:3",
      "16": "6:1",
      "17": "7:1",
      "18": "7:2",
      "19": "7:3",
      "20": "8:1",
      "21": "8:2",
      "22": "8:3",
      "23": "9:1",
      "24": "9:2",
      "25": "9:3",
      "26": "10:1",
      "27": "10:2",
      "28": "10:3",
      "29": "11:1",
      "30": "11:2",
      "31": "11:3",
      "32": "12:1"
   }
}
```

**IMPORTANT**: Upon startup, IR-MS will write the file `/irms/config/gc/gc-dispatcher-config.yml`. Please ensure
that this directory is present for IR-MS. Only after writing this file should gcdispatcher be run.

### nginx config

This is not normally necessary, but if remote access is needed - i.e., for debug and accessing swagger UI, nginx can be proxied like this:
```
cat /etc/nginx/conf.d/gcdispatcher.conf 
location ~ ^\/gcdispatcher\/(.*) {
   client_max_body_size 5M;	
   proxy_pass http://127.0.0.1:9710/$1$is_args$args;
}
```

Then the Swagger UI will be available at http://RACK_IP/gcdispatcher/

### Interfacing with APIs via curl
Try, e.g.:
```
curl http://localhost:9710/api/v1/health
```

See [API Docs](docs/generated/api.md) for more commands

---


# Development/Debug/Test

For development (at project root):
```bash
pip install -e .
```

Now you may run gcdispatcher service with:
```bash
python -m python_globalcache.server
```
to start the server. Go to http://localhost:9710/ for swagger API.

This must be run in either project root, or from the src/python_globalcache directory
(where server.py exists). It will use configuration files from the localdev/config folder.

You can debug in pycharm, set breakpoints, etc simply by right clicking src/python_globaclcache/server.py
and clicking `debug server`

---

### Build and run docker container with docker-compose:

Run the following to build and bring up the docker container for gcdispatcher
```
docker-compose build
docker-compose up
```
* Note: The docker-compose.yml file should be configured for local development.
  Please see comments in the file.
* Navigate to http://localhost:9710/docs
* Set server to / in FastAPI docs (Swagger UI) view
* Try the **health** and **list_devices** APIs

**For additional testing docs, see the /testing directory**






