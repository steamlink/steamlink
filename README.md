## SteamLink


### Installation

SteamLink is best installed in a Python virtual environment. 
It requires python version 3.6 or better, and the python pip module.


1. Create en environment (for example 'sl') for SteamLink	```$ python3 -m venv sl```
1. Activate the new environment ```$ . sl/bin/activate``` 
1. Install (or upgrade) SteamLink from pypi ```$ pip3 install --upgrade steamlink```

Steamlink will run with configuration defaults, if you don't specify a config file. You can create a default config file with the command

```$ steamlink --createconfig ```

It will create `steamlink.yaml` in your home directory. You can use the `-c <filename>` option to specify different name and/or location, both for creating and for running steamlink. Use the `-h` flag for help on available command options.

After editing the config you can start steamlink with

```$ steamlink```. 


### Configuration
The default configuration file is `steamlink.yaml` in the user's home directory. Override with the -c option.

#### General
- `mqtt_broker` - null for external broker or section name n this config file for the internal broker definition. Default `mqtt_broker`. See <B>MQTT Broker</B> below.

- `ping_timeout` - websocket keep-alive timeout


#### Steam
- `id` - id of the top level entry, default 0
- `name` - 
- `description` - 
- `namespace` -	usually /sl


#### tests

Named sub-sections and paramaters for simple package injections tests. See -T command line option


#### Console

The `console` section defines the built-in web console. 

- `host`, `port` - http server 
- `shutdown_timeout` - wait time before shutdown if web clients are connectd
- `namspace` - usually `/sl`, should match `namespace` in the`[general]` section
- `prefix` - 
- `minupdateinterval` - Number of seconds between item updates
- `index` - full path to the root web page
- `ssl_certificate` - tbd.
- `ssl_key` - tbd.

#### MQTT 

The MQTT section defines the MQTT client cconnection.

- `clientid` - 
- `username` -
- `password` -
- `server` -
- `port` -
- `ssl_certificate` -

- `prefix` -	MQTT topic prefix
- `data` - MQTT suffix for data messages
- `control` -	MQTT suffix for control messages

#### MQTT Broker

Steamlink uses an MQTT broker for internal processing and for delivery of data traffic from and to network nodes. A built-in MQTT broker is used by default, the `mqtt_broker` entry in the `[general]` section will point to the configuration section for the internal broker. If you want to use an external MQTT broker, set `mqtt_broker` to blank. The client connection pararamters to your broker are define in the `[mqtt]` section.


