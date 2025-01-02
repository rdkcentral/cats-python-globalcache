# Source code structure

Most of the call flows go: `server.py` => `gcdispatch.py` => `gcdevice.py `

* `server.py`: This currently holds everything related to fastapi and
  reading configuration
* `gcdispatch.py`: The server.py APIs are mapped directly to an
  internal shim layer object called Dispatcher defined in gcdispatch.py.
  This can be an used interactively in a notebook environment for debug.
* `gcdevice.py`:  This abstracts the global cache devices and also the TCP
  socket connection to these devices (via asyncio).
  
There are also the following auxiliary source code files:
* `gcirdb.py`: This defines the internal IR database structure as a dict
  (schema/structure for this needs to be formalized). Importantly, this
  also includes the RedRatDatasetLoader class which allows reading redrat
  xml files into the internal IR DB   
* `exceptions.py`: Various exceptions

The data directory holds:
* `irdb-gc.json`: Not used currently, but this shows what the internal IR
  DB looks like
* `logging_config.yaml`: Config logging
* `REDRAT_KEYMANAGER.xml`: This can be loaded with the RedRatDatabaseLoader.load_default_dataset
  method
  
The localdev directory is for docker volume mount, to read in the `gc-dispatcher.config.yml`
file and `REDRAT_KEYMANAGER.xml`, as provided on racks

