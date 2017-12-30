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

#### MQTT Broker

Steamlink uses an MQTT broker for internal processing and for delivery of data traffic from and to network nodes. A built-in MQTT broker is used by default, the `mqtt_broker` entry in the `general` section will point to the configuration section for the internal broker. If you want to use an external MQTT broker, set `mqtt_broker` to `None` and set the connection pararamters for your broker in the `mqtt` section:
```yaml
	mqtt:
	    clientid: "sl_client"
	    username: "USER"
	    password: "PASSWORD"
	    server: "SERVER.NAME.OR.IO"
	    port: 1883
	    ssl_certificate: "/PATH/TO/CERTIFICATE.PEM"
```


