#!/bin/sh

# Wrapper script to keep running steamlink in the background
# sends notifications email s

MAILTO="andreas@steamlink.net"
LOG="${HOME}/log/steamlink-run.log"
PIDFILE="${HOME}/.steamlink/steamlink.pid"

ARGS="-l /home/steamlink/log/steamlink.log"
PROG="/home/steamlink/sl/bin/steamlink"

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

	if [ ${FAILCOUNT} > 3 ]; then
		echo "$(date)  steamlink exit code $rc, exiting! " >>${LOG}
		dd if=${LOG} skip=${LSIZE} bs=1 2>/dev/null | mail -s "SteamLink FAILED" ${MAILTO}
		break
	elif [ "${DT}" -gt 240 -o ${rc} -ne 0 ]; then
		echo "$(date)  steamlink exit code $rc, restarting" >>${LOG}
		dd if=${LOG} skip=${LSIZE} bs=1 2>/dev/null | mail -s "SteamLink restart (${rc}" ${MAILTO}
		LASTTS=${NOW}
		LSIZE=$(stat -f "%z" $LOG)
	fi
	sleep 1
done
rm  ${PIDFILE}
echo "$(date) ending steamlink" >> ${LOG}

