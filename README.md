## SteamLink


### Installation

SteamLink is best installed in a Python virtual environment. 
It requires python version 3.6 or better, and the python pip module.


1. Create en environment (for example 'sl') for SteamLink	```$ python3 -m venv sl```
1. Activate the new environment ```$ . sl/bin/activate``` 
1. Install (or upgrade) SteamLink from pypi ```$ pip3 install --upgrade steamlink```

If you installed SteamLink for the first you can create a default configuration file with the command

```$ steamlink -createconfig```

to create `steamlink.yaml`. Use the `-c <filename>` option to specify different name.

After editing the config you can start steamlink with

```$ steamlink```. 

Use the `-h` flag for help on available command options.



