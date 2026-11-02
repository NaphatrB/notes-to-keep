#!/bin/sh
# Adaptive drip: estimate how many notes the account's quota has refilled
# since the last success, ask for that many, and adjust a cap on failures.
#
# launchd calls this at login and every 2 minutes; it exits immediately until
# the next estimated attempt time, so it is cheap when idle.
#
# State line: <date> <runs> <next_epoch> <last_success_epoch> <batch_cap>
set -eu

MAX=${KEEP_RUNS_PER_DAY:-200}
DIR="$HOME/keep-migration"
STATE="$DIR/.drip-state"
LEDGER="$DIR/.keep-import-state.json"
TODAY=$(date +%Y-%m-%d)
NOW=$(date +%s)

DATE=""; COUNT=0; NEXT=0; LAST_OK=0; CAP=200
if [ -f "$STATE" ]; then
    read -r DATE COUNT NEXT LAST_OK CAP < "$STATE" || true
fi
for v in COUNT NEXT LAST_OK CAP; do
    eval "val=\$$v"
    case "$val" in ''|*[!0-9]*) eval "$v=0" ;; esac
done
if [ "$CAP" -eq 0 ]; then CAP=200; fi
if [ "$LAST_OK" -eq 0 ]; then LAST_OK=$((NOW - 300)); fi
if [ "$DATE" != "$TODAY" ]; then COUNT=0; fi

if [ "$NOW" -lt "$NEXT" ]; then
    exit 0
fi
if [ "$COUNT" -ge "$MAX" ]; then
    echo "$(date '+%F %T') guard: daily limit reached ($COUNT/$MAX)"
    exit 0
fi

# ~1 note per 3 seconds observed; ask for what should have accumulated.
IDLE=$((NOW - LAST_OK))
if [ "$IDLE" -lt 0 ]; then IDLE=0; fi
BATCH=$((IDLE / 3))
if [ "$BATCH" -lt 50 ]; then BATCH=50; fi
if [ "$BATCH" -gt "$CAP" ]; then BATCH=$CAP; fi
if [ "$BATCH" -gt 400 ]; then BATCH=400; fi

BEFORE=$(grep -c ':' "$LEDGER" 2>/dev/null || true)
case "$BEFORE" in ''|*[!0-9]*) BEFORE=0 ;; esac

COUNT=$((COUNT + 1))
echo "$(date '+%F %T') guard: run $COUNT/$MAX, batch $BATCH (idle ${IDLE}s, cap $CAP)"

set +e
"$DIR/run-import.sh" --batch-size "$BATCH" --sync-timeout 120
set -e

AFTER=$(grep -c ':' "$LEDGER" 2>/dev/null || true)
case "$AFTER" in ''|*[!0-9]*) AFTER=0 ;; esac
DELTA=$((AFTER - BEFORE))
NOW2=$(date +%s)

if [ "$DELTA" -gt 0 ]; then
    CAP=$((CAP + 50))
    if [ "$CAP" -gt 400 ]; then CAP=400; fi
    LAST_OK=$NOW2
    WAIT=$((BATCH * 3))
    if [ "$WAIT" -lt 180 ]; then WAIT=180; fi
    if [ "$WAIT" -gt 1800 ]; then WAIT=1800; fi
    NEXT=$((NOW2 + WAIT))
    echo "$(date '+%F %T') guard: imported $DELTA, next in ${WAIT}s, cap $CAP"
else
    CAP=$((CAP / 2))
    if [ "$CAP" -lt 50 ]; then CAP=50; fi
    NEXT=$((NOW2 + 900))
    echo "$(date '+%F %T') guard: imported 0, backing off 15 min, cap $CAP"
fi

printf '%s %s %s %s %s\n' "$TODAY" "$COUNT" "$NEXT" "$LAST_OK" "$CAP" > "$STATE"
exit 0
