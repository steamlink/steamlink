## SteamLink Notes

- repository structure
  steamlink/steamlink -> pypi package
  steamlink/steamlink-arduino	-> arduino library w/ samples

- to make a new release on pypi
	cd ~/git/steamlink
	vi steamlink/const.py   edit *_VERSION
	git commit -a -m "version 0.7.1"
	git push
	git tag 0.7.1 -m "0.7.1"	
	git push --tags origin master

	. ~/sl/bin/activate
	
	python3 setup.py sdist upload -r pypi

- to find all debug key tags run
	 grep in\ logging.DBGK steamlink/*.py | sed "s/.*if '\(.*\)' in logging.*/\1/" | sort -u
	
## TODOs
see TODO file

- HTTP route:
	- '/config'

- Security
	- Default:
		- run on localhost without password
- Configurable via GUI:
	- Working directory
	- MQTT broker selection
		- default: we'll fire up our own
			- customize local broker
		- options: configure an external broker



## Node States

These are the node states as implemented in steamlink.py

1. INITIAL

AKA UNKNOWN. This means the backend has not heard from the node since the backend started. All data from the node is retreived from the cache.

2. UP

We have received a sign on (op code `ON`) packet from the node. If a node keeps sending data the state stays at UP indefinitely. 

3. OVERDUE

A heartbeat process is run periodically to check which nodes are overdue. If data is missed up to the `max_silence` time as defined in the node configuration the state goes to `OVERDUE`. If a node is overdue to send data and the node is pingable (defined in the node configuration), the store sends a get status request (op code `GS`) 

4. <NODE STATUS> 

If a node sends a set status (op code `SS`) this status will be set. A node should be able to respond to a `GS` op code, and the steamlink client Arduino library defaults to an `SS` message of "OK" when it receives a `GS`.

Suggested reponses to `GS`:
	- "OFFLINE": If the node powers off
	- "SLEEPING": If the node enters low power mode
TODO: This is not implemented in the client libraries by default.

5. TRANSMITTING

The sign on packet for the node was not seen by the backend but the node appears to be transmitting normaly.


## MQTT Auth 

There are four different user types with different access rights to the MQTT broker:

- users:  owners of nodes, can read and write public data and control topics
- store:  can read/write everthing in the topic transport and public topic
- bridges: can write the transport data topic and read the transport control topic
- admins:  no restrictions

A sample aclfile for mosquitto, that implements the above rules for prefix 'SteamLink' and
public topic 'SteamLink/pub' is here:

```
# Users
user demo0
topic read SteamLink/pub/+/data
topic write SteamLink/pub/+/control
user demo1
topic read SteamLink/pub/+/data
topic write SteamLink/pub/+/control

# Stores
user store0
topic readwrite SteamLink/#
user store1
topic readwrite SteamLink/#

# Bridges
user demobridge0
topic write SteamLink/+/data
topic read SteamLink/+/control
user demobridge1
topic write SteamLink/+/data
topic read SteamLink/+/control


# Admins
user andreas
topic readwrite #
user udit
topic readwrite #

# This affects all clients.
pattern write $SYS/broker/connection/%c/state
```
	

## Websocket Interface
 
Starting and Modifying Streams:

```Web -> Store message:
    table_name: [Steam, Mesh, Node, Packet, Log]
    key_field: (ordered by this field)
    restrict_by:
        - list of dictionaries
        {
            field_name
            op: ["==", "!=", ">=", ">", "<=" "<"]
            value: 
        }
    start_key: (if start key is null use start_item_number)
    start_item_number:
    end_key: (if end key is null, use count)
    count:
	stream_tag:
```
```Store -> Web message:
    start_key:
    end_key:
    count:
    start_item_number:
    total_item_count:
```
Example query for mesh header tile

```table_name: Mesh
restrict_by: [
    {
        field_name: steam_id
        op: "=="
        value: 1
    }
]
start_key: null
end_key: null
count: 1
```

Example query for nodes table

```table_name: Node
restrict_by: [
    {
        field_name: mesh_id
        op: "=="
        value: ...
    }
]
start_key: null
end_key: null
count: 20
```

Alerts: publish on event 'alert'
```{ 
	'msg': 'message',
	'lvl':  just like unix
```}

