# coding: utf-8
"""Constants used by Home Assistant components."""
MAJOR_VERSION = 0
MINOR_VERSION = 8
PATCH_VERSION = '0'
__short_version__ = '{}.{}'.format(MAJOR_VERSION, MINOR_VERSION)
__version__ = '{}.{}'.format(__short_version__, PATCH_VERSION)
REQUIRED_PYTHON_VER = (3, 6, 0)


PROJECT_NAME = 'SteamLink'
PROJECT_PACKAGE_NAME = 'steamlink'
PROJECT_LICENSE = 'MIT License'
PROJECT_AUTHOR = 'SteamLink Authors'
PROJECT_COPYRIGHT = ' 2017-2018, {}'.format(PROJECT_AUTHOR)
PROJECT_URL = 'https://steamlink.net/'
PROJECT_EMAIL = 'info+git@steamlink.net'
PROJECT_DESCRIPTION = ('Open-source IoT network framework '
                       'running on Python 3.')
PROJECT_LONG_DESCRIPTION = ('SteamLink is an open-source '
							'IoT networking framework that manages'
							'and runs LoRa radio nodes. ')
PROJECT_CLASSIFIERS = [
    'Intended Audience :: Developers',
    'License :: OSI Approved :: MIT License',
    'Operating System :: OS Independent',
    'Programming Language :: Python :: 3.6',
    'Topic :: Software Development :: Libraries :: Python Modules',
    'Topic :: Internet :: WWW/HTTP :: Dynamic Content'
]

PROJECT_GITHUB_USERNAME = 'steamlink'
PROJECT_GITHUB_REPOSITORY = 'steamlink'

PYPI_URL = 'https://pypi.python.org/pypi/{}'.format(PROJECT_PACKAGE_NAME)
GITHUB_PATH = '{}/{}'.format(PROJECT_GITHUB_USERNAME,
                             PROJECT_GITHUB_REPOSITORY)
GITHUB_URL = 'https://github.com/{}'.format(GITHUB_PATH)

PLATFORM_FORMAT = '{}.{}'

###

SL_RESPONSE_WAIT_SEC = 10
MAX_NODE_LOG_LEN = 1000     # maximum packets stored in per node log
DEFAULT_CONFIG_FILE = "steamlink.yaml"
