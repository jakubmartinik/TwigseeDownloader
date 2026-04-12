#!/usr/bin/env bashio

CONFIG_PATH=/data/options.json

EMAIL=$(bashio::config 'email')
PASSWORD=$(bashio::config 'password')
MAX_AGE_DAYS=$(bashio::config 'max_age_days')
SCHEDULE_HOURS=$(bashio::config 'schedule_hours')
TEACHER=$(bashio::config 'teacher')
RCLONE_ENABLED=$(bashio::config 'rclone_enabled')
RCLONE_CLIENT_ID=$(bashio::config 'rclone_google_client_id')
RCLONE_CLIENT_SECRET=$(bashio::config 'rclone_google_client_secret')
RCLONE_TOKEN=$(bashio::config 'rclone_google_token')
QUIET_START=$(bashio::config 'quiet_hours_start')
QUIET_END=$(bashio::config 'quiet_hours_end')

export TWIGSEE_EMAIL="${EMAIL}"
export TWIGSEE_PASSWORD="${PASSWORD}"

SCHEDULE_SECONDS=$((SCHEDULE_HOURS * 3600))
DOWNLOAD_DIR="/media/twigsee"
RCLONE_CONF="/data/rclone.conf"
# Generate rclone config for Google Photos
if [ "${RCLONE_ENABLED}" = "true" ]; then
    bashio::log.info "Writing rclone config..."
    cat > "${RCLONE_CONF}" <<EOF
[googlephotos]
type = google photos
client_id = ${RCLONE_CLIENT_ID}
client_secret = ${RCLONE_CLIENT_SECRET}
token = ${RCLONE_TOKEN}
read_only = false
EOF
    RCLONE_READY=0
else
    RCLONE_READY=1
fi

# Returns 0 (true) if current hour is within quiet hours, 1 (false) otherwise
is_quiet_hour() {
    local current_hour
    current_hour=$(date +%H | sed 's/^0//')  # strip leading zero
    if [ "${QUIET_START}" -le "${QUIET_END}" ]; then
        # e.g. 9-17: quiet during daytime
        [ "${current_hour}" -ge "${QUIET_START}" ] && [ "${current_hour}" -lt "${QUIET_END}" ]
    else
        # e.g. 22-7: quiet overnight (wraps midnight)
        [ "${current_hour}" -ge "${QUIET_START}" ] || [ "${current_hour}" -lt "${QUIET_END}" ]
    fi
}

bashio::log.info "Twigsee Downloader started"
bashio::log.info "Downloading every ${SCHEDULE_HOURS} hours, max age ${MAX_AGE_DAYS} days"
bashio::log.info "Quiet hours: ${QUIET_START}:00 - ${QUIET_END}:00"

while true; do
    if is_quiet_hour; then
        bashio::log.info "Quiet hours active (${QUIET_START}:00-${QUIET_END}:00), skipping run. Next check in 30 min."
        sleep 1800
        continue
    fi
    bashio::log.info "Starting download run..."

    ARGS="--headless --download-dir ${DOWNLOAD_DIR} --max-age-days ${MAX_AGE_DAYS}"
    if [ -n "${TEACHER}" ]; then
        ARGS="${ARGS} --teacher ${TEACHER}"
    fi
    if [ "${RCLONE_ENABLED}" = "true" ] && [ ${RCLONE_READY} -eq 0 ]; then
        ARGS="${ARGS} --rclone-conf ${RCLONE_CONF} --rclone-remote googlephotos:album"
    fi

    python3 /app/twigsee_download.py ${ARGS} 2>&1
    DL_EXIT=$?

    if [ ${DL_EXIT} -ne 0 ]; then
        bashio::log.error "Download run failed"
    fi

    bashio::log.info "Next run in ${SCHEDULE_HOURS} hours"
    sleep ${SCHEDULE_SECONDS}
done
