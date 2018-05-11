#!/bin/sh

# Wrapper script to keep running steamlink in the background
# sends notifications email s

MAILTO="andreas@steamlink.net"

LOG="${HOME}/log/steamlink-run.log"
PIDFILE="${HOME}/.steamlink/steamlink.pid"

ARGS="-l ${HOME}/log/steamlink.log"
PROG="${HOME}/sl/bin/steamlink"

LASTTS=0
touch ${LOG}
LSIZE=$(stat -f "%z" $LOG)
FAILCOUNT=0
while true; do
	$PROG $ARGS >>$LOG 2>&1 &
	SPID=$!
	echo "$(date)  steamlink started, running as ${SPID}" >>$LOG
	echo ${SPID} > ${PIDFILE}
	wait
	rc=$?
	NOW=$(date +%s)
	DT=$(( ${NOW} - ${LASTTS}))
	if [ ${rc} != 0 ]; then
		FAILCOUNT=$(( ${FAILCOUNT}  + 1 ))
	else
		FAILCOUNT=0
	fi

	if [ ${FAILCOUNT} -gt 3 ]; then
		echo "$(date)  steamlink exit code $rc, exiting! " >>${LOG}
		if [ "${MAILTO}" != "" ]; then
			dd if=${LOG} skip=${LSIZE} bs=1 2>/dev/null | mail -s "SteamLink FAILED" ${MAILTO}
		else
			echo "steamlink failed, exit code $rc, NOT restarting! "
		fi
		break
	elif [ "${DT}" -gt 240 -o ${rc} -ne 0 ]; then
		echo "$(date)  steamlink exit code $rc, restarting" >>${LOG}
		if [ "${MAILTO}" != "" ]; then
			dd if=${LOG} skip=${LSIZE} bs=1 2>/dev/null | mail -s "SteamLink restart (${rc}" ${MAILTO}
		else
			echo "steamlink exit code $rc, restarting"
		fi
		LASTTS=${NOW}
		LSIZE=$(stat -f "%z" $LOG)
	fi
	sleep 1
done
rm  ${PIDFILE}
echo "$(date) ending steamlink" >> ${LOG}

