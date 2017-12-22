
import sys
import argparse
import logging
import yaml

#
# Utility
#

def phex(p, l=0):
	if type(p) == type(""):
		pp = p.encode()
	else:
		pp = p
	hh = ""
	cc = ""
	head = " " * l
	lines = []
	i = 0
	for c in pp:
		hh += "%02x " % c
		if c >= ord(' ') and pp[i] <= ord('~'):
			cc += chr(c)
		else:
			cc += '.'
		if i % 16 == 15:
			lines.append("%s%s %s" % (head, hh, cc))
			hh = ""
			cc = ""
		i += 1
	if cc != "":
		lines.append("%s%-48s %s" % (head, hh, cc))
	return lines


def getargs():
	parser = argparse.ArgumentParser()
	parser.add_argument("-c", "--config", 
							help="config file default steamlink.yaml",
							default="steamlink.yaml")
	parser.add_argument("-d", "--daemon", 
							help="excute as a daemon",
							default=False, action='store_true')
	parser.add_argument("-L", "--loglevel", 
							help="set loglevel, default is info", 
							default="info")
	parser.add_argument("-l", "--logfile", 
							help="file to log to", 
							default=None)
	parser.add_argument("-C", "--createconfig", 
							help="create a skeleton config file",
							default=False, action='store_true')
	parser.add_argument("-p", "--pid-file", 
							help="path to pid file when running as daemon", 
							default=None)
	parser.add_argument("-T", "--testdata", 
							help="generate test data",
							default=False, action='store_true')
	parser.add_argument("-v", "--verbose", 
							help="print some info",
							default=False, action='store_true')
	parser.add_argument("-X", "--debug", 
							help="increase debug level",
							default=0, action="count")
	return parser.parse_args()


def loadconfig(conf_fname):
	try:
		conf_f = "".join(open(conf_fname, "r").readlines())
		return yaml.load(conf_f)
	except Exception as e:
		print("error: config load: %s" % e)
		sys.exit(1)

def createconfig(conf_fname):
	if os.path.exists(conf_fname):
		print("error: config file '%s' exists, will NOT overwrite with sample!!" % conf_fname)
		sys.exit(1)
	sample_conf = LIB_DIR + '/steamlink.yaml.sample'
	conf_f = "".join(open(sample_conf, "r").readlines())
	open(conf_fname,"w").write(conf_f)
	print("note: config sample copied to %s" % (conf_fname))
	sys.exit(0)


# borrowed from homeassistant
def daemonize() -> None:
	"""Move current process to daemon process."""
	# Create first fork
	pid = os.fork()
	if pid > 0:
		sys.exit(0)

	# Decouple fork
	os.setsid()

	# Create second fork
	pid = os.fork()
	if pid > 0:
		sys.exit(0)

	# redirect standard file descriptors to devnull
	infd = open(os.devnull, 'r')
	outfd = open(os.devnull, 'a+')
	sys.stdout.flush()
	sys.stderr.flush()
	os.dup2(infd.fileno(), sys.stdin.fileno())
	os.dup2(outfd.fileno(), sys.stdout.fileno())
	os.dup2(outfd.fileno(), sys.stderr.fileno())


def check_pid(pid_file: str) -> None:
	"""Check that HA is not already running."""
	# Check pid file
	try:
		pid = int(open(pid_file, 'r').readline())
	except IOError:
		# PID File does not exist
		return

	# If we just restarted, we just found our own pidfile.
	if pid == os.getpid():
		return

	try:
		os.kill(pid, 0)
	except OSError:
		# PID does not exist
		return
	print('Fatal Error: HomeAssistant is already running.')
	sys.exit(1)


def write_pid(pid_file: str) -> None:
	"""Create a PID File."""
	pid = os.getpid()
	try:
		open(pid_file, 'w').write(str(pid))
	except IOError:
		print('Fatal Error: Unable to write pid file {}'.format(pid_file))
		sys.exit(1)


def closefds_osx(min_fd: int, max_fd: int) -> None:
	"""Make sure file descriptors get closed when we restart.

	We cannot call close on guarded fds, and we cannot easily test which fds
	are guarded. But we can set the close-on-exec flag on everything we want to
	get rid of.
	"""
	from fcntl import fcntl, F_GETFD, F_SETFD, FD_CLOEXEC

	for _fd in range(min_fd, max_fd):
		try:
			val = fcntl(_fd, F_GETFD)
			if not val & FD_CLOEXEC:
				fcntl(_fd, F_SETFD, val | FD_CLOEXEC)
		except IOError:
			pass



