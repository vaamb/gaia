GAIA_DIR=${1}
SERVICE_FILE=${2}

# Create systemd service file
cat > "${SERVICE_FILE}" << EOF
[Unit]
Description=Gaia Service
After=network.target

[Service]
Environment=GAIA_DIR="${GAIA_DIR}"
Type=simple
User=${USER}
WorkingDirectory=${GAIA_DIR}
Restart=always
RestartSec=10
ExecStart=${GAIA_DIR}/scripts/start.sh
ExecStop=${GAIA_DIR}/scripts/stop.sh
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=gaia

[Install]
WantedBy=multi-user.target
EOF
